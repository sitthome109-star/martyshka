//! Голос в облаке — Fish Audio API + воспроизведение через cpal (WASAPI) со стабильными русскими ударениями.
use crate::config;
use anyhow::{anyhow, Result};
#[cfg(windows)]
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use once_cell::sync::Lazy;
use regex::Regex;
use reqwest::Client;
use serde_json::{json, Value};
#[cfg(windows)]
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(windows)]
use std::sync::Arc;
use std::time::Duration;

pub const API: &str = "https://api.fish.audio";
pub const SAMPLE_RATE: u32 = 24_000;
pub const FREE: &str = "s2.1-pro-free";
pub const DRAMA: &str = "drama-3";
pub const DEFAULT_VOICE: &str = "b871e425b6c0442baa5cf73f6c336bbe";

static CLIENT: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(180))
        .build()
        .expect("fish client")
});

static CUE_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"\{\s*([a-zA-Zа-яА-ЯёЁ][^{}]{0,90}?)\s*\}").unwrap());
static BRACKET_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"\[[^\]]*\]").unwrap());
static DRAMA_MARK_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[\s*(драма|drama)\s*\]\s*").unwrap());
static SING_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[[^\]]*(по[её]т|пою|напева|песн|мелоди|sing|hum)[^\]]*\]").unwrap());

pub fn key() -> String {
    config::get().fish_key.trim().to_string()
}

pub fn prepare_text(text: &str) -> String {
    let s = BRACKET_RX.replace_all(text, " ");
    let s = CUE_RX.replace_all(&s, "[$1]");
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn mood_pick(text: &str, voice_id: &str, model: &str, anger: i32) -> (String, String, String) {
    let s = config::get();
    let marked = DRAMA_MARK_RX.is_match(text);
    let clean = DRAMA_MARK_RX.replace_all(text, "").trim().to_string();
    if !s.mood_voice {
        return (clean, voice_id.to_string(), model.to_string());
    }
    let angry = anger >= s.mood_angry_from;
    let vid = if angry && !s.angry_voice_id.trim().is_empty() {
        s.angry_voice_id
    } else {
        voice_id.to_string()
    };
    let mdl = if marked || angry || SING_RX.is_match(&clean) {
        DRAMA.to_string()
    } else {
        model.to_string()
    };
    (clean, vid, mdl)
}

pub async fn speak_pcm_bytes(text: &str, voice_id: Option<&str>, model: Option<&str>, anger: i32) -> Result<Vec<u8>> {
    let k = key();
    if k.is_empty() {
        return Err(anyhow!("Нет ключа Fish Audio."));
    }
    let s = config::get();
    let vid_base = voice_id
        .filter(|v| !v.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| if s.voice_id.is_empty() { DEFAULT_VOICE.into() } else { s.voice_id.clone() });
    let mdl_base = model
        .filter(|m| !m.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| if s.fish_model.is_empty() { FREE.into() } else { s.fish_model.clone() });

    let (said, vid, mdl) = mood_pick(&prepare_text(text), &vid_base, &mdl_base, anger);
    if said.is_empty() {
        return Ok(Vec::new());
    }
    let temp = if mdl == DRAMA { 0.78 } else { 0.75 };
    let body = json!({
        "text": said,
        "reference_id": vid,
        "format": "pcm",
        "sample_rate": SAMPLE_RATE,
        "latency": "balanced",
        "normalize": true,
        "temperature": temp,
        "top_p": 0.80,
        "chunk_length": 200,
        "prosody": {"speed": 1.0, "volume": 0, "normalize_loudness": true}
    });

    let resp = CLIENT
        .post(format!("{API}/v1/tts"))
        .bearer_auth(&k)
        .header("model", &mdl)
        .json(&body)
        .send()
        .await?;
    let status = resp.status().as_u16();
    if status != 200 {
        let err = resp.text().await.unwrap_or_default();
        return Err(anyhow!("Fish Audio ошибка {status}: {}", err.chars().take(200).collect::<String>()));
    }
    Ok(resp.bytes().await?.to_vec())
}

#[cfg(windows)]
pub async fn speak_pcm(text: &str, voice_id: Option<&str>, model: Option<&str>, anger: i32) -> Result<Vec<f32>> {
    let bytes = speak_pcm_bytes(text, voice_id, model, anger).await?;
    let mut samples = Vec::with_capacity(bytes.len() / 2);
    for ch in bytes.chunks_exact(2) {
        let v = i16::from_le_bytes([ch[0], ch[1]]);
        samples.push(v as f32 / 32768.0);
    }
    Ok(samples)
}

/// Проиграть PCM 24 кГц через устройство вывода по умолчанию с поддержкой мгновенного стопа.
#[cfg(windows)]
pub fn play_pcm_blocking(samples: Vec<f32>, volume: f32, stop: Arc<AtomicBool>) -> Result<()> {
    if samples.is_empty() {
        return Ok(());
    }
    let host = cpal::default_host();
    let device = host.default_output_device().ok_or_else(|| anyhow!("Нет устройства вывода звука"))?;
    let cfg = device.default_output_config()?;
    let out_sr = cfg.sample_rate().0;
    let channels = cfg.channels() as usize;

    // Простой линейный ресемплинг 24 кГц → частота устройства
    let ratio = out_sr as f64 / SAMPLE_RATE as f64;
    let out_len = (samples.len() as f64 * ratio) as usize;
    let mut resampled = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let src = i as f64 / ratio;
        let idx = src.floor() as usize;
        let frac = (src - idx as f64) as f32;
        let a = samples.get(idx).copied().unwrap_or(0.0);
        let b = samples.get(idx + 1).copied().unwrap_or(a);
        resampled.push(((a + (b - a) * frac) * volume).clamp(-1.0, 1.0));
    }

    let pos = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let done = Arc::new(AtomicBool::new(false));
    let pos_c = pos.clone();
    let done_c = done.clone();
    let stop_c = stop.clone();

    let stream = device.build_output_stream(
        &cfg.into(),
        move |data: &mut [f32], _| {
            if stop_c.load(Ordering::Relaxed) {
                data.fill(0.0);
                done_c.store(true, Ordering::Relaxed);
                return;
            }
            for frame in data.chunks_mut(channels) {
                let i = pos_c.fetch_add(1, Ordering::Relaxed);
                let s = if i < resampled.len() { resampled[i] } else { done_c.store(true, Ordering::Relaxed); 0.0 };
                for ch in frame {
                    *ch = s;
                }
            }
        },
        |_| {},
        None,
    )?;
    stream.play()?;
    while !done.load(Ordering::Relaxed) && !stop.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(30));
    }
    Ok(())
}

pub async fn credits() -> String {
    let k = key();
    if k.is_empty() {
        return "нет ключа".into();
    }
    let Ok(resp) = CLIENT
        .get(format!("{API}/wallet/self/api-credit"))
        .bearer_auth(&k)
        .timeout(Duration::from_secs(10))
        .send()
        .await
    else {
        return "не узнать".into();
    };
    if resp.status().as_u16() != 200 {
        return format!("ошибка {}", resp.status().as_u16());
    }
    let Ok(v) = resp.json::<Value>().await else { return "?".into() };
    v.get("credit")
        .and_then(|c| c.as_str().map(str::to_string).or_else(|| c.as_f64().map(|f| format!("{f:.2}"))))
        .map(|s| format!("{s} $"))
        .unwrap_or_else(|| "?".into())
}

pub fn catalog() -> Vec<Value> {
    let p = config::data_dir().join("голоса_fish.json");
    if let Ok(raw) = std::fs::read_to_string(p) {
        if let Ok(arr) = serde_json::from_str::<Vec<Value>>(&raw) {
            return arr;
        }
    }
    vec![
        json!({"id": DEFAULT_VOICE, "title": "Артём", "lang": "ru", "gender": "male", "official": true, "popular": 0, "desc": "Официальный голос Fish"}),
        json!({"id": "911cdc7841634850ba72d88f5ed8c576", "title": "Кира", "lang": "ru", "gender": "female", "official": true, "popular": 0, "desc": "Официальный голос Fish"}),
        json!({"id": "bf75c2e80b0e441485eeec20330a75fa", "title": "Полина", "lang": "ru", "gender": "female", "official": true, "popular": 0, "desc": "Официальный голос Fish"}),
    ]
}

pub async fn search_voices(q: &str) -> Vec<Value> {
    let k = key();
    if k.is_empty() || q.trim().is_empty() {
        return Vec::new();
    }
    let Ok(resp) = CLIENT
        .get(format!("{API}/model"))
        .bearer_auth(&k)
        .query(&[("title", q.trim()), ("page_size", "40"), ("sort_by", "task_count")])
        .timeout(Duration::from_secs(15))
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(v) = resp.json::<Value>().await else { return Vec::new() };
    v.get("items")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|i| {
                    let id = i.get("_id")?.as_str()?;
                    let title = i.get("title").and_then(Value::as_str).unwrap_or(id);
                    let desc = i.get("description").and_then(Value::as_str).unwrap_or("");
                    let pop = i.get("task_count").and_then(Value::as_u64).unwrap_or(0);
                    Some(json!({"id": id, "title": title, "lang": "ru", "gender": "", "official": false, "popular": pop, "desc": desc}))
                })
                .collect()
        })
        .unwrap_or_default()
}
