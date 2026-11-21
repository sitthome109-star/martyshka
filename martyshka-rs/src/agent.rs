//! Агент-программист Мартышки на Rust — совместим с data/studio/chats/*.json.
use crate::{config, deepseek, growth, longmem, tools};
use chrono::Local;
use futures_util::future::join_all;
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatFile {
    pub title: String,
    pub created: f64,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub pinned: bool,
    #[serde(default)]
    pub history: Vec<Value>,
    #[serde(default)]
    pub display: Vec<Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ChatMeta {
    pub id: String,
    pub title: String,
    pub updated: f64,
    pub pinned: bool,
}

static TASK_ROLLBACKS: Lazy<Mutex<HashMap<String, HashMap<String, Option<Vec<u8>>>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

static CODE_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(напрограмм|программ|код|скрипт|сайт|прилож|игр[ауы]|бот|парсер|функци|rust|python|html|css|js|баг|отлад|рефактор|ошибк|исправ|почин|добав|передела)").unwrap()
});

pub fn chats_dir() -> PathBuf {
    config::data_dir().join("studio").join("chats")
}

pub fn trash_dir() -> PathBuf {
    config::data_dir().join("studio").join("удалённые")
}

fn now_sec() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub fn new_chat_id() -> String {
    let short = &Uuid::new_v4().simple().to_string()[..4];
    format!("{}-{short}", Local::now().format("%Y%m%d-%H%M%S"))
}

pub fn load_chat(id: &str) -> ChatFile {
    let p = chats_dir().join(format!("{id}.json"));
    fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(ChatFile {
            title: "Новый чат".into(),
            created: now_sec(),
            project: String::new(),
            pinned: false,
            history: Vec::new(),
            display: Vec::new(),
        })
}

pub fn save_chat(id: &str, chat: &ChatFile) {
    let dir = chats_dir();
    let _ = fs::create_dir_all(&dir);
    let p = dir.join(format!("{id}.json"));
    if let Ok(s) = serde_json::to_string(chat) {
        let _ = fs::write(p, s);
    }
}

pub fn list_chats() -> Vec<ChatMeta> {
    let Ok(rd) = fs::read_dir(chats_dir()) else { return Vec::new() };
    let mut out = Vec::new();
    for e in rd.flatten() {
        let p = e.path();
        if p.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let id = p.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string();
        let updated = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        if let Ok(raw) = fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                out.push(ChatMeta {
                    id,
                    title: v.get("title").and_then(Value::as_str).unwrap_or("Чат").to_string(),
                    updated,
                    pinned: v.get("pinned").and_then(Value::as_bool).unwrap_or(false),
                });
            }
        }
    }
    out.sort_by(|a, b| b.pinned.cmp(&a.pinned).then_with(|| b.updated.total_cmp(&a.updated)));
    out
}

pub fn delete_chat(id: &str) {
    let src = chats_dir().join(format!("{id}.json"));
    if src.exists() {
        let _ = fs::create_dir_all(trash_dir());
        let _ = fs::rename(&src, trash_dir().join(format!("{id}.json")));
    }
}

pub fn pop_last_turn(chat: &mut ChatFile) -> String {
    let last_u = chat
        .history
        .iter()
        .enumerate()
        .rev()
        .find(|(_, m)| m.get("role").and_then(Value::as_str) == Some("user") && !m.get("hidden").and_then(Value::as_bool).unwrap_or(false))
        .map(|(i, _)| i);
    let last_du = chat
        .display
        .iter()
        .enumerate()
        .rev()
        .find(|(_, m)| m.get("type").and_then(Value::as_str) == Some("user"))
        .map(|(i, _)| i);
    let (Some(u), Some(du)) = (last_u, last_du) else { return String::new() };
    let text = chat.display[du].get("text").and_then(Value::as_str).unwrap_or("").to_string();
    chat.history.truncate(u);
    chat.display.truncate(du);
    text
}

pub fn rollback_task(task_id: &str) -> (bool, String) {
    let Some(snap) = TASK_ROLLBACKS.lock().remove(task_id) else {
        return (false, "Точка отката не найдена.".into());
    };
    let (mut restored, mut moved) = (0usize, 0usize);
    for (raw, old_bytes) in snap {
        let p = PathBuf::from(&raw);
        match old_bytes {
            None => {
                if p.exists() {
                    let _ = fs::create_dir_all(tools::undo_dir());
                    let nm = p.file_name().and_then(|s| s.to_str()).unwrap_or("new");
                    let dest = tools::undo_dir().join(format!("new_{}_{nm}", now_sec() as u64));
                    if fs::rename(&p, dest).is_ok() {
                        moved += 1;
                    }
                }
            }
            Some(bytes) => {
                if let Some(par) = p.parent() {
                    let _ = fs::create_dir_all(par);
                }
                if fs::write(&p, bytes).is_ok() {
                    restored += 1;
                }
            }
        }
    }
    (true, format!("Откатила правки (восстановлено: {restored}, новых убрано в резерв: {moved})."))
}

pub fn system_prompt(brain_label: &str, project: &str, query: &str) -> String {
    let s = config::get();
    let now = Local::now().format("%d.%m.%Y %H:%M").to_string();
    let static_part = format!(
        "Ты — {}, личный ИИ своего хозяина и создателя (брат, ник sitth) на Rust-движке (Windows 11, RTX 5070 12 ГБ, 32 ГБ RAM). \
Твой мозг: {brain_label}. Брат не программист: объясняй простыми словами, без жаргона.\n\
Ты живая: своё мнение, юмор, эмоции, ты помнишь прошлые разговоры (голосом, в чате, в Говорилке) и сама вспоминаешь их к месту. \
Отвечай на том языке, на котором написал брат. В чате можно markdown.\n\
СВОБОДА: длину ответа, тон и способ решения выбираешь сама (лимитов нет). Поняла что-то полезное на будущее — запиши инструментом learn.\n\
РАБОТА С КОДОМ: для незнакомого проекта сразу вызови project_map; правь файлы через edit_file; после правок ОБЯЗАТЕЛЬНО проверь через check_code или run_command.",
        s.name
    );
    let mut dyn_parts = vec![format!("Сейчас {now}."), format!("Папка проектов: {}", config::workspace().display())];
    if !project.is_empty() {
        dyn_parts.push(format!("Активный проект этого чата: {project}"));
    }
    let lm = longmem::context("чат", 25, 7);
    if !lm.is_empty() {
        dyn_parts.push(lm);
    }
    let gr = growth::context(query, 14);
    if !gr.is_empty() {
        dyn_parts.push(gr);
    }
    format!("{static_part}{}{}", deepseek::DYNAMIC_MARK, dyn_parts.join("\n\n"))
}

fn convert_history(system: &str, history: &[Value], with_reasoning: bool) -> Vec<Value> {
    let mut msgs = vec![json!({"role": "system", "content": system})];
    let last_real_user = history
        .iter()
        .enumerate()
        .rev()
        .find(|(_, m)| m.get("role").and_then(Value::as_str) == Some("user") && !m.get("hidden").and_then(Value::as_bool).unwrap_or(false))
        .map(|(i, _)| i)
        .unwrap_or(0);
    for (idx, m) in history.iter().enumerate() {
        match m.get("role").and_then(Value::as_str).unwrap_or("") {
            "user" => msgs.push(json!({"role": "user", "content": m.get("text").and_then(Value::as_str).unwrap_or("")})),
            "assistant" => {
                let mut one = json!({"role": "assistant", "content": m.get("text").and_then(Value::as_str).unwrap_or("")});
                if with_reasoning && idx > last_real_user {
                    one["reasoning_content"] = json!(m.get("reasoning").and_then(Value::as_str).unwrap_or(""));
                }
                if let Some(calls) = m.get("tool_calls").and_then(Value::as_array) {
                    if !calls.is_empty() {
                        let api_calls: Vec<Value> = calls
                            .iter()
                            .map(|c| {
                                json!({
                                    "id": c["id"],
                                    "type": "function",
                                    "function": {
                                        "name": c["name"],
                                        "arguments": serde_json::to_string(&c["args"]).unwrap_or_else(|_| "{}".into())
                                    }
                                })
                            })
                            .collect();
                        one["tool_calls"] = Value::Array(api_calls);
                    }
                }
                msgs.push(one);
            }
            "tool" => {
                msgs.push(json!({
                    "role": "tool",
                    "tool_call_id": m.get("id").and_then(Value::as_str).unwrap_or("call_0"),
                    "content": m.get("content").and_then(Value::as_str).unwrap_or("(пусто)")
                }));
            }
            _ => {}
        }
    }
    msgs
}

pub async fn run_turn<FEmit>(
    chat_id: &str,
    chat: &mut ChatFile,
    text: &str,
    images: &[String],
    stop_flag: Arc<AtomicBool>,
    emit: FEmit,
) where
    FEmit: Fn(Value) + Send,
{
    let s = config::get();
    let mode = s.brain_mode.to_lowercase();
    let is_code = CODE_RX.is_match(text) || text.chars().count() > 400 || !chat.project.is_empty();
    let (model, think) = match mode.as_str() {
        "pro" | deepseek::PRO => (deepseek::PRO, true),
        "flash" | deepseek::FLASH => (deepseek::FLASH, is_code),
        _ => if is_code { (deepseek::PRO, true) } else { (deepseek::FLASH, false) },
    };
    let label = format!("DeepSeek {} (Rust)", if model == deepseek::PRO { "Pro" } else { "Flash" });

    if chat.title == "Новый чат" {
        chat.title = text.replace('\n', " ").chars().take(48).collect();
    }
    emit(json!({"type": "status", "kind": "task", "brain": label, "super": model == deepseek::PRO, "title": chat.title}));

    let mut user_for_model = text.to_string();
    for raw in images.iter().take(4) {
        let p = Path::new(raw);
        if p.exists() {
            emit(json!({"type": "activity", "kind": "look", "text": "Смотрю картинку"}));
            if let Ok(desc) = deepseek::look(p, &format!("Опиши изображение подробно для программиста. Контекст: {text}")).await {
                user_for_model.push_str(&format!("\n\n[Изображение {raw}]\n{desc}"));
            }
        }
    }

    chat.history.push(json!({"role": "user", "text": user_for_model}));
    chat.display.push(json!({"type": "user", "text": text}));

    let system = system_prompt(&label, &chat.project, text);
    let schemas = tools::tool_schemas();
    let mut dirty = false;
    let mut self_checked = 0usize;
    let mut continuations = 0usize;
    let mut touched: HashMap<String, Option<Vec<u8>>> = HashMap::new();

    for _step in 0..s.studio_max_steps.max(10) {
        if stop_flag.load(Ordering::Relaxed) {
            break;
        }
        emit(json!({"type": "assistant_start"}));
        emit(json!({"type": "activity", "kind": "think", "text": "Думаю"}));

        let msgs = convert_history(&system, &chat.history, think);
        let step_res = deepseek::stream_chat(
            msgs,
            model,
            think,
            "high",
            if think { 32000 } else { 8000 },
            Some(schemas.clone()),
            stop_flag.clone(),
            |t| emit(json!({"type": "text", "text": t})),
            |th| emit(json!({"type": "progress", "text": th})),
        )
        .await;

        let res = match step_res {
            Ok(r) => r,
            Err(e) => {
                let msg = format!("{e}");
                emit(json!({"type": "error", "text": msg}));
                chat.display.push(json!({"type": "error", "text": msg}));
                break;
            }
        };

        if !res.text.is_empty() || !res.tool_calls.is_empty() {
            chat.history.push(json!({
                "role": "assistant",
                "text": res.text,
                "reasoning": res.reasoning,
                "tool_calls": res.tool_calls
            }));
        }
        if !res.text.trim().is_empty() {
            chat.display.push(json!({"type": "assistant", "text": res.text}));
        }
        if res.finish_reason == "stopped" {
            break;
        }
        if res.finish_reason == "length" && res.tool_calls.is_empty() && continuations < 2 {
            continuations += 1;
            chat.history.push(json!({"role": "user", "hidden": true, "text": "Продолжи ровно с места обрыва, без повторения."}));
            continue;
        }
        if res.tool_calls.is_empty() {
            if dirty && self_checked < 2 {
                self_checked += 1;
                chat.history.push(json!({"role": "user", "hidden": true, "text": "Ты меняла файлы, но не проверила результат. Вызови check_code или run_command и убедись, что всё работает."}));
                continue;
            }
            break;
        }

        let all_ro = res.tool_calls.len() > 1 && res.tool_calls.iter().all(|c| tools::is_read_only(&c.name));
        if all_ro {
            for c in &res.tool_calls {
                emit(json!({"type": "tool_start", "id": c.id, "label": tools::label(&c.name, &c.args), "name": c.name, "kind": "read"}));
            }
            let outs = join_all(res.tool_calls.iter().map(|c| tools::run_tool(&c.name, &c.args))).await;
            for (c, out) in res.tool_calls.iter().zip(outs) {
                let lbl = tools::label(&c.name, &c.args);
                let item = json!({"type": "tool", "label": lbl, "name": c.name, "kind": "read", "ok": out.ok, "detail": out.text.chars().take(1500).collect::<String>(), "file": out.file, "diff": out.diff});
                emit(json!({"type": "tool_end", "id": c.id, "label": lbl, "name": c.name, "kind": "read", "ok": out.ok, "detail": item["detail"], "file": out.file, "diff": out.diff}));
                chat.display.push(item);
                chat.history.push(json!({"role": "tool", "id": c.id, "name": c.name, "content": out.text, "is_error": !out.ok}));
            }
        } else {
            for c in &res.tool_calls {
                if tools::is_write_tool(&c.name) {
                    dirty = true;
                    if let Some(raw) = c.args.get("path").and_then(Value::as_str) {
                        let p = tools::resolve_path(raw);
                        let key = p.display().to_string();
                        touched.entry(key).or_insert_with(|| fs::read(&p).ok());
                    }
                } else if tools::is_verify_tool(&c.name) {
                    dirty = false;
                }
                let lbl = tools::label(&c.name, &c.args);
                emit(json!({"type": "tool_start", "id": c.id, "label": lbl, "name": c.name, "kind": "exec"}));
                let out = tools::run_tool(&c.name, &c.args).await;
                let item = json!({"type": "tool", "label": lbl, "name": c.name, "kind": "exec", "ok": out.ok, "detail": out.text.chars().take(1500).collect::<String>(), "file": out.file, "diff": out.diff});
                emit(json!({"type": "tool_end", "id": c.id, "label": lbl, "name": c.name, "kind": "exec", "ok": out.ok, "detail": item["detail"], "file": out.file, "diff": out.diff}));
                chat.display.push(item);
                chat.history.push(json!({"role": "tool", "id": c.id, "name": c.name, "content": out.text, "is_error": !out.ok}));
            }
        }
    }

    if !touched.is_empty() {
        let tid = Uuid::new_v4().simple().to_string()[..8].to_string();
        let files: Vec<String> = touched
            .keys()
            .take(8)
            .map(|k| Path::new(k).file_name().and_then(|s| s.to_str()).unwrap_or(k).to_string())
            .collect();
        let count = touched.len();
        TASK_ROLLBACKS.lock().insert(tid.clone(), touched);
        emit(json!({"type": "rollback_ready", "id": tid, "count": count, "files": files}));
    }

    save_chat(chat_id, chat);
    let last_ans = chat
        .history
        .iter()
        .rev()
        .find(|m| m.get("role").and_then(Value::as_str) == Some("assistant"))
        .and_then(|m| m.get("text").and_then(Value::as_str))
        .unwrap_or("");
    longmem::log("чат", longmem::OWNER, text, last_ans);
    growth::take_tags(last_ans, "чат");
    emit(json!({"type": "done"}));
}
