//! Telegram-сервис на Rust: работает 24/7 (текст, фото, документы, режим агента).
use crate::{agent, config, deepseek};
use reqwest::Client;
use serde_json::{json, Value};
use std::fs;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Duration;

fn api_url(token: &str, method: &str) -> String {
    format!("https://api.telegram.org/bot{token}/{method}")
}

async fn send_msg(client: &Client, token: &str, chat_id: i64, text: &str) {
    let trimmed = text.trim();
    if trimmed.is_empty() || chat_id == 0 {
        return;
    }
    let chars: Vec<char> = trimmed.chars().collect();
    for chunk in chars.chunks(3900) {
        let part: String = chunk.iter().collect();
        let _ = client
            .post(api_url(token, "sendMessage"))
            .json(&json!({"chat_id": chat_id, "text": part}))
            .send()
            .await;
    }
}

pub async fn run_forever() {
    let client = Client::builder().timeout(Duration::from_secs(40)).build().unwrap();
    let mut offset: i64 = 0;
    loop {
        let s = config::get();
        let token = s.telegram_token.trim().to_string();
        let owner = s.telegram_owner_id;
        if token.is_empty() || owner == 0 {
            tokio::time::sleep(Duration::from_secs(10)).await;
            continue;
        }
        let resp = client
            .post(api_url(&token, "getUpdates"))
            .json(&json!({"offset": offset, "timeout": 25, "allowed_updates": ["message"]}))
            .send()
            .await;
        let Ok(r) = resp else {
            tokio::time::sleep(Duration::from_secs(5)).await;
            continue;
        };
        let Ok(v) = r.json::<Value>().await else { continue };
        let Some(arr) = v.get("result").and_then(Value::as_array) else { continue };
        for upd in arr {
            if let Some(uid) = upd.get("update_id").and_then(Value::as_i64) {
                offset = uid + 1;
            }
            let Some(msg) = upd.get("message") else { continue };
            let from = msg["from"]["id"].as_i64().unwrap_or(0);
            if from != owner {
                continue;
            }
            let text = msg.get("text").or_else(|| msg.get("caption")).and_then(Value::as_str).unwrap_or("").trim().to_string();
            let _ = client
                .post(api_url(&token, "sendChatAction"))
                .json(&json!({"chat_id": owner, "action": "typing"}))
                .send()
                .await;

            // Фото из Telegram → DeepSeek Flash
            if let Some(photos) = msg.get("photo").and_then(Value::as_array) {
                if let Some(fid) = photos.last().and_then(|p| p.get("file_id")).and_then(Value::as_str) {
                    if let Ok(fr) = client.post(api_url(&token, "getFile")).json(&json!({"file_id": fid})).send().await {
                        if let Ok(fv) = fr.json::<Value>().await {
                            if let Some(fpath) = fv["result"]["file_path"].as_str() {
                                let dl = format!("https://api.telegram.org/file/bot{token}/{fpath}");
                                if let Ok(bytes) = client.get(dl).send().await.and_then(|r| Ok(r)) {
                                    if let Ok(raw) = bytes.bytes().await {
                                        let dir = config::data_dir().join("telegram_files");
                                        let _ = fs::create_dir_all(&dir);
                                        let img = dir.join("tg_photo.jpg");
                                        let _ = fs::write(&img, raw);
                                        let q = if text.is_empty() { "Опиши фото и помоги, если это ошибка или код." } else { &text };
                                        let ans = deepseek::look(&img, q).await.unwrap_or_else(|e| format!("Ошибка: {e}"));
                                        send_msg(&client, &token, owner, &ans).await;
                                        continue;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            if text.is_empty() {
                continue;
            }
            let cid = agent::new_chat_id();
            let mut chat = agent::load_chat(&cid);
            let answer = Arc::new(parking_lot::Mutex::new(String::new()));
            let ans_c = answer.clone();
            agent::run_turn(&cid, &mut chat, &text, &[], Arc::new(AtomicBool::new(false)), move |ev| {
                if ev.get("type").and_then(Value::as_str) == Some("text") {
                    if let Some(t) = ev.get("text").and_then(Value::as_str) {
                        ans_c.lock().push_str(t);
                    }
                }
            })
            .await;
            let final_ans = answer.lock().clone();
            send_msg(&client, &token, owner, if final_ans.trim().is_empty() { "Готово." } else { &final_ans }).await;
        }
    }
}
