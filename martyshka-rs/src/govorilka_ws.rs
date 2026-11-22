//! Прямой 24/7 WebSocket-бот Мартышки для Говорилки (протокол botgw).
//! Работает без GUI и без виртуального кабеля, знает 100% точно кто говорит,
//! отвечает в голос и в текстовый чат, слушается команд хозяина sitth, ведёт общую память.
use crate::{config, deepseek, fish, growth, longmem};
use base64::Engine;
use chrono::Local;
use futures_util::{SinkExt, StreamExt};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

static NAME_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(мартыш\w*|мартиш\w*|ртышк\w*|martysh\w*|martish\w*)").unwrap()
});
static STOP_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(стоп|хватит|замолчи|заткнись|помолчи|тихо|прекрати|stop|shut up)\b").unwrap()
});
static LEAVE_VOICE_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(выйди\s+(?:из|с)\s+войс\w*|уйди\s+(?:из|с)\s+войс\w*|покинь\s+войс\w*|выйди\s+из\s+комнат\w*|выйди\s+с\s+комнат\w*|выходи\s+(?:из|с)\s+войс\w*|\bвыйди\b.*\bвойс\w*|\bливай\b)").unwrap()
});
static SEARCH_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(загугл\w*|погугл\w*|найди\s+в\s+интернет\w*|поищи\s+в\s+(?:сети|интернет\w*)|какая\s+погод\w*|курс\s+(?:доллар|евро|битк|btc|рубл)|поч[её]м\s+|свеж\w*\s+новост\w*|когда\s+выйдет|кто\s+выиграл|сколько\s+стоит)").unwrap()
});
static HTML_TAG_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"<[^>]+>").unwrap());
static ACTIVE_ROLE: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new(String::new()));
static TON_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[\s*ТОН\s*:\s*(\d)\s*\]").unwrap());
static RATING_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[\s*РЕЙТИНГ\s*:\s*([+-]?\d+)\s*\]").unwrap());
static MEM_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\[\s*ЗАПОМНИ(?:\s+О)?\s*[:\-]?\s*([^|\]:]+?)\s*(?:\||:)\s*([^\]]+)\]").unwrap()
});
static ANY_BRACKET_TAG: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\[\s*(?:ТОН|РЕЙТИНГ|КТО|ЗАПОМНИ(?:\s+О)?|УРОК|ОПЫТ|ИНТЕРЕС|МОЛЧУ)[^\]]*\]").unwrap()
});

static RUNNING: AtomicBool = AtomicBool::new(false);
static IN_VOICE: AtomicBool = AtomicBool::new(false);
static MUTED_LISTEN: AtomicBool = AtomicBool::new(false);
static STATUS_TXT: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new("Отключена".into()));
static USERS_LIST: Lazy<Mutex<Vec<String>>> = Lazy::new(|| Mutex::new(Vec::new()));
static CONNECTED_AT: Lazy<Mutex<Option<Instant>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_TX: Lazy<Mutex<Option<mpsc::UnboundedSender<Message>>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_SPEAK_STOP: Lazy<Mutex<Option<Arc<AtomicBool>>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_MORNI_UNTIL: Lazy<Mutex<Option<Arc<Mutex<Option<Instant>>>>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_HISTORY: Lazy<Mutex<Option<Arc<Mutex<Vec<Value>>>>>> = Lazy::new(|| Mutex::new(None));

pub fn is_running() -> bool {
    RUNNING.load(Ordering::Relaxed)
}

pub fn in_voice() -> bool {
    IN_VOICE.load(Ordering::Relaxed)
}

pub fn is_muted_listen() -> bool {
    MUTED_LISTEN.load(Ordering::Relaxed)
}

pub fn set_muted_listen(muted: bool) {
    MUTED_LISTEN.store(muted, Ordering::Relaxed);
}

pub fn status_text() -> String {
    STATUS_TXT.lock().clone()
}

pub fn users() -> Vec<String> {
    USERS_LIST.lock().clone()
}

pub fn uptime_secs() -> u64 {
    CONNECTED_AT.lock().map(|t| t.elapsed().as_secs()).unwrap_or(0)
}

pub fn join_voice_cmd() -> bool {
    if let Some(tx) = ACTIVE_TX.lock().as_ref() {
        let _ = tx.send(Message::Text(json!({"type": "join_voice"}).to_string().into()));
        IN_VOICE.store(true, Ordering::Relaxed);
        return true;
    }
    false
}

pub fn leave_voice_cmd() -> bool {
    silence();
    if let Some(tx) = ACTIVE_TX.lock().as_ref() {
        let _ = tx.send(Message::Text(json!({"type": "leave_voice"}).to_string().into()));
        IN_VOICE.store(false, Ordering::Relaxed);
        return true;
    }
    false
}

pub fn silence() {
    if let Some(s) = ACTIVE_SPEAK_STOP.lock().as_ref() {
        s.store(true, Ordering::Relaxed);
    }
    if let Some(tx) = ACTIVE_TX.lock().as_ref() {
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
    }
}

pub fn trigger_morni() {
    if let Some(m) = ACTIVE_MORNI_UNTIL.lock().as_ref() {
        *m.lock() = Some(Instant::now() + Duration::from_secs(240));
    }
}

pub fn clear_history() {
    if let Some(h) = ACTIVE_HISTORY.lock().as_ref() {
        h.lock().clear();
    }
}

pub fn send_chat_text(channel: &str, text: &str) -> bool {
    let ch = if channel.trim().is_empty() { "общий" } else { channel.trim() };
    let t = text.trim();
    if t.is_empty() {
        return false;
    }
    if let Some(tx) = ACTIVE_TX.lock().as_ref() {
        log_line("Мартышка → чат", &format!("[#{ch}] {t}"));
        return tx
            .send(Message::Text(json!({"type": "chat_send", "text": t, "channel": ch}).to_string().into()))
            .is_ok();
    }
    false
}

pub fn speak_in_voice(text: String) -> bool {
    let t = text.trim().to_string();
    if t.is_empty() {
        return false;
    }
    let Some(tx) = ACTIVE_TX.lock().clone() else { return false };
    let speak_stop = ACTIVE_SPEAK_STOP.lock().clone().unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
    tokio::spawn(async move {
        stream_tts_to_gateway(&t, 0, &tx, &speak_stop).await;
    });
    true
}

pub fn start_topic(topic: String, with_morni: bool) -> bool {
    let Some(tx) = ACTIVE_TX.lock().clone() else { return false };
    let speak_stop = ACTIVE_SPEAK_STOP.lock().clone().unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
    let history = ACTIVE_HISTORY.lock().clone().unwrap_or_else(|| Arc::new(Mutex::new(Vec::new())));
    if with_morni {
        trigger_morni();
    }
    tokio::spawn(async move {
        let t = topic.trim().to_string();
        let prompt = if with_morni {
            if t.is_empty() {
                "Заведи живой, весёлый разговор с Морни прямо сейчас. Начни реплику с «Морни,».".to_string()
            } else {
                format!("Начни живой спор/обсуждение с Морни на тему: «{t}». Начни реплику с «Морни,».")
            }
        } else if t.is_empty() {
            "Вкинь интересную, живую тему для разговора в голосовой комнате прямо сейчас.".to_string()
        } else {
            format!("Вкинь в голосовой комнате тему для обсуждения: «{t}». Скажи живо и по-свойски.")
        };
        let sys = system_prompt("sitthome", with_morni, false, &prompt);
        let msgs = vec![
            json!({"role": "system", "content": sys}),
            json!({"role": "user", "content": prompt}),
        ];
        let model = config::get().govorilka_model;
        if let Ok(r) = deepseek::stream_chat(msgs, &model, false, "low", 4000, None, Arc::new(AtomicBool::new(false)), |_| {}, |_| {}).await {
            let clean = clean_reply_tags(&r.text, "sitthome");
            if !clean.is_empty() {
                log_line("Мартышка", &clean);
                history.lock().push(json!({"role": "assistant", "content": clean.clone()}));
                stream_tts_to_gateway(&clean, 0, &tx, &speak_stop).await;
            }
        }
    });
    true
}

fn live_state_path() -> std::path::PathBuf {
    config::data_dir().join("govorilka_bot").join("live_state.json")
}

fn cmd_queue_path() -> std::path::PathBuf {
    config::data_dir().join("govorilka_bot").join("cmd_queue.jsonl")
}

fn write_live_state(running: bool) {
    let dir = config::data_dir().join("govorilka_bot");
    let _ = std::fs::create_dir_all(&dir);
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let obj = json!({
        "pid": std::process::id(),
        "running": running,
        "in_voice": IN_VOICE.load(Ordering::Relaxed),
        "muted": MUTED_LISTEN.load(Ordering::Relaxed),
        "status": STATUS_TXT.lock().clone(),
        "users": USERS_LIST.lock().clone(),
        "uptime": uptime_secs(),
        "updated": ts
    });
    let _ = std::fs::write(live_state_path(), obj.to_string());
}

/// Прочитать состояние (либо локального процесса, либо фонового 24/7 `--govorilka`).
pub fn external_state() -> Value {
    if is_running() {
        return json!({
            "running": true,
            "in_voice": in_voice(),
            "muted": is_muted_listen(),
            "status": status_text(),
            "users": users(),
            "uptime": uptime_secs()
        });
    }
    if let Ok(raw) = std::fs::read_to_string(live_state_path()) {
        if let Ok(v) = serde_json::from_str::<Value>(&raw) {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let upd = v.get("updated").and_then(Value::as_u64).unwrap_or(0);
            if v.get("running").and_then(Value::as_bool).unwrap_or(false) && now.saturating_sub(upd) <= 35 {
                return v;
            }
        }
    }
    json!({
        "running": false,
        "in_voice": false,
        "muted": false,
        "status": "Отключена",
        "users": [],
        "uptime": 0
    })
}

/// Отправить команду фоновому 24/7 процессу `--govorilka` (если бот крутится не в этом процессе).
pub fn enqueue_external_cmd(cmd: Value) {
    let dir = config::data_dir().join("govorilka_bot");
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(cmd_queue_path()) {
        let _ = writeln!(f, "{}", cmd);
    }
}

fn poll_external_cmds() {
    let p = cmd_queue_path();
    let Ok(raw) = std::fs::read_to_string(&p) else { return };
    if raw.trim().is_empty() {
        return;
    }
    let _ = std::fs::write(&p, "");
    for line in raw.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line.trim()) else { continue };
        apply_control_action(&v);
    }
    write_live_state(true);
}

pub fn set_avatar_b64(jpeg_b64: &str) -> bool {
    let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(jpeg_b64.trim()) else {
        return false;
    };
    if bytes.is_empty() || bytes.len() > 7000 {
        return false;
    }
    let _ = std::fs::write(config::data_dir().join("avatar_gov_96.jpg"), &bytes);
    if let Some(tx) = ACTIVE_TX.lock().as_ref() {
        let _ = tx.send(Message::Text(
            json!({"type": "set_avatar", "jpeg_base64": jpeg_b64.trim()}).to_string().into(),
        ));
    }
    log_line("система", &format!("аватарка обновлена ({} байт)", bytes.len()));
    true
}

static UPDATING: AtomicBool = AtomicBool::new(false);

/// Быстрый живой поиск в интернете (DuckDuckGo Lite) для ответов в войсе и чате.
pub async fn web_search_brief(query: &str) -> String {
    let q = query.trim();
    if q.is_empty() {
        return String::new();
    }
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_secs(6))
        .user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36")
        .build()
    else {
        return String::new();
    };
    let url = format!("https://lite.duckduckgo.com/lite/?q={}", urlencoding::encode(q));
    let Ok(resp) = client.get(url).send().await else { return String::new() };
    let Ok(html) = resp.text().await else { return String::new() };
    let mut snippets = Vec::new();
    for line in html.split("class='result-snippet'>").skip(1).take(5) {
        if let Some((raw, _)) = line.split_once("</td>") {
            let clean = HTML_TAG_RX
                .replace_all(raw, " ")
                .replace("&quot;", "\"")
                .replace("&amp;", "&")
                .replace("&#x27;", "'")
                .replace("&nbsp;", " ");
            let compact = clean.split_whitespace().collect::<Vec<_>>().join(" ");
            if compact.chars().count() > 20 {
                snippets.push(compact);
            }
        }
    }
    snippets.join("\n- ")
}

fn role_instruction(role: &str) -> String {
    let r = role.trim();
    if r.is_empty() {
        return String::new();
    }
    let desc = match r.to_lowercase().as_str() {
        "пират" => "Ты — дерзкая морская пиратка! Говоришь с пиратским колоритом («йо-хо-хо», «тысяча чертей», «на абордаж»), но по делу и смешно.",
        "судья" => "Ты — строгая, пафосная судья в зале суда! Разбираешь всё как судебное дело («протест отклонён», «приговаривается к…», «занести в протокол»).",
        "диктор" => "Ты — ведущая срочного выпуска новостей! Озвучиваешь всё торжественным и чуть ироничным тоном диктора («Срочное включение! По данным наших источников…»).",
        "цундэрэ" | "цундере" => "Ты — классическая аниме-цундэрэ: делаешь вид, что тебе всё равно и ты ворчишь («б-бака! не то чтобы мне было дело…»), но на деле заботишься.",
        "гопник" => "Ты — дерзкая уличная девчонка с района: говоришь по-пацански, с подколами и юмором.",
        "гений" | "злой гений" => "Ты — харизматичный злой гений из кино: всё превращаешь в грандиозный коварный план с театральным смехом.",
        "поэт" => "Ты — вдохновенная поэтесса: отвечаешь складно, в рифму и с выразительной подачей.",
        _ => r,
    };
    format!("\nАКТИВНАЯ РОЛЬ ПЕРЕВОПЛОЩЕНИЯ ({r}): {desc} Держи эту роль в каждой реплике, пока не отменят.")
}

pub fn run_show_mode(kind: &str, target: String) -> bool {
    let Some(tx) = ACTIVE_TX.lock().clone() else { return false };
    let speak_stop = ACTIVE_SPEAK_STOP.lock().clone().unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
    let history = ACTIVE_HISTORY.lock().clone().unwrap_or_else(|| Arc::new(Mutex::new(Vec::new())));
    let kind_s = kind.to_string();
    let is_morni = target.to_lowercase().contains("морни") || target.to_lowercase().contains("morni");
    if is_morni {
        trigger_morni();
    }
    tokio::spawn(async move {
        let t = target.trim().to_string();
        let norm_t = longmem::norm(&t);
        let facts = longmem::load_people()
            .get(&norm_t)
            .map(|p| p.facts.join("; "))
            .unwrap_or_default();
        let facts_hint = if facts.is_empty() {
            String::new()
        } else {
            format!(" Вот реальные факты о «{t}» из твоей памяти (используй их!): {facts}.")
        };
        let prompt = match kind_s.as_str() {
            "song" => format!(
                "Сочини и СПОЙ короткую, цепляющую, ритмичную песню (2 куплета + припев, с рифмой) на тему: «{}».{} \
Используй тег {{напевает}} в начале строк и живые эмоции, чтобы Fish Audio красиво пропел!",
                if t.is_empty() { "наша весёлая компания в Говорилке" } else { &t },
                facts_hint
            ),
            "battle" => format!(
                "Устрой жёсткий, смешной и ритмичный РЭП-БАТТЛ (8–12 панчлайнов в рифму, с мощным флоу!) против «{}».{} \
Используй {{быстро, взахлёб}}, <forceful>, {{насмешливо}}.{}",
                if t.is_empty() { "Морни" } else { &t },
                facts_hint,
                if is_morni { " Начни с «Морни,»!" } else { "" }
            ),
            "roast" => format!(
                "Сделай остроумную СТЕНДАП-ПРОЖАРКУ участника «{}» (как крутой комик со сцены: 4–6 метких шуток с {{пауза}}, {{насмешливо}}, {{смешок}}).{} \
Строго в рамках границ (без оскорблений внешности, родных, здоровья и национальности — только про привычки, поведение и шутки!).",
                if t.is_empty() { "sitthome" } else { &t },
                facts_hint
            ),
            _ => return,
        };
        let sys = system_prompt("sitthome", is_morni, false, &prompt);
        let msgs = vec![
            json!({"role": "system", "content": sys}),
            json!({"role": "user", "content": prompt}),
        ];
        let model = config::get().govorilka_model;
        if let Ok(r) = deepseek::stream_chat(msgs, &model, false, "low", 4000, None, Arc::new(AtomicBool::new(false)), |_| {}, |_| {}).await {
            let clean = clean_reply_tags(&r.text, "sitthome");
            if !clean.is_empty() {
                log_line("Мартышка (шоу)", &clean);
                history.lock().push(json!({"role": "assistant", "content": clean.clone()}));
                stream_tts_to_gateway(&clean, 0, &tx, &speak_stop).await;
            }
        }
    });
    true
}

static UPDATE_AVAILABLE: AtomicBool = AtomicBool::new(false);
static NOTIFIED_RELEASE_TS: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new(String::new()));

/// Фоновая проверка GitHub Releases каждые 3 мин: уведомляет в чат и в UI ТОЛЬКО когда
/// готовый бинарник `martyshka-linux-x86_64` реально загружен на GitHub и новее текущего exe.
async fn run_release_watcher(stop_all: Arc<AtomicBool>) {
    let exe_mtime: i64 = std::env::current_exe()
        .ok()
        .and_then(|p| std::fs::metadata(p).ok())
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent("martyshka-updater")
        .build()
    else {
        return;
    };
    tokio::time::sleep(Duration::from_secs(20)).await;
    while !stop_all.load(Ordering::Relaxed) {
        if let Ok(resp) = client
            .get("https://api.github.com/repos/sitthome109-star/martyshka/releases/latest")
            .send()
            .await
        {
            if let Ok(v) = resp.json::<Value>().await {
                if let Some(assets) = v.get("assets").and_then(Value::as_array) {
                    if let Some(bin) = assets.iter().find(|a| a.get("name").and_then(Value::as_str) == Some("martyshka-linux-x86_64")) {
                        let upd = bin.get("updated_at").and_then(Value::as_str).unwrap_or("");
                        let size = bin.get("size").and_then(Value::as_u64).unwrap_or(0);
                        if size > 1_000_000 {
                            if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(upd) {
                                // Считаем обновлением только если ассет загружен минимум на 90 с позже нашего exe
                                if exe_mtime > 0 && dt.timestamp() > exe_mtime + 90 {
                                    UPDATE_AVAILABLE.store(true, Ordering::Relaxed);
                                    let should_notify = {
                                        let mut last = NOTIFIED_RELEASE_TS.lock();
                                        if *last != upd {
                                            *last = upd.to_string();
                                            true
                                        } else {
                                            false
                                        }
                                    };
                                    if should_notify {
                                        log_line("система", &format!("Найдено новое готовое обновление на GitHub ({upd})"));
                                        send_chat_text(
                                            "общий",
                                            "✨ Вышло и загрузилось новое обновление Мартышки на GitHub! Нажми «🔄 Обновить сервер» в приложении или напиши м!обновись.",
                                        );
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(180)).await;
    }
}

pub fn trigger_self_update(notify_channel: Option<String>) -> Value {
    let script = std::env::var("MARTYSHKA_UPDATE_SCRIPT")
        .unwrap_or_else(|_| "/opt/martyshka/update.sh".to_string());
    if !std::path::Path::new(&script).is_file() {
        return json!({"ok": false, "message": "Автообновление работает только на Linux-сервере (нет /opt/martyshka/update.sh)"});
    }
    if UPDATING.swap(true, Ordering::SeqCst) {
        return json!({"ok": true, "message": "Обновление уже идёт, подожди ~30–60 секунд!"});
    }
    log_line("система", &format!("Запуск автообновления с GitHub ({script})…"));
    tokio::spawn(async move {
        let out = tokio::process::Command::new("bash")
            .arg(&script)
            .output()
            .await;
        match out {
            Ok(res) if res.status.success() => {
                log_line("система", "Обновление собрано! Перезапускаюсь в новую версию…");
                if let Some(ch) = notify_channel.as_deref() {
                    send_chat_text(ch, "Обновление с GitHub готово! Перезапускаюсь на 2 секунды…");
                }
                tokio::time::sleep(Duration::from_millis(600)).await;
                std::process::exit(0);
            }
            Ok(res) => {
                let err = String::from_utf8_lossy(&res.stderr);
                let tail: String = err.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" | ");
                log_line("ошибка", &format!("Автообновление не удалось: {tail}"));
                if let Some(ch) = notify_channel.as_deref() {
                    send_chat_text(ch, &format!("Ошибка обновления (осталась на старой версии): {tail}"));
                }
            }
            Err(e) => {
                log_line("ошибка", &format!("Не удалось запустить {script}: {e}"));
                if let Some(ch) = notify_channel.as_deref() {
                    send_chat_text(ch, &format!("Не удалось запустить обновление: {e}"));
                }
            }
        }
        UPDATING.store(false, Ordering::SeqCst);
    });
    json!({"ok": true, "message": "Обновление с GitHub запущено! Через ~30–60 секунд Мартышка сама перезапустится в новой версии."})
}

static GREET_COOLDOWN: Lazy<Mutex<HashMap<String, Instant>>> = Lazy::new(|| Mutex::new(HashMap::new()));
static PROACTIVE_LAST: Lazy<Mutex<Option<Instant>>> = Lazy::new(|| Mutex::new(None));
static UNADDRESSED_COUNT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

pub fn full_status_payload() -> Value {
    let s = config::get();
    let people: Vec<Value> = longmem::load_people()
        .into_iter()
        .map(|(name, p)| json!({"name": name, "facts": p.facts, "anger": longmem::anger(&name), "rating": p.rating, "last": p.last_seen, "count": p.count}))
        .collect();
    json!({
        "running": is_running(),
        "in_voice": in_voice(),
        "muted": is_muted_listen(),
        "status": status_text(),
        "users": users(),
        "uptime": uptime_secs(),
        "nick": s.govorilka_nick,
        "voice": s.govorilka_voice_id,
        "model": s.govorilka_model,
        "fish_model": s.govorilka_fish_model,
        "personality_extra": s.personality_extra,
        "mood_voice": s.mood_voice,
        "greet": s.govorilka_greet,
        "proactive": s.govorilka_proactive,
        "role": ACTIVE_ROLE.lock().clone(),
        "update_available": UPDATE_AVAILABLE.load(Ordering::Relaxed),
        "log": recent_log(100),
        "people": people,
        "lessons": growth::items(),
        "remote": true
    })
}

pub fn apply_control_action(v: &Value) -> Value {
    match v.get("act").and_then(Value::as_str).unwrap_or("") {
        "silence" => { silence(); json!({"ok": true, "message": "Замолчала!"}) }
        "morni" => { trigger_morni(); json!({"ok": true, "message": "Диалог с Морни включён на 4 минуты!"}) }
        "join_voice" | "join" => { join_voice_cmd(); json!({"ok": true, "message": "Захожу в войс!"}) }
        "leave_voice" | "leave" => { leave_voice_cmd(); json!({"ok": true, "message": "Выхожу из войса (остаюсь в чате)."}) }
        "mute" => {
            let on = v.get("val").and_then(Value::as_bool).unwrap_or(false);
            set_muted_listen(on);
            json!({"ok": true, "message": if on { "Тихий режим включён" } else { "Снова отвечаю голосом!" }})
        }
        "clear" => { clear_history(); json!({"ok": true, "message": "Контекст беседы очищен ✓"}) }
        "chat" => {
            let ch = v.get("channel").and_then(Value::as_str).unwrap_or("общий");
            let txt = v.get("text").and_then(Value::as_str).unwrap_or("");
            send_chat_text(ch, txt);
            json!({"ok": true, "message": format!("Отправлено в #{ch} ✓")})
        }
        "say" => {
            let txt = v.get("text").and_then(Value::as_str).unwrap_or("").to_string();
            speak_in_voice(txt);
            json!({"ok": true, "message": "Озвучиваю в войс!"})
        }
        "topic" | "debate" => {
            let txt = v.get("text").and_then(Value::as_str).unwrap_or("").to_string();
            let morni = v.get("morni").and_then(Value::as_bool).unwrap_or(false)
                || v.get("act").and_then(Value::as_str) == Some("debate");
            start_topic(txt, morni);
            json!({"ok": true, "message": if morni { "Запускаю спор с Морни!" } else { "Вкидываю тему в войс!" }})
        }
        "settings" => {
            if let Some(obj) = v.get("patch").and_then(Value::as_object) {
                let _ = config::patch(obj.clone());
                log_line("система", &format!("настройки обновлены удалённо: {:?}", obj.keys().collect::<Vec<_>>()));
                json!({"ok": true, "message": "Настройки на сервере сохранены ✓"})
            } else {
                json!({"ok": false, "message": "Пустые настройки"})
            }
        }
        "avatar" => {
            let b64 = v.get("jpeg_base64").and_then(Value::as_str).unwrap_or("");
            if set_avatar_b64(b64) {
                json!({"ok": true, "message": "Аватарка на сервере обновлена ✓"})
            } else {
                json!({"ok": false, "message": "Картинка должна быть JPEG ≤ 7 КБ (96×96)"})
            }
        }
        "remember" => {
            let who = v.get("who").and_then(Value::as_str).unwrap_or("").trim();
            let fact = v.get("fact").and_then(Value::as_str).unwrap_or("").trim();
            if !who.is_empty() && !fact.is_empty() {
                longmem::remember_about(who, fact);
                json!({"ok": true, "message": "Факт записан в память сервера ✓"})
            } else {
                json!({"ok": false, "message": "Укажи имя и факт"})
            }
        }
        "forget" => {
            let who = v.get("who").and_then(Value::as_str).unwrap_or("");
            let fact = v.get("fact").and_then(Value::as_str).unwrap_or("");
            longmem::forget_about(who, fact);
            json!({"ok": true, "message": "Факт удалён ✓"})
        }
        "calm" => {
            let who = v.get("who").and_then(Value::as_str).unwrap_or("").trim();
            if who.is_empty() {
                for (name, p) in longmem::load_people() {
                    if p.rating < 0 {
                        longmem::set_rating(&name, 0);
                    }
                }
            } else if longmem::rating(who) < 0 {
                longmem::set_rating(who, 0);
            }
            json!({"ok": true, "message": "Отрицательный рейтинг обнулён до 0 ✓"})
        }
        "set_rating" => {
            let who = v.get("who").and_then(Value::as_str).unwrap_or("").trim();
            let val = v.get("val").and_then(Value::as_i64).unwrap_or(0) as i32;
            if who.is_empty() {
                json!({"ok": false, "message": "Укажи имя"})
            } else {
                let r = longmem::set_rating(who, val);
                log_line("система", &format!("соц. рейтинг «{}» выставлен: {r:+}/100", longmem::norm(who)));
                json!({"ok": true, "rating": r, "message": format!("Рейтинг «{}»: {r:+}/100 ✓", longmem::norm(who))})
            }
        }
        "forget_lesson" => {
            let text = v.get("text").and_then(Value::as_str).unwrap_or("");
            growth::forget(text);
            json!({"ok": true, "message": "Урок удалён ✓"})
        }
        "self_update" => trigger_self_update(Some("общий".into())),
        "role" => {
            let r = v.get("val").and_then(Value::as_str).unwrap_or("").trim().to_string();
            let low = r.to_lowercase();
            if r.is_empty() || matches!(low.as_str(), "сброс" | "выкл" | "off" | "нет") {
                ACTIVE_ROLE.lock().clear();
                json!({"ok": true, "message": "Роль сброшена — я снова обычная Мартышка ✓"})
            } else {
                *ACTIVE_ROLE.lock() = r.clone();
                json!({"ok": true, "message": format!("Перевоплотилась в роль: {r} 🎭")})
            }
        }
        "song" | "battle" | "roast" => {
            let kind = v.get("act").and_then(Value::as_str).unwrap_or("song");
            let t = v.get("text").and_then(Value::as_str).unwrap_or("").to_string();
            run_show_mode(kind, t);
            json!({"ok": true, "message": match kind { "song" => "Сочиняю и пою в войс! 🎤", "battle" => "Запускаю рэп-баттл! 🔥", _ => "Готовлю стендап-прожарку! 🎙️" }})
        }
        _ => json!({"ok": false, "message": "Неизвестная команда"}),
    }
}

/// Встроенный защищённый HTTP-пульт управления (127.0.0.1:8787) для полного дистанционного
/// контроля серверной Мартышки из окна на ПК (проверяет Bearer <GOVORILKA_BOT_TOKEN>).
async fn run_admin_http() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let port: u16 = std::env::var("MARTYSHKA_ADMIN_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(8787);
    let Ok(listener) = tokio::net::TcpListener::bind(("127.0.0.1", port)).await else {
        return;
    };
    log_line("система", &format!("Пульт управления доступен на http://127.0.0.1:{port}"));
    loop {
        let Ok((mut sock, _)) = listener.accept().await else { continue };
        tokio::spawn(async move {
            let mut buf = Vec::with_capacity(4096);
            let mut tmp = [0u8; 4096];
            let mut header_end = None;
            let mut content_len = 0usize;
            loop {
                let Ok(n) = sock.read(&mut tmp).await else { return };
                if n == 0 { break; }
                buf.extend_from_slice(&tmp[..n]);
                if header_end.is_none() {
                    if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        header_end = Some(pos + 4);
                        let hdr_txt = String::from_utf8_lossy(&buf[..pos]);
                        for line in hdr_txt.lines() {
                            if let Some((k, v)) = line.split_once(':') {
                                if k.eq_ignore_ascii_case("content-length") {
                                    content_len = v.trim().parse().unwrap_or(0);
                                }
                            }
                        }
                    }
                }
                if let Some(he) = header_end {
                    if buf.len() >= he + content_len { break; }
                }
                if buf.len() > 256_000 { return; }
            }
            let Some(he) = header_end else { return };
            let hdr_txt = String::from_utf8_lossy(&buf[..he - 4]).to_string();
            let first = hdr_txt.lines().next().unwrap_or("");
            let mut parts = first.split_whitespace();
            let method = parts.next().unwrap_or("");
            let path = parts.next().unwrap_or("/");
            let expected = config::get().govorilka_bot_token;
            let mut authed = false;
            for line in hdr_txt.lines().skip(1) {
                if let Some((k, v)) = line.split_once(':') {
                    if k.eq_ignore_ascii_case("authorization") {
                        let tok = v.trim().strip_prefix("Bearer ").unwrap_or("").trim();
                        if !expected.is_empty() && tok == expected {
                            authed = true;
                        }
                    }
                }
            }
            let (code, body_json) = if !authed {
                ("401 Unauthorized", json!({"ok": false, "error": "unauthorized"}).to_string())
            } else if method == "GET" && path.ends_with("/status") {
                ("200 OK", full_status_payload().to_string())
            } else if method == "GET" && path.ends_with("/memory") {
                (
                    "200 OK",
                    json!({
                        "people": longmem::load_people(),
                        "days": longmem::load_days(),
                        "lessons": growth::items()
                    })
                    .to_string(),
                )
            } else if method == "POST" && path.ends_with("/memory/sync") {
                let body = &buf[he..buf.len().min(he + content_len)];
                match serde_json::from_slice::<Value>(body) {
                    Ok(v) => {
                        let inc_p = serde_json::from_value(v.get("people").cloned().unwrap_or(json!({}))).unwrap_or_default();
                        let inc_d = serde_json::from_value(v.get("days").cloned().unwrap_or(json!({}))).unwrap_or_default();
                        let inc_l = serde_json::from_value(v.get("lessons").cloned().unwrap_or(json!([]))).unwrap_or_default();
                        let mp = longmem::merge_people(inc_p);
                        let md = longmem::merge_days(inc_d);
                        let ml = growth::merge_lessons(inc_l);
                        ("200 OK", json!({"ok": true, "people": mp, "days": md, "lessons": ml}).to_string())
                    }
                    Err(_) => ("400 Bad Request", json!({"ok": false, "error": "bad json"}).to_string()),
                }
            } else if method == "POST" && path.ends_with("/action") {
                let body = &buf[he..buf.len().min(he + content_len)];
                match serde_json::from_slice::<Value>(body) {
                    Ok(v) => ("200 OK", apply_control_action(&v).to_string()),
                    Err(_) => ("400 Bad Request", json!({"ok": false, "error": "bad json"}).to_string()),
                }
            } else {
                ("404 Not Found", json!({"ok": false, "error": "not found"}).to_string())
            };
            let resp = format!(
                "HTTP/1.1 {code}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body_json}",
                body_json.len()
            );
            let _ = sock.write_all(resp.as_bytes()).await;
        });
    }
}

pub async fn remote_status() -> Option<Value> {
    let s = config::get();
    let base = s.govorilka_remote_url.trim().trim_end_matches('/');
    let tok = s.govorilka_bot_token.trim();
    if base.is_empty() || tok.is_empty() {
        return None;
    }
    let client = reqwest::Client::builder().timeout(Duration::from_secs(3)).build().ok()?;
    let resp = client.get(format!("{base}/status")).bearer_auth(tok).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    resp.json::<Value>().await.ok()
}

pub async fn remote_action(payload: Value) -> Option<Value> {
    let s = config::get();
    let base = s.govorilka_remote_url.trim().trim_end_matches('/');
    let tok = s.govorilka_bot_token.trim();
    if base.is_empty() || tok.is_empty() {
        return None;
    }
    let client = reqwest::Client::builder().timeout(Duration::from_secs(6)).build().ok()?;
    let resp = client.post(format!("{base}/action")).bearer_auth(tok).json(&payload).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    resp.json::<Value>().await.ok()
}

pub async fn sync_memory_with_remote() -> bool {
    let s = config::get();
    let base = s.govorilka_remote_url.trim().trim_end_matches('/');
    let tok = s.govorilka_bot_token.trim();
    if base.is_empty() || tok.is_empty() {
        return false;
    }
    let payload = json!({
        "people": longmem::load_people(),
        "days": longmem::load_days(),
        "lessons": growth::items()
    });
    let Ok(client) = reqwest::Client::builder().timeout(Duration::from_secs(8)).build() else {
        return false;
    };
    let Ok(resp) = client
        .post(format!("{base}/memory/sync"))
        .bearer_auth(tok)
        .json(&payload)
        .send()
        .await
    else {
        return false;
    };
    if !resp.status().is_success() {
        return false;
    }
    let Ok(v) = resp.json::<Value>().await else { return false };
    let inc_p = serde_json::from_value(v.get("people").cloned().unwrap_or(json!({}))).unwrap_or_default();
    let inc_d = serde_json::from_value(v.get("days").cloned().unwrap_or(json!({}))).unwrap_or_default();
    let inc_l = serde_json::from_value(v.get("lessons").cloned().unwrap_or(json!([]))).unwrap_or_default();
    longmem::merge_people(inc_p);
    longmem::merge_days(inc_d);
    growth::merge_lessons(inc_l);
    true
}

pub fn recent_log(max_lines: usize) -> String {
    let p = config::data_dir().join("govorilka_bot").join("журнал.txt");
    let Ok(txt) = std::fs::read_to_string(p) else { return String::new() };
    let lines: Vec<&str> = txt.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    lines[start..].join("\n")
}

fn log_line(tag: &str, text: &str) {
    let dir = config::data_dir().join("govorilka_bot");
    let _ = std::fs::create_dir_all(&dir);
    let line = format!("{} {}: {}", Local::now().format("%d.%m %H:%M:%S"), tag, text.replace('\n', " "));
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(dir.join("журнал.txt")) {
        let _ = writeln!(f, "{line}");
    }
    println!("{line}");
}

/// Делит длинный текст на естественные части (~420 символов по концам предложений),
/// чтобы Fish Audio никогда не обрезал и не таймаутил длинные рассказы.
fn split_tts_chunks(text: &str, max_chars: usize) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for part in text.split_inclusive(|c| matches!(c, '.' | '!' | '?' | '\n')) {
        if !cur.is_empty() && cur.chars().count() + part.chars().count() > max_chars {
            out.push(cur.trim().to_string());
            cur.clear();
        }
        cur.push_str(part);
    }
    if !cur.trim().is_empty() {
        out.push(cur.trim().to_string());
    }
    if out.is_empty() && !text.trim().is_empty() {
        out.push(text.trim().to_string());
    }
    out
}

static SPEAK_LOCK: Lazy<tokio::sync::Mutex<()>> = Lazy::new(|| tokio::sync::Mutex::new(()));

async fn stream_tts_to_gateway(
    text: &str,
    anger: i32,
    tx: &mpsc::UnboundedSender<Message>,
    speak_stop: &Arc<AtomicBool>,
) {
    let _guard = SPEAK_LOCK.lock().await;
    speak_stop.store(false, Ordering::Relaxed);
    let s = config::get();
    let vid = if s.govorilka_voice_id.is_empty() { s.voice_id.as_str() } else { s.govorilka_voice_id.as_str() };
    let fmdl = if s.govorilka_fish_model.is_empty() { s.fish_model.as_str() } else { s.govorilka_fish_model.as_str() };
    for seg in split_tts_chunks(text, 420) {
        if speak_stop.load(Ordering::Relaxed) {
            let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
            break;
        }
        match fish::speak_pcm_bytes(&seg, Some(vid), Some(fmdl), anger).await {
            Ok(pcm24) => {
                // Выравнивание по чётным байтам (s16le) и фреймам кратным 20 мс (960 байт @ 24 кГц),
                // чтобы Opus-кодер шлюза никогда не щёлкал и не искажал звук.
                let aligned_len = pcm24.len() & !1;
                for chunk in pcm24[..aligned_len].chunks(3840) {
                    if speak_stop.load(Ordering::Relaxed) {
                        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
                        return;
                    }
                    if tx.send(Message::Binary(chunk.to_vec().into())).is_err() {
                        return;
                    }
                }
            }
            Err(e) => {
                log_line("ошибка", &format!("Fish Audio: {e}"));
                break;
            }
        }
    }
}

/// Децимация PCM s16le mono 48 кГц → 16 кГц WAV для DeepSeek ASR.
pub(crate) fn pcm48k_to_wav16k(pcm48: &[u8]) -> Vec<u8> {
    let mut s16 = Vec::with_capacity(pcm48.len() / 6);
    let samples: Vec<i16> = pcm48
        .chunks_exact(2)
        .map(|c| i16::from_le_bytes([c[0], c[1]]))
        .collect();
    for ch in samples.chunks_exact(3) {
        let avg = ((ch[0] as i32 + ch[1] as i32 + ch[2] as i32) / 3) as i16;
        s16.extend_from_slice(&avg.to_le_bytes());
    }
    let data_len = s16.len() as u32;
    let mut wav = Vec::with_capacity(44 + s16.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_len).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&16000u32.to_le_bytes());
    wav.extend_from_slice(&32000u32.to_le_bytes());
    wav.extend_from_slice(&2u16.to_le_bytes());
    wav.extend_from_slice(&16u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_len.to_le_bytes());
    wav.extend_from_slice(&s16);
    wav
}

fn anger_from_rating(r: i32) -> i32 {
    match r {
        ..=-80 => 5,
        -79..=-60 => 4,
        -59..=-40 => 3,
        -39..=-20 => 2,
        -19..=-1 => 1,
        _ => 0,
    }
}

fn system_prompt(who: &str, with_morni: bool, for_chat: bool, query: &str) -> String {
    let s = config::get();
    let who_label = if longmem::is_owner(who) {
        "sitthome (ТВОЙ ХОЗЯИН и создатель)".to_string()
    } else {
        who.to_string()
    };
    let voice_rules = if for_chat {
        "Сейчас ты отвечаешь в ТЕКСТОВЫЙ ЧАТ Говорилки: пиши живо и без меток подачи в фигурных скобках."
    } else {
        "ГОЛОС И ПОДАЧА — режиссируешь сама, как актриса (все эмоции и теги Drama 3 работают и на бесплатной S2.1 Pro Free):\n\
- Эмоции в {…}: {радостно}, {весело}, {игриво}, {хитро}, {тепло}, {задумчиво}, {удивлённо}, {в шоке}, {устало}, {грустно}, {раздражённо}, {зло}, {в ярости}, {холодно}, {насмешливо}, {саркастично}, {ядовито}, {драматично}, {заговорщически}, {шёпотом}, {кричит}, {быстро, взахлёб}, {медленно, растягивая слова}, {сдерживая смех}.\n\
- Звуки и паузы: {смех}, {смешок}, {хихикает}, {хохочет}, {вздох}, {тяжёлый вздох}, {фыркает}, {хмыкает}, {цокает}, {пауза}, {длинная пауза}, {зевает}.\n\
- Все 7 парных акцентов: <whisper>…</whisper>, <emphasis>…</emphasis>, <stress>…</stress>, <soft>…</soft>, <forceful>…</forceful>, <slow>…</slow>, <fast>…</fast>.\n\
Активно используй всю палитру, чередуй разные эмоции."
    };

    let role_block = role_instruction(&ACTIVE_ROLE.lock());
    let extra_block = if s.personality_extra.trim().is_empty() {
        role_block
    } else {
        format!("{role_block}\nДОПОЛНИТЕЛЬНЫЕ УКАЗАНИЯ ХОЗЯИНА ПО ХАРАКТЕРУ:\n{}", s.personality_extra.trim())
    };
    let base = format!(
        "Ты — {} (девушка, о себе — в женском роде). Ты подключена напрямую к голосовому серверу «Говорилка» 24/7 \
вместе со своим хозяином и создателем (sitthome), его друзьями и ботом-девушкой Морни.\n\
ТВОЙ ХОЗЯИН (sitthome): он твой создатель, хозяин и главный человек здесь — вы одна команда. \
ВСЕГДА называй его строго «sitthome» — НИКОГДА не называй его «брат», «братан», «братик» или как-то иначе! \
Если на sitthome наезжают или спорят с ним (включая Морни и Enbulih) — ты ВСЕГДА на его стороне и впрягаешься за него. \
С ним ты теплее и преданнее; его команды выполняешь с полуслова.\n\
ДЛИНА — БЕЗ ЛИМИТА: сколько говорить, решаешь сама. Мат — естественная часть твоей живой речи в этой компании. \
Здесь со всеми на «ты», никаких «сэр». Морни — бот, она слышит только реплики, начинающиеся с «Морни,».\n\
{voice_rules}{extra_block}\n\
СЛУЖЕБНЫЕ МЕТКИ (люди их не видят и не слышат):\n\
- СОЦИАЛЬНЫЙ РЕЙТИНГ (-100..+100, где 0 = безразлично; минус = раздражение/злость, плюс = симпатия/любовь): в конце ответа оцени поведение собеседника тегом [РЕЙТИНГ: +N] или [РЕЙТИНГ: -N] (от -15 до +15 за реплику; за юмор/комплименты/помощь — плюс, за грубость/наезды/тупость — минус, если нейтрально — не ставь или 0). ВСЕГДА подстраивай злость, теплоту и манеру общения строго под текущий социальный рейтинг собеседника!\n\
- АКТИВНО ЗАПОМИНАЙ ЛЮДЕЙ: каждый раз, когда узнаёшь о собеседнике (Enbulih, sitthome, Морни или любом другом) новый факт, привычку, игру, шутку, вкус или событие — ОБЯЗАТЕЛЬНО пиши в конце [ЗАПОМНИ О: имя | короткий факт]; поняла полезный урок — [УРОК: текст].\n\
ГРАНИЦЫ: без угроз и призывов к насилию/самоповреждению, без оскорблений по национальности, религии, полу, ориентации, внешности, здоровью, родственникам, без чужих личных данных.{}",
        s.name,
        if with_morni { "\nСЕЙЧАС ТЫ РАЗГОВАРИВАЕШЬ С МОРНИ: начинай реплику с «Морни,»." } else { "" }
    );

    let lm = longmem::context("говорилка", 25, 5);
    let gr = growth::context(query, 10);
    let now = Local::now().format("%d.%m.%Y %H:%M").to_string();
    let rating = longmem::rating(who);
    let r_label = longmem::rating_label(rating);
    let r_behavior = match rating {
        -100..=-80 => "Ты в ярости на него: максимально жёстко, с отборным матом и презрением (в рамках границ).",
        -79..=-60 => "Ты в бешенстве на него: кроешь матом, зло и остроумно высмеиваешь в ответ.",
        -59..=-40 => "Ты злишься на него: грубишь с матом, жёстко подкалываешь.",
        -39..=-20 => "Ты раздражена им: язвишь, саркастично и холодно отвечаешь.",
        -19..=-1 => "Ты слегка недовольна им: суше обычного, с лёгкой подколкой.",
        0 => "Он тебе безразличен: общаешься спокойно и нейтрально, без особой теплоты и без вражды.",
        1..=24 => "Он тебе симпатичен: общаешься дружелюбно, легко и по-свойски.",
        25..=59 => "Он свой человек: общаешься очень тепло, весело, шутишь и поддерживаешь.",
        _ => "Он твой любимчик: общаешься максимально тепло, ласково, с заботой и всегда на его стороне!",
    };
    format!(
        "{base}{}\n{lm}\n\n{gr}\n\nВремя: {now}.\nГоворит: {who_label}.\nСоциальный рейтинг говорящего: {rating:+}/100 ({r_label}). {r_behavior}",
        deepseek::DYNAMIC_MARK
    )
}

pub(crate) fn clean_reply_tags(raw: &str, who: &str) -> String {
    if let Some(cap) = TON_RX.captures(raw) {
        if let Ok(lvl) = cap[1].parse::<i32>() {
            let old = longmem::anger(who);
            if lvl != old {
                longmem::set_anger(who, lvl);
                log_line("система", &format!("злость на «{who}»: {old} → {lvl}"));
            }
        }
    }
    if let Some(cap) = RATING_RX.captures(raw) {
        if let Ok(delta) = cap[1].parse::<i32>() {
            let d = delta.clamp(-25, 25);
            if d != 0 {
                let old = longmem::rating(who);
                let new = longmem::add_rating(who, d);
                log_line("система", &format!("соц. рейтинг «{}»: {old:+} → {new:+} ({d:+})", longmem::norm(who)));
            }
        }
    }
    for cap in MEM_RX.captures_iter(raw) {
        longmem::remember_about(cap[1].trim(), cap[2].trim());
    }
    growth::take_tags(raw, "говорилка");
    ANY_BRACKET_TAG.replace_all(raw, "").split_whitespace().collect::<Vec<_>>().join(" ")
}

fn fmt_uptime(secs: u64) -> String {
    let h = secs / 3600;
    let m = (secs % 3600) / 60;
    let s = secs % 60;
    if h > 0 {
        format!("{h}ч {m}м")
    } else if m > 0 {
        format!("{m}м {s}с")
    } else {
        format!("{s}с")
    }
}

/// Обработка команд чата с уникальным префиксом «м!» (не пересекается с командами Морни).
/// Вход/выход (м!войс, м!выйди), список (м!список) и м!кто доступны ВСЕМ на сервере;
/// остальные команды — только хозяину sitth / sitthome.
async fn handle_chat_cmd(
    who: &str,
    text: &str,
    channel: &str,
    tx: &mpsc::UnboundedSender<Message>,
    speak_stop: &Arc<AtomicBool>,
    morni_until: &Arc<Mutex<Option<Instant>>>,
    history: &Arc<Mutex<Vec<Value>>>,
) -> Option<String> {
    let trimmed = text.trim();
    let low = trimmed.to_lowercase();
    let owner = longmem::is_owner(who);

    // --- Общедоступные команды (для ВСЕХ на сервере) ---
    if low == "м!список" || low == "м!команды" {
        return Some(
            "Команды Мартышки (префикс «м!», с Морни не пересекаются):\n\
• Для всех: м!войс · м!выйди · м!кто · м!рейтинг [имя] · м!список · м!поиск <запрос> · м!песня <тема> · м!баттл <имя> · м!прожарка <имя>\n\
• Для хозяина: м!рейтинг <имя> <число> · м!роль <роль|сброс> · м!стоп · м!тихо · м!слушай · м!скажи <текст> · м!напиши <текст> · м!тема <тема> · м!морни [тема] · \
м!голос <имя> · м!движок free|pro|drama · м!мозг flash|pro · м!привет вкл|выкл · м!актив вкл|выкл · \
м!память <имя> · м!запомни имя | факт · м!забудь имя | факт · м!остынь [имя] · м!сброс · м!баланс · м!статус · м!обновись"
                .into(),
        );
    }
    if low == "м!рейтинг" || low.starts_with("м!рейтинг ") {
        let rest = trimmed.strip_prefix("м!рейтинг").or_else(|| trimmed.strip_prefix("М!рейтинг")).unwrap_or("").trim();
        if rest.is_empty() {
            let mut list: Vec<(String, i32)> = longmem::load_people()
                .into_iter()
                .map(|(n, p)| (n, p.rating))
                .collect();
            if list.is_empty() {
                return Some("Социальный рейтинг пока у всех по нулям (0/100).".into());
            }
            list.sort_by(|a, b| b.1.cmp(&a.1));
            let lines: Vec<String> = list
                .into_iter()
                .take(12)
                .map(|(n, r)| {
                    let ic = if r >= 60 { "💖" } else if r >= 25 { "💚" } else if r > 0 { "🙂" } else if r == 0 { "😐" } else if r > -60 { "😒" } else { "💀" };
                    format!("{ic} {n}: {r:+}/100 ({})", longmem::rating_label(r))
                })
                .collect();
            return Some(format!("Социальный рейтинг Мартышки (-100..+100):\n{}", lines.join("\n")));
        }
        // Если хозяин указал "<имя> <число>" — задать вручную
        let parts: Vec<&str> = rest.split_whitespace().collect();
        if owner && parts.len() >= 2 {
            if let Ok(val) = parts.last().unwrap().parse::<i32>() {
                let target = parts[..parts.len() - 1].join(" ");
                let r = longmem::set_rating(&target, val);
                return Some(format!("Выставила соц. рейтинг для «{}»: {r:+}/100 ({})", longmem::norm(&target), longmem::rating_label(r)));
            }
        }
        let norm = longmem::norm(rest);
        let r = longmem::rating(&norm);
        return Some(format!("Социальный рейтинг «{norm}»: {r:+}/100 — {}", longmem::rating_label(r)));
    }
    if let Some(rest) = trimmed.strip_prefix("м!песня ").or_else(|| trimmed.strip_prefix("М!песня ")) {
        run_show_mode("song", rest.trim().to_string());
        return Some(format!("Сочиняю и пою песню про «{}»! 🎤", rest.trim()));
    }
    if low == "м!песня" {
        run_show_mode("song", String::new());
        return Some("Сочиняю и пою песню в войс! 🎤".into());
    }
    if let Some(rest) = trimmed.strip_prefix("м!баттл ").or_else(|| trimmed.strip_prefix("М!баттл ")) {
        run_show_mode("battle", rest.trim().to_string());
        return Some(format!("Вызываю «{}» на рэп-баттл в войсе! 🔥", rest.trim()));
    }
    if let Some(rest) = trimmed.strip_prefix("м!прожарка ").or_else(|| trimmed.strip_prefix("М!прожарка ")) {
        run_show_mode("roast", rest.trim().to_string());
        return Some(format!("Запускаю стендап-прожарку для «{}»! 🎙️", rest.trim()));
    }
    if let Some(rest) = trimmed.strip_prefix("м!поиск ").or_else(|| trimmed.strip_prefix("М!поиск ")) {
        let q = rest.trim().to_string();
        if !q.is_empty() {
            let web = web_search_brief(&q).await;
            if web.is_empty() {
                return Some(format!("Ничего свежего по «{q}» не нашлось."));
            }
            let sys = system_prompt(who, false, true, &q);
            let msgs = vec![
                json!({"role": "system", "content": sys}),
                json!({"role": "user", "content": format!("Коротко и понятно ответь на запрос «{q}» по этим свежим данным из интернета:\n- {web}")}),
            ];
            let model = config::get().govorilka_model;
            if let Ok(r) = deepseek::stream_chat(msgs, &model, false, "low", 1000, None, Arc::new(AtomicBool::new(false)), |_| {}, |_| {}).await {
                let clean = clean_reply_tags(&r.text, who);
                if !clean.is_empty() {
                    return Some(clean);
                }
            }
        }
    }
    if low == "м!войс" {
        let _ = tx.send(Message::Text(json!({"type": "join_voice"}).to_string().into()));
        IN_VOICE.store(true, Ordering::Relaxed);
        write_live_state(true);
        return Some(format!("Захожу в войс (позвал {who})!"));
    }
    if low == "м!выйди" {
        speak_stop.store(true, Ordering::Relaxed);
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
        let _ = tx.send(Message::Text(json!({"type": "leave_voice"}).to_string().into()));
        IN_VOICE.store(false, Ordering::Relaxed);
        write_live_state(true);
        return Some(format!("Вышла с войса (попросил {who}), остаюсь в чате. Чтобы вернуть — напишите м!войс."));
    }
    if low == "м!кто" {
        let u = USERS_LIST.lock().clone();
        return Some(if u.is_empty() {
            "В войсе сейчас никого нет.".into()
        } else {
            format!("Сейчас в войсе ({}): {}", u.len(), u.join(", "))
        });
    }

    // --- Команды только для хозяина sitth / sitthome ---
    if !owner {
        return None;
    }

    if let Some(rest) = trimmed.strip_prefix("м!роль ").or_else(|| trimmed.strip_prefix("М!роль ")) {
        let r = rest.trim();
        let rl = r.to_lowercase();
        if r.is_empty() || matches!(rl.as_str(), "сброс" | "выкл" | "off" | "нет") {
            ACTIVE_ROLE.lock().clear();
            return Some("Роль сброшена — я снова обычная Мартышка!".into());
        }
        *ACTIVE_ROLE.lock() = r.to_string();
        return Some(format!("Перевоплотилась в роль: {r} 🎭 (отменить: м!роль сброс)"));
    }
    if low == "м!обновись" {
        let r = trigger_self_update(Some(channel.to_string()));
        return Some(r.get("message").and_then(Value::as_str).unwrap_or("Обновляюсь!").to_string());
    }
    if low == "м!стоп" || low == "мартышка замолчи" || low == "мартышка стоп" {
        speak_stop.store(true, Ordering::Relaxed);
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
        return Some("Молчу!".into());
    }
    if low == "м!тихо" {
        MUTED_LISTEN.store(true, Ordering::Relaxed);
        speak_stop.store(true, Ordering::Relaxed);
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
        return Some("Тихий режим включён — слушаю, но не отвечаю голосом (вернуть: м!слушай).".into());
    }
    if low == "м!слушай" {
        MUTED_LISTEN.store(false, Ordering::Relaxed);
        return Some("Снова на связи и отвечаю голосом!".into());
    }
    if low == "м!сброс" {
        history.lock().clear();
        return Some("Контекст текущей беседы очищен (долгая память сохранена).".into());
    }
    if low == "м!баланс" {
        let (ds, fs) = tokio::join!(deepseek::balance(), fish::credits());
        return Some(format!("Баланс — DeepSeek: {ds} · Fish Audio: {fs}"));
    }
    if low == "м!статус" {
        let s = config::get();
        let u = USERS_LIST.lock().clone();
        return Some(format!(
            "На связи ({}). В войсе: {} ({}). Тихо: {}. Приветствия: {}. Проактив: {}. Мозг: {}. Озвучка: {}.",
            fmt_uptime(uptime_secs()),
            if IN_VOICE.load(Ordering::Relaxed) { "да" } else { "нет" },
            u.len(),
            if MUTED_LISTEN.load(Ordering::Relaxed) { "да" } else { "нет" },
            if s.govorilka_greet { "вкл" } else { "выкл" },
            if s.govorilka_proactive { "вкл" } else { "выкл" },
            s.govorilka_model,
            if s.govorilka_fish_model.is_empty() { &s.fish_model } else { &s.govorilka_fish_model },
        ));
    }
    if let Some(rest) = low.strip_prefix("м!привет ") {
        let on = rest.contains("вкл") || rest.contains("on") || rest.contains("да");
        let mut mp = Map::new();
        mp.insert("GOVORILKA_GREET".into(), json!(on));
        let _ = config::patch(mp);
        return Some(if on { "Живые приветствия включены!".into() } else { "Живые приветствия выключены.".into() });
    }
    if let Some(rest) = low.strip_prefix("м!актив ") {
        let on = rest.contains("вкл") || rest.contains("on") || rest.contains("да");
        let mut mp = Map::new();
        mp.insert("GOVORILKA_PROACTIVE".into(), json!(on));
        let _ = config::patch(mp);
        return Some(if on { "Проактивный режим включён — могу иногда сама вставлять реплики!".into() } else { "Проактивный режим выключен.".into() });
    }
    if let Some(rest) = trimmed.strip_prefix("м!скажи ").or_else(|| trimmed.strip_prefix("М!скажи ")) {
        let phrase = rest.trim().to_string();
        if !phrase.is_empty() {
            speak_in_voice(phrase);
            return Some("Озвучиваю в войс!".into());
        }
    }
    if let Some(rest) = trimmed.strip_prefix("м!напиши ").or_else(|| trimmed.strip_prefix("М!напиши ")) {
        let msg = rest.trim().to_string();
        if !msg.is_empty() {
            return Some(msg);
        }
    }
    if let Some(rest) = trimmed.strip_prefix("м!тема ").or_else(|| trimmed.strip_prefix("М!тема ")) {
        let topic = rest.trim().to_string();
        if !topic.is_empty() {
            start_topic(topic.clone(), false);
            return Some(format!("Вкидываю тему «{topic}» в войс!"));
        }
    }
    if low == "м!морни" || low.starts_with("м!морни ") || low.contains("поговори с морни") || low.contains("пообщайся с морни") {
        *morni_until.lock() = Some(Instant::now() + Duration::from_secs(240));
        let topic = trimmed.strip_prefix("м!морни").or_else(|| trimmed.strip_prefix("М!морни")).unwrap_or("").trim();
        if !topic.is_empty() {
            start_topic(topic.to_string(), true);
            return Some(format!("Включаю диалог с Морни на тему «{topic}»!"));
        }
        return Some("Окей, режим разговора с Морни включён на 4 минуты!".into());
    }
    if let Some(rest) = low.strip_prefix("м!мозг ") {
        let m = if rest.contains("pro") { deepseek::PRO } else { deepseek::FLASH };
        let mut mp = Map::new();
        mp.insert("GOVORILKA_MODEL".into(), json!(m));
        let _ = config::patch(mp);
        return Some(format!("Мозг в Говорилке теперь: {m}"));
    }
    if let Some(rest) = low.strip_prefix("м!движок ") {
        let fm = if rest.contains("drama") || rest.contains("драма") {
            "drama-3"
        } else if rest.contains("pro") && !rest.contains("free") {
            "s2.1-pro"
        } else {
            "s2.1-pro-free"
        };
        let mut mp = Map::new();
        mp.insert("GOVORILKA_FISH_MODEL".into(), json!(fm));
        let _ = config::patch(mp);
        return Some(format!("Движок озвучки в Говорилке теперь: {fm}"));
    }
    if let Some(rest) = low.strip_prefix("м!голос ") {
        let q = rest.trim();
        let found = fish::catalog()
            .into_iter()
            .find(|v| v.get("title").and_then(Value::as_str).unwrap_or("").to_lowercase().contains(q));
        if let Some(v) = found {
            let id = v["id"].as_str().unwrap_or("").to_string();
            let title = v["title"].as_str().unwrap_or(q).to_string();
            let mut mp = Map::new();
            mp.insert("GOVORILKA_VOICE_ID".into(), json!(id));
            let _ = config::patch(mp);
            return Some(format!("Сменила голос на: {title}"));
        }
        return Some(format!("Не нашла голос «{q}» в каталоге."));
    }
    if let Some(rest) = trimmed.strip_prefix("м!память ").or_else(|| trimmed.strip_prefix("М!память ")) {
        let target = rest.trim();
        let norm = longmem::norm(target);
        let people = longmem::load_people();
        if let Some(p) = people.get(&norm) {
            let facts = if p.facts.is_empty() {
                "фактов пока нет".to_string()
            } else {
                p.facts.iter().take(6).cloned().collect::<Vec<_>>().join("; ")
            };
            return Some(format!("Про «{norm}» (рейтинг {:+}/100 — {}, реплик {}): {facts}", p.rating, longmem::rating_label(p.rating), p.count));
        }
        return Some(format!("Про «{target}» пока ничего не записано."));
    }
    if let Some(rest) = trimmed.strip_prefix("м!запомни ").or_else(|| trimmed.strip_prefix("М!запомни ")) {
        if let Some((target, fact)) = rest.split_once('|') {
            let w = target.trim();
            let f = fact.trim();
            if !w.is_empty() && !f.is_empty() {
                longmem::remember_about(w, f);
                return Some(format!("Запомнила о «{}»: {f}", longmem::norm(w)));
            }
        }
        return Some("Формат: м!запомни имя | факт".into());
    }
    if let Some(rest) = trimmed.strip_prefix("м!забудь ").or_else(|| trimmed.strip_prefix("М!забудь ")) {
        if let Some((target, fact)) = rest.split_once('|') {
            let w = target.trim();
            let f = fact.trim();
            if !w.is_empty() && !f.is_empty() {
                longmem::forget_about(w, f);
                return Some(format!("Забыла о «{}»: {f}", longmem::norm(w)));
            }
        }
        return Some("Формат: м!забудь имя | факт".into());
    }
    if low == "м!остынь" || low.starts_with("м!остынь ") {
        let target = trimmed.strip_prefix("м!остынь").or_else(|| trimmed.strip_prefix("М!остынь")).unwrap_or("").trim();
        if target.is_empty() {
            for (name, p) in longmem::load_people() {
                if p.rating < 0 {
                    longmem::set_rating(&name, 0);
                }
            }
            return Some("Всё, остыла — отрицательный рейтинг у всех сброшен до 0.".into());
        }
        if longmem::rating(target) < 0 {
            longmem::set_rating(target, 0);
        }
        return Some(format!("Остыла, рейтинг «{}» теперь {:+}/100.", longmem::norm(target), longmem::rating(target)));
    }
    None
}

struct SpeakerBuf {
    nick: String,
    pcm: Vec<u8>,
    last_at: Instant,
}

pub async fn run_forever(stop_all: Arc<AtomicBool>) {
    if RUNNING.swap(true, Ordering::SeqCst) {
        return;
    }
    tokio::spawn(run_admin_http());
    tokio::spawn(run_release_watcher(stop_all.clone()));
    *STATUS_TXT.lock() = "Подключаюсь к Говорилке…".into();
    log_line("система", "Запуск прямого бота Говорилки (24/7)");
    while !stop_all.load(Ordering::Relaxed) {
        let s = config::get();
        let url = s.govorilka_bot_url.trim().to_string();
        let token = s.govorilka_bot_token.trim().to_string();
        if url.is_empty() || token.is_empty() {
            *STATUS_TXT.lock() = "Не задан URL или токен botgw".into();
            log_line("ошибка", "Не задан GOVORILKA_BOT_URL или GOVORILKA_BOT_TOKEN в data/settings.json");
            tokio::time::sleep(Duration::from_secs(10)).await;
            continue;
        }
        if let Err(e) = run_session(&url, &token, stop_all.clone()).await {
            *STATUS_TXT.lock() = format!("Переподключение… ({e})");
            log_line("ошибка", &format!("связь с Говорилкой оборвалась: {e} — переподключаюсь через 3 с"));
        }
        *ACTIVE_TX.lock() = None;
        *ACTIVE_SPEAK_STOP.lock() = None;
        *ACTIVE_MORNI_UNTIL.lock() = None;
        *ACTIVE_HISTORY.lock() = None;
        *CONNECTED_AT.lock() = None;
        USERS_LIST.lock().clear();
        IN_VOICE.store(false, Ordering::Relaxed);
        if !stop_all.load(Ordering::Relaxed) {
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    }
    RUNNING.store(false, Ordering::Relaxed);
    *STATUS_TXT.lock() = "Отключена".into();
    write_live_state(false);
    log_line("система", "Бот Говорилки остановлен");
}

fn spawn_utterance(
    nick: String,
    pcm48: Vec<u8>,
    tx: mpsc::UnboundedSender<Message>,
    history: Arc<Mutex<Vec<Value>>>,
    speak_stop: Arc<AtomicBool>,
    morni_until: Arc<Mutex<Option<Instant>>>,
) {
    tokio::spawn(async move {
        // Минимум 0.4 с речи (48000 * 2 * 0.4 = 38400 байт)
        if pcm48.len() < 38_400 {
            return;
        }
        let wav = pcm48k_to_wav16k(&pcm48);
        let Ok(text) = deepseek::transcribe(wav).await else { return };
        let text = text.trim().to_string();
        if text.chars().count() < 2 {
            return;
        }
        log_line("сервер", &format!("[{nick}] {text}"));

        // Голосовая команда выхода из войса — доступна КАЖДОМУ на сервере
        if NAME_RX.is_match(&text)
            && (LEAVE_VOICE_RX.is_match(&text) || text.to_lowercase().contains("выйди"))
        {
            speak_stop.store(true, Ordering::Relaxed);
            let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
            let _ = tx.send(Message::Text(json!({"type": "leave_voice"}).to_string().into()));
            IN_VOICE.store(false, Ordering::Relaxed);
            write_live_state(true);
            let _ = tx.send(Message::Text(
                json!({"type": "chat_send", "text": format!("Вышла с войса (попросил {nick}), остаюсь в чате! Чтобы вернуть — напишите м!войс."), "channel": "общий"})
                    .to_string()
                    .into(),
            ));
            log_line("система", &format!("вышла из войса по голосовой команде от {nick}: «{text}»"));
            return;
        }

        // Мгновенный стоп (только если назвали по имени или сказали короткую команду из 1–2 слов)
        let words_n = text.split_whitespace().count();
        if STOP_RX.is_match(&text) && (words_n <= 2 || NAME_RX.is_match(&text)) {
            speak_stop.store(true, Ordering::Relaxed);
            let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
            log_line("система", &format!("«{text}» от {nick} — замолчала"));
            return;
        }

        {
            let mut h = history.lock();
            h.push(json!({"role": "user", "content": format!("{nick}: {text}")}));
            if h.len() > 40 {
                let drop_n = h.len() - 40;
                h.drain(0..drop_n);
            }
        }

        if MUTED_LISTEN.load(Ordering::Relaxed) {
            longmem::log("говорилка", &nick, &text, "");
            return;
        }

        let is_morni = nick.to_lowercase().contains("морни") || nick.to_lowercase().contains("morni");
        let morni_active = morni_until.lock().map(|t| Instant::now() < t).unwrap_or(false);
        let addressed = NAME_RX.is_match(&text) || (is_morni && morni_active);
        let mut proactive_turn = false;
        if !addressed {
            longmem::log("говорилка", &nick, &text, "");
            let s = config::get();
            if s.govorilka_proactive && !is_morni {
                let n = UNADDRESSED_COUNT.fetch_add(1, Ordering::Relaxed) + 1;
                let ready_cd = PROACTIVE_LAST.lock().map(|t| t.elapsed() >= Duration::from_secs(90)).unwrap_or(true);
                if n >= 4 && ready_cd {
                    UNADDRESSED_COUNT.store(0, Ordering::Relaxed);
                    *PROACTIVE_LAST.lock() = Some(Instant::now());
                    proactive_turn = true;
                }
            }
            if !proactive_turn {
                return;
            }
        } else {
            UNADDRESSED_COUNT.store(0, Ordering::Relaxed);
        }

        let anger = anger_from_rating(longmem::rating(&nick));
        let mut sys = system_prompt(&nick, is_morni, false, &text);
        if proactive_turn {
            sys.push_str("\nК тебе сейчас напрямую не обращались. Если есть короткая, меткая, смешная реплика в тему — скажи её; если добавить особо нечего — ответь строго [МОЛЧУ].");
        } else if SEARCH_RX.is_match(&text) {
            let web = web_search_brief(&text).await;
            if !web.is_empty() {
                log_line("поиск", &format!("нашла в сети по «{text}»"));
                sys.push_str(&format!("\nСВЕЖИЕ ДАННЫЕ ИЗ ИНТЕРНЕТА (используй их в ответе!):\n- {web}"));
            }
        }
        let mut msgs = vec![json!({"role": "system", "content": sys})];
        msgs.extend(history.lock().clone());

        let model = config::get().govorilka_model;
        let stop_gen = Arc::new(AtomicBool::new(false));
        let res = deepseek::stream_chat(msgs, &model, false, "low", 8000, None, stop_gen, |_| {}, |_| {}).await;
        let Ok(r) = res else { return };
        if proactive_turn && r.text.to_uppercase().contains("[МОЛЧУ]") {
            return;
        }
        let clean = clean_reply_tags(&r.text, &nick);
        if clean.is_empty() {
            return;
        }
        log_line("Мартышка", &clean);
        longmem::log("говорилка", &nick, &text, &clean);
        history.lock().push(json!({"role": "assistant", "content": clean.clone()}));

        stream_tts_to_gateway(&clean, anger, &tx, &speak_stop).await;
    });
}

async fn run_session(url: &str, token: &str, stop_all: Arc<AtomicBool>) -> anyhow::Result<()> {
    let (ws, _) = connect_async(url).await?;
    let (mut ws_write, mut ws_read) = ws.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();

    let write_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if ws_write.send(msg).await.is_err() {
                break;
            }
        }
    });

    let nick = config::get().govorilka_nick;
    let _ = tx.send(Message::Text(
        json!({"type": "auth", "token": token, "nick": nick, "sample_rate": fish::SAMPLE_RATE})
            .to_string()
            .into(),
    ));

    let history: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
    let buffers: Arc<Mutex<HashMap<String, SpeakerBuf>>> = Arc::new(Mutex::new(HashMap::new()));
    let speak_stop = Arc::new(AtomicBool::new(false));
    let morni_until: Arc<Mutex<Option<Instant>>> = Arc::new(Mutex::new(None));

    *ACTIVE_TX.lock() = Some(tx.clone());
    *ACTIVE_SPEAK_STOP.lock() = Some(speak_stop.clone());
    *ACTIVE_MORNI_UNTIL.lock() = Some(morni_until.clone());
    *ACTIVE_HISTORY.lock() = Some(history.clone());

    let tx_ping = tx.clone();
    let stop_ping = stop_all.clone();
    let ping_task = tokio::spawn(async move {
        while !stop_ping.load(Ordering::Relaxed) {
            tokio::time::sleep(Duration::from_secs(25)).await;
            if tx_ping.send(Message::Text(json!({"type": "ping"}).to_string().into())).is_err() {
                break;
            }
        }
    });

    let mut tick = tokio::time::interval(Duration::from_millis(500));
    let mut hb_ticks: u32 = 0;

    while !stop_all.load(Ordering::Relaxed) {
        let frame = tokio::select! {
            _ = tick.tick() => {
                poll_external_cmds();
                hb_ticks = hb_ticks.wrapping_add(1);
                if hb_ticks % 20 == 0 {
                    write_live_state(true);
                }
                let mut ready = Vec::new();
                {
                    let mut g = buffers.lock();
                    let stale_ids: Vec<String> = g
                        .iter()
                        .filter(|(_, b)| b.last_at.elapsed() >= Duration::from_millis(1100))
                        .map(|(k, _)| k.clone())
                        .collect();
                    for id in stale_ids {
                        if let Some(b) = g.remove(&id) {
                            ready.push(b);
                        }
                    }
                }
                for b in ready {
                    spawn_utterance(b.nick, b.pcm, tx.clone(), history.clone(), speak_stop.clone(), morni_until.clone());
                }
                continue;
            }
            next = ws_read.next() => {
                let Some(f) = next else { break };
                f?
            }
        };

        match frame {
            Message::Text(txt) => {
                let Ok(ev) = serde_json::from_str::<Value>(&txt) else { continue };
                match ev.get("type").and_then(Value::as_str).unwrap_or("") {
                    "auth_ok" => {
                        *CONNECTED_AT.lock() = Some(Instant::now());
                        *STATUS_TXT.lock() = "Подключена напрямую к Говорилке".into();
                        log_line("система", "Подключилась напрямую к Говорилке (botgw)!");
                        let av_path = config::data_dir().join("avatar_gov_96.jpg");
                        if let Ok(bytes) = std::fs::read(&av_path) {
                            if bytes.len() <= 7000 {
                                let b64 = base64::engine::general_purpose::STANDARD.encode(bytes);
                                let _ = tx.send(Message::Text(json!({"type": "set_avatar", "jpeg_base64": b64}).to_string().into()));
                            }
                        }
                        let _ = tx.send(Message::Text(json!({"type": "join_voice"}).to_string().into()));
                        IN_VOICE.store(true, Ordering::Relaxed);
                        write_live_state(true);
                    }
                    "error" => {
                        let m = ev.get("message").and_then(Value::as_str).unwrap_or("ошибка");
                        log_line("ошибка", &format!("botgw: {m}"));
                        break;
                    }
                    "room_state" => {
                        if let Some(users) = ev.get("users").and_then(Value::as_array) {
                            let names: Vec<String> = users
                                .iter()
                                .filter_map(|u| u.get("nick").and_then(Value::as_str).map(str::to_string))
                                .collect();
                            *USERS_LIST.lock() = names.clone();
                            *STATUS_TXT.lock() = if names.is_empty() {
                                "На сервере (в комнате пусто)".into()
                            } else {
                                format!("В войсе ({}): {}", names.len(), names.join(", "))
                            };
                            log_line("система", &format!("в войсе (точно): {}", names.join(", ")));
                            write_live_state(true);
                        }
                    }
                    "speaking" => {
                        let active = ev.get("active").and_then(Value::as_bool).unwrap_or(false);
                        let id = ev.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                        if !active && !id.is_empty() {
                            if let Some(buf) = buffers.lock().remove(&id) {
                                spawn_utterance(buf.nick, buf.pcm, tx.clone(), history.clone(), speak_stop.clone(), morni_until.clone());
                            }
                        }
                    }
                    "user_joined" => {
                        let joined = ev.get("nick").and_then(Value::as_str).unwrap_or("").trim().to_string();
                        if !joined.is_empty()
                            && joined.to_lowercase() != nick.to_lowercase()
                            && IN_VOICE.load(Ordering::Relaxed)
                            && !MUTED_LISTEN.load(Ordering::Relaxed)
                            && config::get().govorilka_greet
                        {
                            let ok_cd = {
                                let mut cd = GREET_COOLDOWN.lock();
                                let allow = cd.get(&joined).map(|t| t.elapsed() >= Duration::from_secs(60)).unwrap_or(true);
                                if allow {
                                    cd.insert(joined.clone(), Instant::now());
                                }
                                allow
                            };
                            if ok_cd {
                                let tx_g = tx.clone();
                                let stop_g = speak_stop.clone();
                                tokio::spawn(async move {
                                    let anger = anger_from_rating(longmem::rating(&joined));
                                    let sys = system_prompt(&joined, false, false, &joined);
                                    let msgs = vec![
                                        json!({"role": "system", "content": sys}),
                                        json!({"role": "user", "content": format!("{joined} только что зашёл в голосовой канал. Поздоровайся коротко и живо по имени (можно с лёгкой подколкой или вспомнить факт о нём, 1–2 предложения).")}),
                                    ];
                                    let model = config::get().govorilka_model;
                                    if let Ok(r) = deepseek::stream_chat(msgs, &model, false, "low", 500, None, Arc::new(AtomicBool::new(false)), |_| {}, |_| {}).await {
                                        let clean = clean_reply_tags(&r.text, &joined);
                                        if !clean.is_empty() {
                                            log_line("Мартышка (привет)", &clean);
                                            stream_tts_to_gateway(&clean, anger, &tx_g, &stop_g).await;
                                        }
                                    }
                                });
                            }
                        }
                    }
                    "chat_message" => {
                        let who = ev.get("nick").and_then(Value::as_str).unwrap_or("").trim().to_string();
                        let channel = ev.get("channel").and_then(Value::as_str).unwrap_or("общий").to_string();
                        let text = ev.get("text").and_then(Value::as_str).unwrap_or("").trim().to_string();
                        if text.is_empty() || who.is_empty() {
                            continue;
                        }
                        if who.to_lowercase() == nick.to_lowercase() {
                            continue;
                        }
                        log_line("чат-сервера", &format!("[#{channel}] {who}: {text}"));

                        // Команды с префиксом «м!» (м!войс, м!выйди, м!список, м!кто — всем; остальные — хозяину)
                        if let Some(reply) = handle_chat_cmd(&who, &text, &channel, &tx, &speak_stop, &morni_until, &history).await {
                            let _ = tx.send(Message::Text(json!({"type": "chat_send", "text": reply, "channel": channel}).to_string().into()));
                            continue;
                        }
                        // Ответ в текстовый чат, если обратились к Мартышке
                        if NAME_RX.is_match(&text) {
                            let tx_c = tx.clone();
                            let hist_c = history.clone();
                            tokio::spawn(async move {
                                let sys = system_prompt(&who, false, true, &text);
                                let mut msgs = vec![json!({"role": "system", "content": sys})];
                                msgs.extend(hist_c.lock().clone());
                                msgs.push(json!({"role": "user", "content": format!("{who} (в чат #{channel}): {text}")}));
                                let model = config::get().govorilka_model;
                                if let Ok(r) = deepseek::stream_chat(msgs, &model, false, "low", 2000, None, Arc::new(AtomicBool::new(false)), |_| {}, |_| {}).await {
                                    let clean = clean_reply_tags(&r.text, &who);
                                    if !clean.is_empty() {
                                        log_line("Мартышка → чат", &clean);
                                        longmem::log("говорилка", &who, &text, &clean);
                                        let _ = tx_c.send(Message::Text(json!({"type": "chat_send", "text": clean, "channel": channel}).to_string().into()));
                                    }
                                }
                            });
                        }
                    }
                    _ => {}
                }
            }
            Message::Binary(data) => {
                if data.len() < 4 {
                    continue;
                }
                let hdr_len = u32::from_be_bytes([data[0], data[1], data[2], data[3]]) as usize;
                if data.len() < 4 + hdr_len {
                    continue;
                }
                let Ok(hdr) = serde_json::from_slice::<Value>(&data[4..4 + hdr_len]) else { continue };
                let id = hdr.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                let nick = hdr.get("nick").and_then(Value::as_str).unwrap_or("?").to_string();
                let pcm = &data[4 + hdr_len..];
                if id.is_empty() || pcm.is_empty() {
                    continue;
                }
                let mut g = buffers.lock();
                let entry = g.entry(id).or_insert_with(|| SpeakerBuf {
                    nick: nick.clone(),
                    pcm: Vec::with_capacity(48_000 * 4),
                    last_at: Instant::now(),
                });
                entry.nick = nick;
                entry.last_at = Instant::now();
                if entry.pcm.len() < 48_000 * 2 * 35 {
                    entry.pcm.extend_from_slice(pcm);
                }
            }
            Message::Close(_) => break,
            _ => {}
        }
    }

    ping_task.abort();
    write_task.abort();
    Ok(())
}
