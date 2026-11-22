//! Долгая память Мартышки — совместима с data/память/ (люди.json, дни.json, лента/*.jsonl).
use crate::config;
use chrono::{Duration, Local, NaiveDate};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub const OWNER: &str = "sitthome";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Person {
    #[serde(default, rename = "факты")]
    pub facts: Vec<String>,
    #[serde(default, rename = "темы")]
    pub topics: Vec<String>,
    #[serde(default, rename = "злость")]
    pub anger: i32,
    #[serde(default, rename = "злость_с")]
    pub anger_since: f64,
    #[serde(default, rename = "первый_раз")]
    pub first_seen: String,
    #[serde(default, rename = "последний_раз")]
    pub last_seen: String,
    #[serde(default, rename = "где")]
    pub where_seen: String,
    #[serde(default, rename = "реплик")]
    pub count: u64,
    #[serde(default, rename = "рейтинг")]
    pub rating: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeedRecord {
    pub t: String,
    #[serde(default, rename = "где")]
    pub source: String,
    #[serde(default, rename = "кто")]
    pub who: String,
    #[serde(default, rename = "сказал")]
    pub said: String,
    #[serde(default, rename = "мартышка", skip_serializing_if = "String::is_empty")]
    pub reply: String,
}

static OWNER_RX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^(сит+х?|сид+х?|сит+х?[оау]м|сид+х?[оау]м|сит+хун|сит+хоум|sit+h?o?m?e?|sith+)$").unwrap()
});
static LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

fn mem_dir() -> PathBuf { config::data_dir().join("память") }
fn feed_dir() -> PathBuf { mem_dir().join("лента") }
fn days_file() -> PathBuf { mem_dir().join("дни.json") }
fn people_file() -> PathBuf { mem_dir().join("люди.json") }

fn now_sec() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn now_iso_min() -> String {
    Local::now().format("%Y-%m-%dT%H:%M").to_string()
}

pub fn norm(name: &str) -> String {
    let cleaned = name.split_whitespace().collect::<Vec<_>>().join(" ");
    let low = cleaned.to_lowercase();
    if cleaned.is_empty()
        || matches!(low.as_str(), "брат" | "хозяин" | "sitth" | "sitthome" | "sith" | "owner")
        || OWNER_RX.is_match(&low)
    {
        return OWNER.to_string();
    }
    cleaned
}

#[allow(dead_code)]
pub fn is_owner(name: &str) -> bool {
    !name.trim().is_empty() && norm(name) == OWNER
}

pub fn is_ignored_person(name: &str) -> bool {
    let low = name.trim().to_lowercase();
    low.is_empty()
        || low == "?"
        || low.contains("кто-то на сервере")
        || low.contains("кто то на сервере")
        || low == "мартышка"
        || low == "martyshka"
        || low == "сервер"
        || low == "система"
}

pub fn load_people() -> HashMap<String, Person> {
    let raw: HashMap<String, Person> = fs::read_to_string(people_file())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    let mut out: HashMap<String, Person> = HashMap::new();
    for (k, p) in raw {
        if is_ignored_person(&k) {
            continue;
        }
        let nk = norm(&k);
        if is_ignored_person(&nk) {
            continue;
        }
        let e = out.entry(nk).or_default();
        for f in p.facts {
            let fl = f.trim().to_lowercase();
            if !fl.is_empty() && !e.facts.iter().any(|x| x.trim().to_lowercase() == fl) {
                e.facts.push(f);
            }
        }
        for t in p.topics {
            if !e.topics.contains(&t) { e.topics.push(t); }
        }
        e.count = e.count.max(p.count);
        if p.last_seen > e.last_seen { e.last_seen = p.last_seen; e.where_seen = p.where_seen; }
        if e.first_seen.is_empty() || (!p.first_seen.is_empty() && p.first_seen < e.first_seen) { e.first_seen = p.first_seen; }
        if p.anger_since >= e.anger_since { e.anger = p.anger; e.anger_since = p.anger_since; }
        if p.rating != 0 { e.rating = p.rating.clamp(-100, 100); }
    }
    out
}

fn save_people(map: &HashMap<String, Person>) {
    let p = people_file();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(map) {
        let tmp = p.with_extension("tmp");
        if fs::write(&tmp, text).is_ok() {
            let _ = fs::rename(&tmp, &p);
        }
    }
}

pub fn load_days() -> BTreeMap<String, String> {
    let mut map: BTreeMap<String, String> = fs::read_to_string(days_file())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    for v in map.values_mut() {
        if v.contains("Рокик") || v.contains("брат") {
            *v = v
                .replace("братом Рокиком", "sitthome")
                .replace("Рокиком", "sitthome")
                .replace("Рокик", "sitthome")
                .replace("братом", "sitthome")
                .replace("брата", "sitthome")
                .replace("брату", "sitthome")
                .replace("брат", "sitthome");
        }
    }
    map
}

fn save_days(map: &BTreeMap<String, String>) {
    let p = days_file();
    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(map) {
        let tmp = p.with_extension("tmp");
        if fs::write(&tmp, text).is_ok() {
            let _ = fs::rename(&tmp, &p);
        }
    }
}

/// Двустороннее объединение карточек людей без потери фактов.
pub fn merge_people(incoming: HashMap<String, Person>) -> HashMap<String, Person> {
    let _g = LOCK.lock();
    let mut cur = load_people();
    for (raw_name, inc) in incoming {
        if is_ignored_person(&raw_name) {
            continue;
        }
        let key = norm(&raw_name);
        if is_ignored_person(&key) {
            continue;
        }
        let entry = cur.entry(key).or_default();
        for f in inc.facts {
            let fl = f.trim().to_lowercase();
            if !fl.is_empty() && !entry.facts.iter().any(|x| x.trim().to_lowercase() == fl) {
                entry.facts.push(f);
            }
        }
        for t in inc.topics {
            if !entry.topics.contains(&t) {
                entry.topics.push(t);
            }
        }
        entry.count = entry.count.max(inc.count);
        let inc_newer = inc.last_seen >= entry.last_seen;
        if inc.last_seen > entry.last_seen {
            entry.last_seen = inc.last_seen;
            entry.where_seen = inc.where_seen;
        }
        if entry.first_seen.is_empty() || (!inc.first_seen.is_empty() && inc.first_seen < entry.first_seen) {
            entry.first_seen = inc.first_seen;
        }
        if inc.anger_since > entry.anger_since {
            entry.anger = inc.anger;
            entry.anger_since = inc.anger_since;
        }
        if inc.rating != 0 && (entry.rating == 0 || inc_newer) {
            entry.rating = inc.rating.clamp(-100, 100);
        }
    }
    save_people(&cur);
    cur
}

pub fn rating(name: &str) -> i32 {
    load_people()
        .get(&norm(name))
        .map(|p| p.rating.clamp(-100, 100))
        .unwrap_or(0)
}

pub fn set_rating(name: &str, value: i32) -> i32 {
    if is_ignored_person(name) {
        return 0;
    }
    let key = norm(name);
    let val = value.clamp(-100, 100);
    let _g = LOCK.lock();
    let mut ps = load_people();
    let p = ps.entry(key).or_default();
    p.rating = val;
    if p.last_seen.is_empty() {
        p.last_seen = now_iso_min();
    }
    save_people(&ps);
    val
}

pub fn add_rating(name: &str, delta: i32) -> i32 {
    if is_ignored_person(name) {
        return 0;
    }
    let key = norm(name);
    let _g = LOCK.lock();
    let mut ps = load_people();
    let p = ps.entry(key).or_default();
    let next = (p.rating + delta).clamp(-100, 100);
    p.rating = next;
    p.last_seen = now_iso_min();
    save_people(&ps);
    next
}

pub fn rating_label(r: i32) -> &'static str {
    match r.clamp(-100, 100) {
        -100..=-60 => "враг народа (презираешь)",
        -59..=-25 => "недолюбливаешь (холодная и язвительная)",
        -24..=-1 => "слегка недовольна (прохладная)",
        0 => "безразличная (нейтральная)",
        1..=24 => "симпатия (дружелюбная)",
        25..=59 => "свой человек (тёплая и весёлая)",
        _ => "любимчик (обожаешь и всегда поддерживаешь)",
    }
}

/// Двустороннее объединение сводок дней.
pub fn merge_days(incoming: BTreeMap<String, String>) -> BTreeMap<String, String> {
    let _g = LOCK.lock();
    let mut cur = load_days();
    for (d, txt) in incoming {
        let replace = cur.get(&d).map(|old| txt.len() > old.len()).unwrap_or(true);
        if replace && !txt.trim().is_empty() {
            cur.insert(d, txt);
        }
    }
    save_days(&cur);
    cur
}

pub fn log(source: &str, who: &str, text: &str, reply: &str) {
    if text.is_empty() && reply.is_empty() {
        return;
    }
    let _g = LOCK.lock();
    let rec = FeedRecord {
        t: Local::now().format("%H:%M:%S").to_string(),
        source: source.to_string(),
        who: norm(who),
        said: text.chars().take(2000).collect(),
        reply: reply.chars().take(2000).collect(),
    };
    let dir = feed_dir();
    let _ = fs::create_dir_all(&dir);
    let file = dir.join(format!("{}.jsonl", Local::now().format("%Y-%m-%d")));
    if let Ok(line) = serde_json::to_string(&rec) {
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(file) {
            let _ = writeln!(f, "{line}");
        }
    }
    let key = norm(who);
    if is_ignored_person(&key) {
        return;
    }
    let mut ps = load_people();
    let entry = ps.entry(key).or_insert_with(|| Person {
        first_seen: now_iso_min(),
        ..Default::default()
    });
    entry.last_seen = now_iso_min();
    entry.where_seen = source.to_string();
    entry.count += 1;
    save_people(&ps);
}

pub fn anger(name: &str) -> i32 {
    let _g = LOCK.lock();
    let mut ps = load_people();
    let key = norm(name);
    let Some(p) = ps.get_mut(&key) else { return 0 };
    if p.anger > 0 && p.anger_since > 0.0 {
        let cooled = ((now_sec() - p.anger_since) / 600.0).floor() as i32;
        if cooled > 0 {
            p.anger = (p.anger - cooled).max(0);
            p.anger_since = if p.anger > 0 { now_sec() } else { 0.0 };
            let lvl = p.anger;
            save_people(&ps);
            return lvl;
        }
    }
    p.anger
}

pub fn set_anger(name: &str, level: i32) -> i32 {
    let _g = LOCK.lock();
    let mut ps = load_people();
    let key = norm(name);
    let lvl = level.clamp(0, 5);
    let entry = ps.entry(key).or_insert_with(|| Person {
        first_seen: now_iso_min(),
        ..Default::default()
    });
    entry.anger = lvl;
    entry.anger_since = if lvl > 0 { now_sec() } else { 0.0 };
    save_people(&ps);
    lvl
}

pub fn remember_about(name: &str, fact: &str) -> bool {
    let fact = fact.trim().trim_end_matches('.');
    if fact.is_empty() {
        return false;
    }
    let _g = LOCK.lock();
    let mut ps = load_people();
    let key = norm(name);
    let entry = ps.entry(key).or_insert_with(|| Person {
        first_seen: now_iso_min(),
        ..Default::default()
    });
    let low = fact.to_lowercase();
    if entry.facts.iter().any(|f| {
        let fl = f.to_lowercase();
        fl == low || fl.contains(&low)
    }) {
        return false;
    }
    entry.facts.push(fact.to_string());
    if entry.facts.len() > 40 {
        let drop_n = entry.facts.len() - 40;
        entry.facts.drain(0..drop_n);
    }
    save_people(&ps);
    true
}

pub fn forget_about(name: &str, what: &str) -> usize {
    let _g = LOCK.lock();
    let mut ps = load_people();
    let key = norm(name);
    let Some(entry) = ps.get_mut(&key) else { return 0 };
    let w = what.trim().to_lowercase();
    let before = entry.facts.len();
    entry.facts.retain(|f| !f.to_lowercase().contains(&w));
    let removed = before - entry.facts.len();
    if removed > 0 {
        save_people(&ps);
    }
    removed
}

pub fn feed_today() -> Vec<FeedRecord> {
    let file = feed_dir().join(format!("{}.jsonl", Local::now().format("%Y-%m-%d")));
    let Ok(raw) = fs::read_to_string(file) else { return Vec::new() };
    raw.lines().filter_map(|l| serde_json::from_str::<FeedRecord>(l).ok()).collect()
}

pub fn context(source: &str, today_lines: usize, n_days: usize) -> String {
    let mut parts = Vec::new();
    let days = load_days();
    if !days.is_empty() {
        let today = Local::now().date_naive();
        let last: Vec<_> = days.iter().rev().take(n_days).collect();
        let mut lines = Vec::new();
        for (d, summary) in last.into_iter().rev() {
            let lbl = NaiveDate::parse_from_str(d, "%Y-%m-%d")
                .map(|nd| match (today - nd).num_days() {
                    1 => "Вчера".to_string(),
                    2 => "Позавчера".to_string(),
                    k => format!("{d} ({k} дн. назад)"),
                })
                .unwrap_or_else(|_| d.clone());
            lines.push(format!("— {lbl}: {summary}"));
        }
        parts.push(format!("Что было в прошлые дни:\n{}", lines.join("\n")));
    }

    let recs = feed_today();
    let other: Vec<_> = recs.iter().filter(|r| r.source != source).collect();
    if !other.is_empty() {
        let tail = other.iter().rev().take(today_lines).rev();
        let mut lines = Vec::new();
        for r in tail {
            let t = r.t.get(..5).unwrap_or(&r.t);
            let mut s = format!("{t} [{}] {}: {}", r.source, r.who, r.said.chars().take(180).collect::<String>());
            if !r.reply.is_empty() {
                s.push_str(&format!(" → {}", r.reply.chars().take(140).collect::<String>()));
            }
            lines.push(s);
        }
        parts.push(format!("Сегодня в других местах:\n{}", lines.join("\n")));
    }

    let ps = load_people();
    if let Some(owner) = ps.get(OWNER) {
        if !owner.facts.is_empty() {
            parts.push(format!("Что ты знаешь о хозяине (sitthome): {}", owner.facts.join("; ")));
        }
    }
    let rated: Vec<String> = ps
        .iter()
        .filter(|(_, p)| p.rating != 0 || !p.facts.is_empty())
        .map(|(n, p)| {
            let f = if p.facts.is_empty() { String::new() } else { format!(" ({})", p.facts.iter().take(3).cloned().collect::<Vec<_>>().join("; ")) };
            format!("{n}: рейтинг {:+}/100 [{}]{f}", p.rating, rating_label(p.rating))
        })
        .collect();
    if !rated.is_empty() {
        parts.push(format!("Социальный рейтинг и люди:\n{}", rated.join("\n")));
    }
    parts.join("\n\n")
}

pub fn search(query: &str) -> String {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return "Пустой запрос.".into();
    }
    let mut hits = Vec::new();
    for (d, s) in load_days().iter().rev() {
        if d.contains(&q) || s.to_lowercase().contains(&q) {
            hits.push(format!("[День {d}] {s}"));
        }
    }
    if let Ok(rd) = fs::read_dir(feed_dir()) {
        let mut files: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
        files.sort();
        for f in files.into_iter().rev().take(30) {
            let day = f.file_stem().and_then(|s| s.to_str()).unwrap_or("");
            if let Ok(raw) = fs::read_to_string(&f) {
                for line in raw.lines() {
                    if line.to_lowercase().contains(&q) {
                        if let Ok(r) = serde_json::from_str::<FeedRecord>(line) {
                            hits.push(format!("[{day} {} {}] {}: {}", r.t, r.source, r.who, r.said));
                            if hits.len() >= 30 {
                                break;
                            }
                        }
                    }
                }
            }
            if hits.len() >= 30 {
                break;
            }
        }
    }
    let _ = Duration::days(1);
    if hits.is_empty() {
        "В долгой памяти ничего не нашла.".into()
    } else {
        hits.join("\n")
    }
}
