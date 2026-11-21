//! Вся связь с DeepSeek API: стрим, мысли, инструменты, зрение (Flash), ASR, баланс и кэш-экономия.
use crate::config;
use anyhow::{anyhow, Result};
use base64::Engine;
use futures_util::StreamExt;
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

pub const FLASH: &str = "deepseek-flash";
pub const PRO: &str = "deepseek-v4-pro";
pub const DYNAMIC_MARK: &str = "\n\n=== Сейчас ===\n";

#[derive(Debug, Clone, Default, Serialize)]
pub struct Usage {
    pub requests: u64,
    pub r#in: u64,
    pub cached: u64,
    pub out: u64,
}

static CLIENT: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(300))
        .build()
        .expect("reqwest client")
});

static USAGE: Lazy<Mutex<Usage>> = Lazy::new(|| Mutex::new(Usage::default()));

pub fn usage() -> Usage {
    USAGE.lock().clone()
}

fn count_usage(u: &Value) {
    let mut g = USAGE.lock();
    g.requests += 1;
    g.r#in += u.get("prompt_tokens").and_then(Value::as_u64).unwrap_or(0);
    g.cached += u.get("prompt_cache_hit_tokens").and_then(Value::as_u64).unwrap_or(0);
    g.out += u.get("completion_tokens").and_then(Value::as_u64).unwrap_or(0);
}

pub fn ready() -> bool {
    !config::get().deepseek_key.trim().is_empty()
}

fn base_url() -> String {
    let u = config::get().deepseek_url;
    let s = if u.trim().is_empty() { "https://api.deepseek.com" } else { u.trim() };
    s.trim_end_matches('/').to_string()
}

fn api_key() -> Result<String> {
    let k = config::get().deepseek_key.trim().to_string();
    if k.is_empty() {
        return Err(anyhow!("Нет ключа DeepSeek. Вставь его в настройках (раздел «Модель»)."));
    }
    Ok(k)
}

fn explain(status: u16, body: &str) -> String {
    match status {
        401 => "Ключ DeepSeek не подходит. Проверь его в настройках.".into(),
        402 => "На счёте DeepSeek закончились деньги. Пополни баланс на platform.deepseek.com.".into(),
        429 => "DeepSeek просит подождать (слишком много запросов).".into(),
        500 | 502 | 503 | 504 => "DeepSeek сейчас перегружен. Попробуй через минуту.".into(),
        400 if body.contains("Content Exists Risk") => "DeepSeek отказался обрабатывать этот текст (фильтр).".into(),
        _ => format!("DeepSeek ответил ошибкой {status}: {}", body.chars().take(280).collect::<String>()),
    }
}

/// Переносит динамический хвост (после `=== Сейчас ===`) перед последним сообщением user
/// и подрезает старые tool-результаты для максимального попадания в префиксный кэш DeepSeek.
pub fn cache_friendly(messages: Vec<Value>) -> Vec<Value> {
    if !config::get().economy || messages.is_empty() {
        return messages;
    }
    let is_sys = messages[0].get("role").and_then(Value::as_str) == Some("system");
    let first_str = messages[0].get("content").and_then(Value::as_str).map(str::to_string);
    let Some(first) = first_str else { return messages };
    if !is_sys || !first.contains(DYNAMIC_MARK) {
        return messages;
    }
    let mut parts = first.splitn(2, DYNAMIC_MARK);
    let stat = parts.next().unwrap_or("");
    let dyn_part = parts.next().unwrap_or("");
    let mut out = messages;
    out[0]["content"] = Value::String(stat.to_string());
    let last_user = out
        .iter()
        .enumerate()
        .rev()
        .find(|(_, m)| m.get("role").and_then(Value::as_str) == Some("user"))
        .map(|(i, _)| i)
        .unwrap_or(out.len());
    out.insert(
        last_user,
        json!({"role": "system", "content": format!("=== Сейчас (свежее) ===\n{dyn_part}")}),
    );
    for m in out.iter_mut().take(last_user).skip(1) {
        if m.get("role").and_then(Value::as_str) == Some("tool") {
            if let Some(c) = m.get("content").and_then(Value::as_str) {
                if c.chars().count() > 2500 {
                    let cut: String = c.chars().take(2500).collect();
                    m["content"] = Value::String(format!("{cut}\n…(старый результат, обрезан для экономии)"));
                }
            }
        }
    }
    out
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub args: Value,
}

#[derive(Debug, Clone, Default)]
pub struct StreamResult {
    pub text: String,
    pub reasoning: String,
    pub tool_calls: Vec<ToolCall>,
    pub finish_reason: String,
}

pub async fn stream_chat<FText, FThink>(
    messages: Vec<Value>,
    model: &str,
    think: bool,
    effort: &str,
    max_tokens: u32,
    tools: Option<Vec<Value>>,
    stop_flag: Arc<AtomicBool>,
    mut on_text: FText,
    mut on_think: FThink,
) -> Result<StreamResult>
where
    FText: FnMut(&str),
    FThink: FnMut(&str),
{
    let key = api_key()?;
    let url = format!("{}/chat/completions", base_url());
    let msgs = cache_friendly(messages);

    let mut payload = json!({
        "model": model,
        "messages": msgs,
        "stream": true,
        "max_tokens": max_tokens,
        "stream_options": {"include_usage": true}
    });
    if think {
        payload["thinking"] = json!({"type": "enabled"});
        payload["reasoning_effort"] = json!(if effort.is_empty() { "high" } else { effort });
    } else {
        payload["thinking"] = json!({"type": "disabled"});
        payload["temperature"] = json!(0.6);
    }
    if let Some(t) = tools {
        if !t.is_empty() {
            payload["tools"] = Value::Array(t);
        }
    }

    let resp = CLIENT
        .post(&url)
        .bearer_auth(&key)
        .json(&payload)
        .send()
        .await
        .map_err(|e| anyhow!("Нет связи с DeepSeek: {e}"))?;

    let status = resp.status().as_u16();
    if status != 200 {
        let body = resp.text().await.unwrap_or_default();
        return Err(anyhow!(explain(status, &body)));
    }

    let mut res = StreamResult {
        finish_reason: "stop".into(),
        ..Default::default()
    };
    let mut raw_calls: BTreeMap<usize, (String, String, String)> = BTreeMap::new();
    let mut buf = String::new();
    let mut byte_stream = resp.bytes_stream();

    while let Some(chunk) = byte_stream.next().await {
        if stop_flag.load(Ordering::Relaxed) {
            res.finish_reason = "stopped".into();
            break;
        }
        let bytes = chunk.map_err(|e| anyhow!("Ошибка потока DeepSeek: {e}"))?;
        buf.push_str(&String::from_utf8_lossy(&bytes));
        while let Some(pos) = buf.find('\n') {
            let line = buf[..pos].trim().to_string();
            buf.drain(..=pos);
            if !line.starts_with("data:") {
                continue;
            }
            let data = line[5..].trim();
            if data == "[DONE]" {
                break;
            }
            let Ok(ev) = serde_json::from_str::<Value>(data) else { continue };
            if let Some(u) = ev.get("usage") {
                if !u.is_null() {
                    count_usage(u);
                }
            }
            if let Some(choices) = ev.get("choices").and_then(Value::as_array) {
                for ch in choices {
                    if let Some(d) = ch.get("delta") {
                        if let Some(rc) = d.get("reasoning_content").and_then(Value::as_str) {
                            res.reasoning.push_str(rc);
                            on_think(rc);
                        }
                        if let Some(c) = d.get("content").and_then(Value::as_str) {
                            res.text.push_str(c);
                            on_text(c);
                        }
                        if let Some(tcs) = d.get("tool_calls").and_then(Value::as_array) {
                            for tc in tcs {
                                let idx = tc.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
                                let slot = raw_calls.entry(idx).or_default();
                                if let Some(id) = tc.get("id").and_then(Value::as_str) {
                                    slot.0 = id.to_string();
                                }
                                if let Some(fn_obj) = tc.get("function") {
                                    if let Some(n) = fn_obj.get("name").and_then(Value::as_str) {
                                        slot.1.push_str(n);
                                    }
                                    if let Some(a) = fn_obj.get("arguments").and_then(Value::as_str) {
                                        slot.2.push_str(a);
                                    }
                                }
                            }
                        }
                    }
                    if let Some(fr) = ch.get("finish_reason").and_then(Value::as_str) {
                        res.finish_reason = fr.to_string();
                    }
                }
            }
        }
    }

    for (idx, (id, name, args_str)) in raw_calls {
        let args = if args_str.trim().is_empty() {
            json!({})
        } else {
            serde_json::from_str(&args_str).unwrap_or_else(|_| json!({"_raw": args_str}))
        };
        res.tool_calls.push(ToolCall {
            id: if id.is_empty() { format!("call_{idx}") } else { id },
            name,
            args,
        });
    }
    Ok(res)
}

#[allow(dead_code)]
pub async fn ask(prompt: &str, system: &str, model: &str, max_tokens: u32) -> Result<String> {
    let key = api_key()?;
    let mut msgs = Vec::new();
    if !system.is_empty() {
        msgs.push(json!({"role": "system", "content": system}));
    }
    msgs.push(json!({"role": "user", "content": prompt}));
    let payload = json!({
        "model": model,
        "messages": msgs,
        "stream": false,
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
        "max_tokens": max_tokens
    });
    let resp = CLIENT
        .post(format!("{}/chat/completions", base_url()))
        .bearer_auth(&key)
        .json(&payload)
        .send()
        .await?;
    let status = resp.status().as_u16();
    if status != 200 {
        let body = resp.text().await.unwrap_or_default();
        return Err(anyhow!(explain(status, &body)));
    }
    let v: Value = resp.json().await?;
    if let Some(u) = v.get("usage") {
        count_usage(u);
    }
    Ok(v["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string())
}

pub async fn look(image_path: &Path, question: &str) -> Result<String> {
    let key = api_key()?;
    let bytes = std::fs::read(image_path)?;
    let ext = image_path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("png")
        .to_lowercase();
    let mime = if ext == "jpg" || ext == "jpeg" { "image/jpeg" } else { "image/png" };
    let b64 = base64::engine::general_purpose::STANDARD.encode(bytes);
    let data_url = format!("data:{mime};base64,{b64}");
    let payload = json!({
        "model": FLASH,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}}
            ]
        }],
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "max_tokens": 900
    });
    let resp = CLIENT
        .post(format!("{}/chat/completions", base_url()))
        .bearer_auth(&key)
        .json(&payload)
        .send()
        .await?;
    let status = resp.status().as_u16();
    if status != 200 {
        let body = resp.text().await.unwrap_or_default();
        return Err(anyhow!(explain(status, &body)));
    }
    let v: Value = resp.json().await?;
    if let Some(u) = v.get("usage") {
        count_usage(u);
    }
    Ok(v["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string())
}

pub async fn transcribe(wav_bytes: Vec<u8>) -> Result<String> {
    let key = api_key()?;
    let part = reqwest::multipart::Part::bytes(wav_bytes)
        .file_name("speech.wav")
        .mime_str("audio/wav")?;
    let form = reqwest::multipart::Form::new().part("file", part);
    let resp = CLIENT
        .post(format!("{}/v1/tools/asr", base_url()))
        .bearer_auth(&key)
        .multipart(form)
        .send()
        .await?;
    let status = resp.status().as_u16();
    if status != 200 {
        let body = resp.text().await.unwrap_or_default();
        return Err(anyhow!(explain(status, &body)));
    }
    let text = resp.text().await.unwrap_or_default();
    if let Ok(v) = serde_json::from_str::<Value>(&text) {
        if let Some(t) = v.get("text").or_else(|| v.get("transcript")).and_then(Value::as_str) {
            return Ok(t.trim().to_string());
        }
    }
    Ok(text.trim().to_string())
}

pub async fn balance() -> String {
    let Ok(key) = api_key() else { return "нет ключа".into() };
    let Ok(resp) = CLIENT
        .get(format!("{}/user/balance", base_url()))
        .bearer_auth(&key)
        .timeout(Duration::from_secs(12))
        .send()
        .await
    else {
        return "не узнать".into();
    };
    if resp.status().as_u16() != 200 {
        return format!("ошибка {}", resp.status().as_u16());
    }
    let Ok(v) = resp.json::<Value>().await else { return "?".into() };
    let info = &v["balance_infos"][0];
    let total = info.get("total_balance").and_then(Value::as_str).unwrap_or("?");
    let cur = info.get("currency").and_then(Value::as_str).unwrap_or("");
    format!("{total} {cur}").trim().to_string()
}
