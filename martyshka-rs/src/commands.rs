//! Свои команды и сценарии — совместимы с data/команды.json.
use crate::config;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserCommand {
    pub phrase: String,
    pub steps: Vec<String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub say: String,
}

fn file_path() -> PathBuf {
    config::data_dir().join("команды.json")
}

pub fn load() -> Vec<UserCommand> {
    fs::read_to_string(file_path())
        .ok()
        .and_then(|s| serde_json::from_str::<Vec<UserCommand>>(&s).ok())
        .unwrap_or_default()
        .into_iter()
        .filter(|c| !c.phrase.trim().is_empty() && !c.steps.is_empty())
        .collect()
}

pub fn save(items: &[UserCommand]) -> anyhow::Result<()> {
    let p = file_path();
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(p, serde_json::to_string_pretty(items)?)?;
    Ok(())
}
