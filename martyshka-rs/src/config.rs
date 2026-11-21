//! Настройки Мартышки — 100% совместимы с data/settings.json.
use anyhow::Result;
use once_cell::sync::Lazy;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default = "d_name", rename = "NAME")]
    pub name: String,
    #[serde(default = "d_nick", rename = "OWNER_NICKNAME")]
    pub owner_nickname: String,
    #[serde(default = "d_mode", rename = "PERSONALITY_MODE")]
    pub personality_mode: String,
    #[serde(default = "d_jarvis", rename = "JARVIS_ADDRESS")]
    pub jarvis_address: String,
    #[serde(default, rename = "PERSONALITY_EXTRA")]
    pub personality_extra: String,

    #[serde(default, rename = "DEEPSEEK_KEY")]
    pub deepseek_key: String,
    #[serde(default = "d_ds_url", rename = "DEEPSEEK_URL")]
    pub deepseek_url: String,
    #[serde(default = "d_auto", rename = "BRAIN_MODE")]
    pub brain_mode: String,
    #[serde(default = "d_true", rename = "ECONOMY")]
    pub economy: bool,
    #[serde(default = "d_econ_bal", rename = "ECONOMY_BALANCE")]
    pub economy_balance: f64,

    #[serde(default, rename = "FISH_KEY")]
    pub fish_key: String,
    #[serde(default = "d_fish_model", rename = "FISH_MODEL")]
    pub fish_model: String,
    #[serde(default, rename = "VOICE_ID")]
    pub voice_id: String,
    #[serde(default, rename = "MOOD_VOICE")]
    pub mood_voice: bool,
    #[serde(default = "d_angry_from", rename = "MOOD_ANGRY_FROM")]
    pub mood_angry_from: i32,
    #[serde(default, rename = "ANGRY_VOICE_ID")]
    pub angry_voice_id: String,
    #[serde(default = "d_vol", rename = "VOICE_VOLUME")]
    pub voice_volume: f32,
    #[serde(default, rename = "CHAT_SPEAK")]
    pub chat_speak: bool,

    #[serde(default = "d_true", rename = "WEB_ENABLED")]
    pub web_enabled: bool,
    #[serde(default, rename = "TELEGRAM_TOKEN")]
    pub telegram_token: String,
    #[serde(default, rename = "TELEGRAM_OWNER_ID")]
    pub telegram_owner_id: i64,

    #[serde(default = "d_workspace", rename = "STUDIO_WORKSPACE")]
    pub studio_workspace: String,
    #[serde(default = "d_max_steps", rename = "STUDIO_MAX_STEPS")]
    pub studio_max_steps: usize,
    #[serde(default, rename = "STUDIO_AUTO_COMMANDS")]
    pub studio_auto_commands: bool,
    #[serde(default, rename = "STUDIO_DISABLED_GROUPS")]
    pub studio_disabled_groups: Vec<String>,
    #[serde(default = "d_theme", rename = "UI_THEME")]
    pub ui_theme: String,

    #[serde(default, rename = "GOVORILKA_BOT_URL")]
    pub govorilka_bot_url: String,
    #[serde(default, rename = "GOVORILKA_BOT_TOKEN")]
    pub govorilka_bot_token: String,
    #[serde(default = "d_name", rename = "GOVORILKA_NICK")]
    pub govorilka_nick: String,
    #[serde(default, rename = "GOVORILKA_VOICE_ID")]
    pub govorilka_voice_id: String,
    #[serde(default = "d_gov_model", rename = "GOVORILKA_MODEL")]
    pub govorilka_model: String,
    #[serde(default, rename = "GOVORILKA_FISH_MODEL")]
    pub govorilka_fish_model: String,
    #[serde(default, rename = "GOVORILKA_REMOTE_URL")]
    pub govorilka_remote_url: String,
}

fn d_gov_model() -> String { "deepseek-flash".into() }

fn d_name() -> String { "Мартышка".into() }
fn d_nick() -> String { "братан".into() }
fn d_mode() -> String { "bro".into() }
fn d_jarvis() -> String { "сэр".into() }
fn d_ds_url() -> String { "https://api.deepseek.com".into() }
fn d_auto() -> String { "auto".into() }
fn d_true() -> bool { true }
fn d_econ_bal() -> f64 { 1.0 }
fn d_fish_model() -> String { "s2.1-pro-free".into() }
fn d_angry_from() -> i32 { 3 }
fn d_vol() -> f32 { 1.0 }
fn d_max_steps() -> usize { 80 }
fn d_theme() -> String { "cyan".into() }
fn d_workspace() -> String {
    let home = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")).unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join("Documents").join("Проекты Мартышки").to_string_lossy().to_string()
}

impl Default for Settings {
    fn default() -> Self {
        serde_json::from_str("{}").unwrap()
    }
}

fn has_real_settings(dir: &Path) -> bool {
    let f = dir.join("data").join("settings.json");
    std::fs::metadata(&f).map(|m| m.is_file() && m.len() > 80).unwrap_or(false)
}

/// Корень проекта Martyshka (где лежит папка data/).
pub fn project_dir() -> PathBuf {
    if let Ok(env_dir) = std::env::var("MARTYSHKA_DIR") {
        let p = PathBuf::from(env_dir);
        if p.exists() {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for anc in exe.ancestors() {
            if has_real_settings(anc)
                || (anc.join("data").is_dir() && anc.join("martyshka").is_dir())
            {
                return anc.to_path_buf();
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for anc in cwd.ancestors() {
            if has_real_settings(anc) {
                return anc.to_path_buf();
            }
        }
    }
    if let Ok(home) = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")) {
        for sub in ["Documents/Martyshka", "Документы/Martyshka", "Martyshka"] {
            let p = PathBuf::from(&home).join(sub);
            if has_real_settings(&p) {
                return p;
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for anc in cwd.ancestors() {
            if anc.join("data").is_dir() {
                return anc.to_path_buf();
            }
        }
    }
    PathBuf::from(".")
}

pub fn data_dir() -> PathBuf {
    project_dir().join("data")
}

pub fn settings_file() -> PathBuf {
    data_dir().join("settings.json")
}

static STATE: Lazy<RwLock<Settings>> = Lazy::new(|| RwLock::new(load_from_disk()));

pub fn load_from_disk() -> Settings {
    let p = settings_file();
    if let Ok(raw) = std::fs::read_to_string(&p) {
        if let Ok(s) = serde_json::from_str::<Settings>(raw.trim_start_matches('\u{feff}')) {
            return s;
        }
    }
    Settings::default()
}

pub fn get() -> Settings {
    STATE.read().clone()
}

pub fn reload() -> Settings {
    let s = load_from_disk();
    *STATE.write() = s.clone();
    s
}

/// Обновить отдельные поля в data/settings.json, не затирая остальные ключи.
pub fn patch(updates: Map<String, Value>) -> Result<Settings> {
    let p = settings_file();
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut obj: Map<String, Value> = std::fs::read_to_string(&p)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default();
    for (k, v) in updates {
        obj.insert(k, v);
    }
    let tmp = p.with_extension("tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&obj)?)?;
    std::fs::rename(&tmp, &p)?;
    Ok(reload())
}

pub fn workspace() -> PathBuf {
    let ws = PathBuf::from(get().studio_workspace);
    let _ = std::fs::create_dir_all(&ws);
    ws
}

#[allow(dead_code)]
pub fn inside_ws(p: &Path) -> bool {
    let ws = workspace();
    let ws_c = ws.canonicalize().unwrap_or(ws);
    let p_c = p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    p_c.starts_with(&ws_c)
}
