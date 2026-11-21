//! Мартышка на Rust — единое нативное приложение:
//!   - по умолчанию (Windows): окно WebView2 с агентом-программистом, памятью, опытом и голосом Fish Audio;
//!   - --govorilka: прямой 24/7 WebSocket-бот Говорилки (botgw) без GUI и кабелей;
//!   - --telegram: автономный 24/7 Telegram-сервис без окна;
//!   - --server: 24/7 серверный режим (Говорилка + Telegram одновременно);
//!   - --ask "вопрос": быстрый запрос из консоли.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod agent;
mod audio;
mod commands;
mod config;
mod deepseek;
mod fish;
mod govorilka_ws;
mod growth;
mod longmem;
mod settings_ui;
mod telegram;
mod tools;

#[cfg(windows)]
const EMBEDDED_UI: &str = include_str!("../../martyshka/studio/ui/index.html");

use parking_lot::Mutex;
use serde_json::{json, Map, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--govorilka") {
        let rt = tokio::runtime::Runtime::new().expect("tokio");
        rt.block_on(govorilka_ws::run_forever(Arc::new(AtomicBool::new(false))));
        return;
    }
    if args.iter().any(|a| a == "--server") {
        let rt = tokio::runtime::Runtime::new().expect("tokio");
        rt.block_on(async {
            tokio::join!(
                govorilka_ws::run_forever(Arc::new(AtomicBool::new(false))),
                telegram::run_forever(),
            );
        });
        return;
    }
    if args.iter().any(|a| a == "--telegram") {
        let rt = tokio::runtime::Runtime::new().expect("tokio");
        rt.block_on(telegram::run_forever());
        return;
    }
    if let Some(pos) = args.iter().position(|a| a == "--ask") {
        let q = args.get(pos + 1).cloned().unwrap_or_default();
        let rt = tokio::runtime::Runtime::new().expect("tokio");
        rt.block_on(async {
            let cid = agent::new_chat_id();
            let mut chat = agent::load_chat(&cid);
            agent::run_turn(&cid, &mut chat, &q, &[], Arc::new(AtomicBool::new(false)), |ev| {
                if ev.get("type").and_then(Value::as_str) == Some("text") {
                    print!("{}", ev.get("text").and_then(Value::as_str).unwrap_or(""));
                }
            })
            .await;
            println!();
        });
        return;
    }

    #[cfg(windows)]
    run_desktop_ui();

    #[cfg(not(windows))]
    {
        let rt = tokio::runtime::Runtime::new().expect("tokio");
        rt.block_on(telegram::run_forever());
    }
}

#[cfg(windows)]
fn run_desktop_ui() {
    use base64::Engine;
    use tao::dpi::LogicalSize;
    use tao::event::{Event, WindowEvent};
    use tao::event_loop::{ControlFlow, EventLoopBuilder};
    use tao::window::WindowBuilder;
    use wry::WebViewBuilder;

    #[derive(Debug)]
    enum UserEvent {
        Js(String),
    }

    let rt = Arc::new(tokio::runtime::Runtime::new().expect("tokio runtime"));
    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();

    let window = WindowBuilder::new()
        .with_title("Мартышка (Rust)")
        .with_inner_size(LogicalSize::new(1120.0, 780.0))
        .with_min_inner_size(LogicalSize::new(720.0, 520.0))
        .build(&event_loop)
        .expect("window");

    let ui_path = config::project_dir().join("martyshka").join("studio").join("ui").join("index.html");
    let html = std::fs::read_to_string(&ui_path).unwrap_or_else(|_| EMBEDDED_UI.to_string());

    // Мост window.pywebview.api → Rust IPC, полностью совместимый с существующим index.html
    let init_script = r#"
(function(){
  const pending = new Map();
  let nextId = 1;
  window.__rsResolve = (id, ok, val) => {
    const p = pending.get(id);
    if (!p) return;
    pending.delete(id);
    ok ? p.resolve(val) : p.reject(val);
  };
  const call = (method, args) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, {resolve, reject});
    window.ipc.postMessage(JSON.stringify({id, method, args}));
  });
  window.pywebview = {
    api: new Proxy({}, { get: (_, prop) => (...args) => call(String(prop), args) })
  };
  window.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => window.dispatchEvent(new Event('pywebviewready')), 20);
  });
})();
"#;

    struct AppState {
        chat_id: String,
        chat: agent::ChatFile,
        busy: bool,
        stop: Arc<AtomicBool>,
        speak_stop: Arc<AtomicBool>,
        dictate_stop: Arc<AtomicBool>,
        gov_stop: Arc<AtomicBool>,
    }

    let init_id = agent::new_chat_id();
    let state = Arc::new(Mutex::new(AppState {
        chat_id: init_id.clone(),
        chat: agent::load_chat(&init_id),
        busy: false,
        stop: Arc::new(AtomicBool::new(false)),
        speak_stop: Arc::new(AtomicBool::new(false)),
        dictate_stop: Arc::new(AtomicBool::new(false)),
        gov_stop: Arc::new(AtomicBool::new(true)),
    }));

    let rt_ipc = rt.clone();
    let st_ipc = state.clone();
    let proxy_ipc = proxy.clone();

    let webview = WebViewBuilder::new()
        .with_initialization_script(init_script)
        .with_html(html)
        .with_ipc_handler(move |req| {
            let body = req.body().to_string();
            let Ok(v) = serde_json::from_str::<Value>(&body) else { return };
            let req_id = v.get("id").and_then(Value::as_u64).unwrap_or(0);
            let method = v.get("method").and_then(Value::as_str).unwrap_or("").to_string();
            let args = v.get("args").and_then(Value::as_array).cloned().unwrap_or_default();

            let st = st_ipc.clone();
            let px = proxy_ipc.clone();
            rt_ipc.spawn(async move {
                let res: Value = match method.as_str() {
                    "init" => {
                        let s = config::get();
                        json!({
                            "chats": agent::list_chats(),
                            "name": s.name,
                            "ready": deepseek::ready(),
                            "chat_speak": s.chat_speak,
                            "volume": s.voice_volume,
                            "brain_mode": s.brain_mode.replace("deepseek-v4-", "").replace("deepseek-", ""),
                            "theme": s.ui_theme,
                            "tab": "chat",
                            "voice": false,
                            "gov": govorilka_ws::is_running()
                        })
                    }
                    "status_dots" => json!({"voice": false, "gov": govorilka_ws::is_running()}),
                    "balances" => json!({"deepseek": deepseek::balance().await, "fish": fish::credits().await}),
                    "usage_info" => {
                        let u = deepseek::usage();
                        let pct = if u.r#in > 0 { (u.cached * 100) / u.r#in } else { 0 };
                        json!({"requests": u.requests, "in": u.r#in, "cached": u.cached, "out": u.out, "cache_pct": pct})
                    }
                    "set_brain_mode" => {
                        let m = args.first().and_then(Value::as_str).unwrap_or("auto").to_string();
                        let mut mp = Map::new();
                        mp.insert("BRAIN_MODE".into(), json!(m));
                        let _ = config::patch(mp);
                        json!({"ok": true})
                    }
                    "set_theme" => {
                        let t = args.first().and_then(Value::as_str).unwrap_or("cyan").to_string();
                        let mut mp = Map::new();
                        mp.insert("UI_THEME".into(), json!(t));
                        let _ = config::patch(mp);
                        json!({"ok": true, "theme": t})
                    }
                    "save_settings" => {
                        if let Some(obj) = args.first().and_then(Value::as_object) {
                            let _ = config::patch(obj.clone());
                        }
                        json!({"ok": true, "message": "Сохранено ✓"})
                    }
                    "list_chats" => json!(agent::list_chats()),
                    "new_chat" => {
                        let mut g = st.lock();
                        if g.busy { json!({"ok": false}) } else {
                            let nid = agent::new_chat_id();
                            g.chat_id = nid.clone();
                            g.chat = agent::load_chat(&nid);
                            json!({"ok": true, "id": nid})
                        }
                    }
                    "open_chat" => {
                        let cid = args.first().and_then(Value::as_str).unwrap_or("").to_string();
                        let mut g = st.lock();
                        if g.busy { json!({"ok": false}) } else {
                            let cf = agent::load_chat(&cid);
                            let out = json!({"ok": true, "id": cid, "title": cf.title, "display": cf.display, "project": cf.project});
                            g.chat_id = cid;
                            g.chat = cf;
                            out
                        }
                    }
                    "delete_chat" => {
                        let cid = args.first().and_then(Value::as_str).unwrap_or("");
                        agent::delete_chat(cid);
                        json!({"ok": true})
                    }
                    "pin_chat" => {
                        let cid = args.first().and_then(Value::as_str).unwrap_or("");
                        let pinned = args.get(1).and_then(Value::as_bool).unwrap_or(false);
                        let mut cf = agent::load_chat(cid);
                        cf.pinned = pinned;
                        agent::save_chat(cid, &cf);
                        json!({"ok": true})
                    }
                    "rename_chat" => {
                        let cid = args.first().and_then(Value::as_str).unwrap_or("");
                        let title = args.get(1).and_then(Value::as_str).unwrap_or("").trim().to_string();
                        if title.is_empty() { json!({"ok": false}) } else {
                            let mut cf = agent::load_chat(cid);
                            cf.title = title.clone();
                            agent::save_chat(cid, &cf);
                            json!({"ok": true, "title": title})
                        }
                    }
                    "projects_list" => {
                        let ws = config::workspace();
                        let mut items = Vec::new();
                        if let Ok(rd) = std::fs::read_dir(&ws) {
                            for e in rd.flatten() {
                                if e.path().is_dir() {
                                    items.push(json!({"name": e.file_name().to_string_lossy(), "path": e.path().display().to_string()}));
                                }
                            }
                        }
                        let cur = st.lock().chat.project.clone();
                        json!({"current": cur, "items": items})
                    }
                    "set_project" => {
                        let p = args.first().and_then(Value::as_str).unwrap_or("").trim().to_string();
                        let mut g = st.lock();
                        g.chat.project = p.clone();
                        if !g.chat.history.is_empty() {
                            let cid = g.chat_id.clone();
                            agent::save_chat(&cid, &g.chat);
                        }
                        json!({"ok": true, "current": p})
                    }
                    "rollback_task" => {
                        let tid = args.first().and_then(Value::as_str).unwrap_or("");
                        let (ok, msg) = agent::rollback_task(tid);
                        json!({"ok": ok, "message": msg})
                    }
                    "regenerate" | "edit_last_user" => {
                        let mut g = st.lock();
                        if g.busy { json!({"ok": false}) } else {
                            let last = agent::pop_last_turn(&mut g.chat);
                            if last.is_empty() { json!({"ok": false}) } else {
                                let cid = g.chat_id.clone();
                                agent::save_chat(&cid, &g.chat);
                                json!({"ok": true, "text": last, "display": g.chat.display})
                            }
                        }
                    }
                    "save_pasted_image" | "save_dropped_file" => {
                        let (fname, data_url) = if method == "save_pasted_image" {
                            ("shot.png".to_string(), args.first().and_then(Value::as_str).unwrap_or("").to_string())
                        } else {
                            (args.first().and_then(Value::as_str).unwrap_or("file.bin").to_string(),
                             args.get(1).and_then(Value::as_str).unwrap_or("").to_string())
                        };
                        if let Some((_, b64)) = data_url.split_once(',') {
                            if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(b64) {
                                let dir = config::data_dir().join("studio").join("uploads");
                                let _ = std::fs::create_dir_all(&dir);
                                let p = dir.join(format!("{}_{fname}", uuid::Uuid::new_v4().simple()));
                                if std::fs::write(&p, bytes).is_ok() {
                                    let is_img = matches!(p.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase().as_str(), "png" | "jpg" | "jpeg" | "webp" | "bmp" | "gif");
                                    json!({"ok": true, "path": p.display().to_string(), "name": fname, "is_image": is_img})
                                } else { json!({"ok": false}) }
                            } else { json!({"ok": false}) }
                        } else { json!({"ok": false}) }
                    }
                    "speak" => {
                        let txt = args.first().and_then(Value::as_str).unwrap_or("").to_string();
                        let stop = {
                            let mut g = st.lock();
                            g.speak_stop.store(true, Ordering::Relaxed);
                            let ns = Arc::new(AtomicBool::new(false));
                            g.speak_stop = ns.clone();
                            ns
                        };
                        let vol = config::get().voice_volume;
                        tokio::spawn(async move {
                            if let Ok(pcm) = fish::speak_pcm(&txt, None, None, 0).await {
                                let _ = tokio::task::spawn_blocking(move || fish::play_pcm_blocking(pcm, vol, stop)).await;
                            }
                        });
                        json!({"ok": true})
                    }
                    "stop_speak" => {
                        st.lock().speak_stop.store(true, Ordering::Relaxed);
                        json!({"ok": true})
                    }
                    "stop" => {
                        st.lock().stop.store(true, Ordering::Relaxed);
                        json!({"ok": true})
                    }
                    "send" => {
                        let txt = args.first().and_then(Value::as_str).unwrap_or("").trim().to_string();
                        let imgs: Vec<String> = args
                            .get(2)
                            .and_then(Value::as_array)
                            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
                            .unwrap_or_default();
                        let prep = {
                            let mut g = st.lock();
                            if g.busy || (txt.is_empty() && imgs.is_empty()) {
                                None
                            } else {
                                g.busy = true;
                                g.stop = Arc::new(AtomicBool::new(false));
                                Some((g.chat_id.clone(), g.chat.clone(), g.stop.clone()))
                            }
                        };
                        if let Some((cid, mut chat_copy, stop_flag)) = prep {
                            let st_bg = st.clone();
                            let px_bg = px.clone();
                            let cid_ret = cid.clone();
                            tokio::spawn(async move {
                                agent::run_turn(&cid, &mut chat_copy, &txt, &imgs, stop_flag, |ev| {
                                    let js = format!("window.onEvents && window.onEvents([{}]);", ev);
                                    let _ = px_bg.send_event(UserEvent::Js(js));
                                })
                                .await;
                                let mut g = st_bg.lock();
                                g.chat = chat_copy;
                                g.busy = false;
                            });
                            json!({"ok": true, "id": cid_ret})
                        } else {
                            json!({"ok": false})
                        }
                    }
                    "memory_info" => {
                        let people: Vec<Value> = longmem::load_people()
                            .into_iter()
                            .map(|(name, p)| json!({"name": name, "facts": p.facts, "anger": longmem::anger(&name), "last": p.last_seen, "count": p.count}))
                            .collect();
                        let days: Vec<Value> = longmem::load_days().into_iter().rev().map(|(d, t)| json!([d, t])).collect();
                        let lessons = growth::items();
                        json!({"people": people, "days": days, "facts": [], "lessons": lessons})
                    }
                    "forget_person_fact" => {
                        let n = args.first().and_then(Value::as_str).unwrap_or("");
                        let f = args.get(1).and_then(Value::as_str).unwrap_or("");
                        longmem::forget_about(n, f);
                        json!({"ok": true})
                    }
                    "calm_person" => {
                        let n = args.first().and_then(Value::as_str).unwrap_or("");
                        longmem::set_anger(n, 0);
                        json!({"ok": true})
                    }
                    "forget_lesson" => {
                        let t = args.first().and_then(Value::as_str).unwrap_or("");
                        growth::forget(t);
                        json!({"ok": true})
                    }
                    "commands_list" => json!(commands::load()),
                    "save_command" => {
                        let phrase = args.first().and_then(Value::as_str).unwrap_or("").trim().to_string();
                        let steps_raw = args.get(1).and_then(Value::as_str).unwrap_or("");
                        let say = args.get(2).and_then(Value::as_str).unwrap_or("").trim().to_string();
                        let steps: Vec<String> = steps_raw.split(|c| c == ';' || c == '\n').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
                        if phrase.is_empty() || steps.is_empty() {
                            json!({"ok": false, "message": "Укажи фразу и шаги."})
                        } else {
                            let mut list: Vec<_> = commands::load().into_iter().filter(|c| c.phrase.to_lowercase() != phrase.to_lowercase()).collect();
                            list.push(commands::UserCommand { phrase: phrase.clone(), steps, say });
                            let _ = commands::save(&list);
                            json!({"ok": true, "message": format!("Команда «{phrase}» сохранена.")})
                        }
                    }
                    "delete_command" => {
                        let phrase = args.first().and_then(Value::as_str).unwrap_or("").to_lowercase();
                        let list: Vec<_> = commands::load().into_iter().filter(|c| c.phrase.to_lowercase() != phrase).collect();
                        let _ = commands::save(&list);
                        json!({"ok": true})
                    }
                    "open_path" => {
                        let target = args.first().and_then(Value::as_str).unwrap_or("");
                        let _ = std::process::Command::new("cmd").args(["/C", "start", "", target]).spawn();
                        json!({"ok": true})
                    }
                    "settings_schema" => settings_ui::build_schema().await,
                    "connectors" => json!(settings_ui::connectors_list()),
                    "toggle_connector" => {
                        let key = args.first().and_then(Value::as_str).unwrap_or("").to_string();
                        let on = args.get(1).and_then(Value::as_bool).unwrap_or(true);
                        let mut off = config::get().studio_disabled_groups;
                        if on { off.retain(|x| x != &key); } else if !off.contains(&key) { off.push(key.clone()); }
                        let mut mp = Map::new();
                        mp.insert("STUDIO_DISABLED_GROUPS".into(), json!(off));
                        if key == "web" { mp.insert("WEB_ENABLED".into(), json!(on)); }
                        let _ = config::patch(mp);
                        json!(settings_ui::connectors_list())
                    }
                    "voices" => json!(fish::catalog()),
                    "search_voices" => json!(fish::search_voices(args.first().and_then(Value::as_str).unwrap_or("")).await),
                    "preview_voice" => {
                        let vid = args.first().and_then(Value::as_str).unwrap_or("").to_string();
                        let vol = config::get().voice_volume;
                        match fish::speak_pcm("Привет! Я Мартышка на Rust. Как тебе мой голос?", Some(&vid), None, 0).await {
                            Ok(pcm) => {
                                let _ = tokio::task::spawn_blocking(move || fish::play_pcm_blocking(pcm, vol, Arc::new(AtomicBool::new(false)))).await;
                                json!({"ok": true})
                            }
                            Err(e) => json!({"error": format!("{e}")}),
                        }
                    }
                    "dictate" => {
                        let stop = {
                            let mut g = st.lock();
                            let ns = Arc::new(AtomicBool::new(false));
                            g.dictate_stop = ns.clone();
                            ns
                        };
                        match tokio::task::spawn_blocking(move || audio::record_wav_16k(stop, 45)).await {
                            Ok(Ok(wav)) if !wav.is_empty() => match deepseek::transcribe(wav).await {
                                Ok(t) => json!({"text": t}),
                                Err(e) => json!({"text": "", "error": format!("{e}")}),
                            },
                            Ok(Ok(_)) => json!({"text": ""}),
                            Ok(Err(e)) => json!({"text": "", "error": format!("{e}")}),
                            Err(e) => json!({"text": "", "error": format!("{e}")}),
                        }
                    }
                    "dictate_stop" => {
                        st.lock().dictate_stop.store(true, Ordering::Relaxed);
                        json!({"ok": true})
                    }
                    "capture_screen" => {
                        let dir = config::data_dir().join("studio").join("uploads");
                        let _ = std::fs::create_dir_all(&dir);
                        let path = dir.join(format!("screen_{}.png", chrono::Local::now().format("%Y%m%d_%H%M%S")));
                        let ps = format!(
                            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; \
                             $b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; \
                             $bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; \
                             $g=[System.Drawing.Graphics]::FromImage($bmp); \
                             $g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); \
                             $bmp.Save('{}',[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()",
                            path.display()
                        );
                        let ok = std::process::Command::new("powershell")
                            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &ps])
                            .status()
                            .map(|s| s.success())
                            .unwrap_or(false);
                        if ok && path.exists() {
                            let b64 = std::fs::read(&path)
                                .map(|b| format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(b)))
                                .unwrap_or_default();
                            let nm = path.file_name().and_then(|s| s.to_str()).unwrap_or("screen.png").to_string();
                            json!({"ok": true, "path": path.display().to_string(), "name": nm, "preview": b64})
                        } else {
                            json!({"ok": false})
                        }
                    }
                    "pick_file" => {
                        let ps = "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; if($d.ShowDialog() -eq 'OK'){ [Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output $d.FileName }";
                        let path = std::process::Command::new("powershell")
                            .args(["-NoProfile", "-Command", ps])
                            .output()
                            .ok()
                            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                            .unwrap_or_default();
                        json!({"path": path})
                    }
                    "notify_done" => {
                        let _ = std::process::Command::new("powershell")
                            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", "[System.Media.SystemSounds]::Asterisk.Play()"])
                            .spawn();
                        json!({"ok": true})
                    }
                    "gov_info" => {
                        let s = config::get();
                        json!({
                            "nick": s.govorilka_nick,
                            "status": govorilka_ws::status_text(),
                            "running": govorilka_ws::is_running(),
                            "cable": true,
                            "voice": s.govorilka_voice_id,
                            "model": s.govorilka_model,
                            "autostart": false,
                            "log": govorilka_ws::recent_log(80),
                            "dialog": true,
                            "fish_model": s.govorilka_fish_model,
                            "fish_models": [
                                ["", "По умолчанию (из Настроек)"],
                                ["s2.1-pro-free", "S2.1 Pro Free (бесплатно, без кредитов)"],
                                ["s2-pro", "S2-Pro (высокое качество)"],
                                ["s2-pro-beta", "Drama 3 (s2-pro-beta, максимум эмоций)"]
                            ],
                            "greet": true,
                            "chime": false
                        })
                    }
                    "gov_action" => {
                        let act = args.first().and_then(Value::as_str).unwrap_or("");
                        let val = args.get(1);
                        match act {
                            "join" => {
                                if govorilka_ws::is_running() {
                                    json!({"ok": true, "message": "Мартышка уже на сервере напрямую!"})
                                } else {
                                    let stop = {
                                        let mut g = st.lock();
                                        let ns = Arc::new(AtomicBool::new(false));
                                        g.gov_stop = ns.clone();
                                        ns
                                    };
                                    tokio::spawn(govorilka_ws::run_forever(stop));
                                    json!({"ok": true, "message": "Подключаюсь напрямую к Говорилке (botgw)!"})
                                }
                            }
                            "leave" | "quit" => {
                                st.lock().gov_stop.store(true, Ordering::Relaxed);
                                govorilka_ws::silence();
                                json!({"ok": true, "message": "Отключаюсь от Говорилки."})
                            }
                            "silence" => {
                                govorilka_ws::silence();
                                json!({"ok": true, "message": "Замолчала!"})
                            }
                            "morni" => {
                                govorilka_ws::trigger_morni();
                                json!({"ok": true, "message": "Режим разговора с Морни включён на 4 минуты!"})
                            }
                            "model" => {
                                let m = val.and_then(Value::as_str).unwrap_or(deepseek::FLASH).to_string();
                                let mut mp = Map::new();
                                mp.insert("GOVORILKA_MODEL".into(), json!(m));
                                let _ = config::patch(mp);
                                json!({"ok": true, "message": format!("Модель для Говорилки: {m}")})
                            }
                            "fish_model" => {
                                let fm = val.and_then(Value::as_str).unwrap_or("").to_string();
                                let mut mp = Map::new();
                                mp.insert("GOVORILKA_FISH_MODEL".into(), json!(fm));
                                let _ = config::patch(mp);
                                json!({"ok": true, "message": "Движок озвучки обновлён ✓"})
                            }
                            _ => json!({"ok": true, "message": "Готово ✓"}),
                        }
                    }
                    _ => json!({"ok": false}),
                };
                let js = format!("window.__rsResolve({req_id}, true, {res});");
                let _ = px.send_event(UserEvent::Js(js));
            });
        })
        .build(&window)
        .expect("webview");

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => *control_flow = ControlFlow::Exit,
            Event::UserEvent(UserEvent::Js(js)) => {
                let _ = webview.evaluate_script(&js);
            }
            _ => {}
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_owner_sitth_norm() {
        for alias in ["sitth", "Sitth", "sitthome", "Ситх", "Сидхом", "брат"] {
            assert_eq!(longmem::norm(alias), longmem::OWNER);
            assert!(longmem::is_owner(alias));
        }
        assert!(!longmem::is_owner("Enbulih"));
        assert!(!longmem::is_owner("Морни"));
    }

    #[test]
    fn test_deepseek_cache_friendly_splits() {
        let sys = format!("Статика{}Динамика", deepseek::DYNAMIC_MARK);
        let msgs = vec![
            json!({"role": "system", "content": sys}),
            json!({"role": "user", "content": "привет"}),
        ];
        let out = deepseek::cache_friendly(msgs);
        assert_eq!(out.len(), 3);
        assert_eq!(out[0]["content"], "Статика");
        assert!(out[1]["content"].as_str().unwrap().contains("Динамика"));
        assert_eq!(out[2]["role"], "user");
    }

    #[test]
    fn test_fish_prepare_keeps_russian_and_pair_tags() {
        let s = fish::prepare_text("{с усмешкой} Привет <whisper>тихо</whisper>!");
        assert_eq!(s, "[с усмешкой] Привет <whisper>тихо</whisper>!");
    }

    #[test]
    fn test_pcm48k_to_wav16k_header_and_size() {
        // 300 сэмплов @ 48кГц (600 байт) -> 100 сэмплов @ 16кГц (200 байт) + 44 байта заголовка WAV
        let pcm48 = vec![0u8; 600];
        let wav = govorilka_ws::pcm48k_to_wav16k(&pcm48);
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(wav.len(), 244);
    }

    #[test]
    fn test_govorilka_clean_reply_tags() {
        let out = govorilka_ws::clean_reply_tags("[ТОН: 0] {весело} Привет, брат! [МОЛЧУ]", "sitth");
        assert_eq!(out, "{весело} Привет, брат!");
    }
}
