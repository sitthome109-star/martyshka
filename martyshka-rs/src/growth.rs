//! Саморазвитие Мартышки — совместимо с data/память/опыт.json.
use crate::config;
use chrono::{Duration, Local};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Lesson {
    pub text: String,
    #[serde(default, rename = "где")]
    pub source: String,
    #[serde(default, rename = "вид")]
    pub kind: String,
    #[serde(default, rename = "когда")]
    pub when: String,
    #[serde(default = "d_one", rename = "подтверждений")]
    pub count: u32,
}

fn d_one() -> u32 { 1 }

static LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));
static WORD_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"[а-яёa-z0-9]{3,}").unwrap());
static TAG_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?is)\[\s*(?:УРОК|ОПЫТ)\s*:\s*(.+?)\s*\]").unwrap());

fn file_path() -> PathBuf {
    config::data_dir().join("память").join("опыт.json")
}

fn words(text: &str) -> HashSet<String> {
    let low = text.to_lowercase().replace('ё', "е");
    WORD_RX
        .find_iter(&low)
        .map(|m| m.as_str().chars().take(6).collect::<String>())
        .filter(|w| !matches!(w.as_str(), "это" | "что" | "как" | "для" | "его" | "она" | "они" | "брат" | "мартыш"))
        .collect()
}

pub fn items() -> Vec<Lesson> {
    fs::read_to_string(file_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save(list: &[Lesson]) {
    let p = file_path();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(list) {
        let tmp = p.with_extension("tmp");
        if fs::write(&tmp, text).is_ok() {
            let _ = fs::rename(&tmp, &p);
        }
    }
}

pub fn add(text: &str, source: &str, kind: &str) -> bool {
    let clean = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .trim_matches(|c| c == '[' || c == ']')
        .trim_end_matches('.')
        .to_string();
    if clean.chars().count() < 8 {
        return false;
    }
    let _g = LOCK.lock();
    let mut list = items();
    let new_w = words(&clean);
    let now = Local::now().format("%Y-%m-%dT%H:%M").to_string();
    for it in &mut list {
        let old_w = words(&it.text);
        let inter = new_w.intersection(&old_w).count();
        let denom = new_w.len().min(old_w.len()).max(1);
        if !new_w.is_empty() && (inter as f64 / denom as f64) >= 0.75 {
            it.when = now;
            it.count += 1;
            if clean.len() > it.text.len() {
                it.text = clean;
            }
            save(&list);
            return false;
        }
    }
    list.push(Lesson {
        text: clean,
        source: source.to_string(),
        kind: kind.to_string(),
        when: now,
        count: 1,
    });
    save(&list);
    true
}

pub fn forget(text: &str) -> usize {
    let _g = LOCK.lock();
    let mut list = items();
    let before = list.len();
    let needle = text.trim().to_lowercase();
    list.retain(|it| !it.text.to_lowercase().contains(&needle));
    let removed = before - list.len();
    if removed > 0 {
        save(&list);
    }
    removed
}

pub fn context(query: &str, n: usize) -> String {
    let mut list = items();
    if list.is_empty() {
        return String::new();
    }
    let q = words(query);
    let recent_cut = (Local::now() - Duration::days(3)).format("%Y-%m-%dT%H:%M").to_string();
    list.sort_by_key(|it| {
        let rel = if q.is_empty() { 0 } else { q.intersection(&words(&it.text)).count() * 3 };
        let conf = it.count.min(5) as usize;
        let fresh = usize::from(it.when >= recent_cut);
        std::cmp::Reverse(rel + conf + fresh)
    });
    let lines: Vec<String> = list.into_iter().take(n).map(|it| format!("- {}", it.text)).collect();
    format!("Твой опыт (сама вынесла из прошлых разговоров и дел — опирайся на него, развивай):\n{}", lines.join("\n"))
}

pub fn take_tags(text: &str, source: &str) -> usize {
    let mut n = 0;
    for cap in TAG_RX.captures_iter(text) {
        if let Some(m) = cap.get(1) {
            if add(m.as_str(), source, "сама") {
                n += 1;
            }
        }
    }
    n
}

/// Двустороннее объединение уроков опыта без дублей.
pub fn merge_lessons(incoming: Vec<Lesson>) -> Vec<Lesson> {
    let _g = LOCK.lock();
    let mut list = items();
    for inc in incoming {
        let key = inc.text.trim().to_lowercase();
        if key.is_empty() {
            continue;
        }
        if let Some(existing) = list.iter_mut().find(|x| x.text.trim().to_lowercase() == key) {
            existing.count = existing.count.max(inc.count);
            if inc.when > existing.when {
                existing.when = inc.when;
            }
        } else {
            list.push(inc);
        }
    }
    save(&list);
    list
}
