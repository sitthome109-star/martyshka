//! Инструменты агента-программиста Мартышки на нативном Rust.
use crate::{config, growth, longmem};
use globset::{Glob, GlobSetBuilder};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use similar::TextDiff;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use walkdir::WalkDir;

const MAX_OUT: usize = 14_000;
const SKIP_DIRS: &[&str] = &[
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "target", ".next", ".cache", "models", "obj", "bin",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolOutput {
    pub text: String,
    pub ok: bool,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub file: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub diff: String,
}

impl ToolOutput {
    pub fn ok(text: impl Into<String>) -> Self {
        Self { text: text.into(), ok: true, file: String::new(), diff: String::new() }
    }
    pub fn err(text: impl Into<String>) -> Self {
        Self { text: text.into(), ok: false, file: String::new(), diff: String::new() }
    }
}

static UNDO_STACK: Lazy<Mutex<HashMap<String, Vec<PathBuf>>>> = Lazy::new(|| Mutex::new(HashMap::new()));
static DANGEROUS_CMD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(format\s+[a-z]:|rm\s+-rf\s+/|del\s+/s\s+/q\s+c:\\|shutdown\s+/s|reg\s+delete\s+hklm)").unwrap()
});

pub fn undo_dir() -> PathBuf {
    config::data_dir().join("studio").join("резерв").join("undo")
}

pub fn resolve_path(raw: &str) -> PathBuf {
    let trimmed = raw.trim().trim_matches('"');
    let p = PathBuf::from(trimmed);
    if p.is_absolute() { p } else { config::workspace().join(p) }
}

fn save_undo(p: &Path) {
    if !p.is_file() {
        return;
    }
    if let Ok(meta) = p.metadata() {
        if meta.len() > 4_000_000 {
            return;
        }
    }
    let dir = undo_dir();
    let _ = fs::create_dir_all(&dir);
    let ts = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
    let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("file");
    let snap = dir.join(format!("{ts}_{name}"));
    if let Ok(bytes) = fs::read(p) {
        if fs::write(&snap, bytes).is_ok() {
            let key = p.canonicalize().unwrap_or_else(|_| p.to_path_buf()).to_string_lossy().to_lowercase();
            let mut g = UNDO_STACK.lock();
            let stack = g.entry(key).or_default();
            stack.push(snap);
            if stack.len() > 10 {
                let old = stack.remove(0);
                let _ = fs::remove_file(old);
            }
        }
    }
}

pub fn is_read_only(name: &str) -> bool {
    matches!(
        name,
        "read_file" | "list_files" | "find_files" | "grep_code" | "glob_files" | "project_map" | "memory_search" | "web_search" | "web_fetch"
    )
}

pub fn is_write_tool(name: &str) -> bool {
    matches!(name, "write_file" | "edit_file")
}

pub fn is_verify_tool(name: &str) -> bool {
    matches!(name, "check_code" | "run_command" | "http_request")
}

pub fn label(name: &str, args: &Value) -> String {
    let verb = match name {
        "plan" => "Составляю план",
        "project_map" => "Изучаю проект",
        "check_code" => "Проверяю код",
        "read_file" => "Читаю файл",
        "write_file" => "Пишу файл",
        "edit_file" => "Правлю файл",
        "undo_edit" => "Откатываю правку",
        "list_files" => "Смотрю папку",
        "find_files" => "Ищу файлы",
        "grep_code" => "Ищу в коде",
        "glob_files" => "Ищу файлы",
        "run_command" => "Исполняю команду",
        "git_command" => "Git",
        "http_request" => "HTTP-запрос",
        "web_search" => "Ищу в интернете",
        "web_fetch" => "Читаю страницу",
        "open_path" => "Открываю",
        "memory_search" => "Вспоминаю",
        "learn" => "Учусь",
        "remember" => "Запоминаю",
        _ => name,
    };
    let detail = ["path", "pattern", "query", "command", "args", "url", "name", "lesson", "fact"]
        .iter()
        .find_map(|k| args.get(*k).and_then(Value::as_str))
        .unwrap_or("");
    if detail.is_empty() {
        verb.to_string()
    } else {
        let short: String = detail.replace('\n', " ").chars().take(80).collect();
        format!("{verb}: {short}")
    }
}

pub fn tool_schemas() -> Vec<Value> {
    let f = |name: &str, desc: &str, params: Value| {
        json!({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
    };
    vec![
        f("plan", "План задачи с галочками (items: [{step, status}] или done: [номера с 1]).", json!({
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object", "properties": {"step": {"type": "string"}, "status": {"type": "string"}}, "required": ["step", "status"]}},
                "done": {"type": "array", "items": {"type": "integer"}}
            }
        })),
        f("project_map", "Карта проекта за 1 шаг: стек, дерево файлов, классы и функции.", json!({
            "type": "object", "properties": {"path": {"type": "string"}, "max_files": {"type": "integer"}}
        })),
        f("check_code", "Проверить синтаксис проекта (Rust cargo check, Python, JSON, JS) и запустить тесты.", json!({
            "type": "object", "properties": {"path": {"type": "string"}}
        })),
        f("read_file", "Прочитать текстовый файл с номерами строк.", json!({
            "type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["path"]
        })),
        f("write_file", "Создать или перезаписать файл целиком.", json!({
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]
        })),
        f("edit_file", "Заменить точный кусок old на new в файле (возвращает diff).", json!({
            "type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "replace_all": {"type": "boolean"}}, "required": ["path", "old", "new"]
        })),
        f("undo_edit", "Откатить последнюю правку файла.", json!({
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]
        })),
        f("list_files", "Список файлов и папок.", json!({
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]
        })),
        f("find_files", "Найти файлы по части имени в папке проектов и документах.", json!({
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]
        })),
        f("grep_code", "Поиск регулярным выражением по содержимому файлов.", json!({
            "type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}, "context": {"type": "integer"}}, "required": ["pattern"]
        })),
        f("glob_files", "Найти файлы по glob-шаблону (например **/*.rs, **/*.py).", json!({
            "type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]
        })),
        f("run_command", "Выполнить команду в PowerShell / shell.", json!({
            "type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command"]
        })),
        f("git_command", "Выполнить команду git (status, diff, log, add, commit...).", json!({
            "type": "object", "properties": {"args": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["args"]
        })),
        f("http_request", "Выполнить HTTP-запрос к API или локальному серверу.", json!({
            "type": "object", "properties": {"method": {"type": "string"}, "url": {"type": "string"}, "body": {"type": "string"}}, "required": ["method", "url"]
        })),
        f("web_search", "Поиск в интернете (DuckDuckGo).", json!({
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]
        })),
        f("web_fetch", "Прочитать текст веб-страницы по URL.", json!({
            "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]
        })),
        f("open_path", "Открыть файл, папку или URL программой по умолчанию.", json!({
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]
        })),
        f("memory_search", "Поиск в долгой памяти Мартышки.", json!({
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]
        })),
        f("learn", "Записать новый урок в опыт Мартышки.", json!({
            "type": "object", "properties": {"lesson": {"type": "string"}}, "required": ["lesson"]
        })),
        f("remember", "Запомнить важный факт о хозяине или его ПК.", json!({
            "type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]
        })),
    ]
}

fn should_skip(entry: &walkdir::DirEntry) -> bool {
    if entry.depth() == 0 {
        return false;
    }
    let name = entry.file_name().to_string_lossy();
    entry.file_type().is_dir() && (SKIP_DIRS.contains(&name.as_ref()) || name.starts_with('.'))
}

fn extract_symbols(path: &Path) -> Vec<String> {
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    let Ok(text) = fs::read_to_string(path) else { return Vec::new() };
    let rx_str = match ext.as_str() {
        "rs" => r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)",
        "py" => r"(?m)^\s*(?:async\s+)?(?:def|class)\s+(\w+)",
        "js" | "ts" | "jsx" | "tsx" => r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+(\w+)",
        "cs" | "java" | "go" => r"(?m)^\s*(?:public|private|internal|func)?\s*(?:static\s+)?(?:class|struct|interface|func)\s+(\w+)",
        _ => return Vec::new(),
    };
    let Ok(rx) = Regex::new(rx_str) else { return Vec::new() };
    rx.captures_iter(&text)
        .take(12)
        .filter_map(|c| c.get(1).map(|m| m.as_str().to_string()))
        .collect()
}

pub async fn run_tool(name: &str, args: &Value) -> ToolOutput {
    match name {
        "plan" => ToolOutput::ok("План обновлён."),
        "project_map" => {
            let root = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or("."));
            if !root.exists() {
                return ToolOutput::err(format!("Нет такой папки: {}", root.display()));
            }
            let max_f = args.get("max_files").and_then(Value::as_u64).unwrap_or(120).clamp(10, 400) as usize;
            let mut stack = Vec::new();
            if root.join("Cargo.toml").exists() { stack.push("Rust"); }
            if root.join("package.json").exists() { stack.push("Node.js"); }
            if root.join(".git").exists() { stack.push("git"); }
            if root.join("requirements.txt").exists() || root.join("pyproject.toml").exists() { stack.push("Python"); }
            let mut lines = vec![format!("Проект: {}", root.display())];
            if !stack.is_empty() {
                lines.push(format!("Стек: {}", stack.join(", ")));
            }
            let mut count = 0;
            for e in WalkDir::new(&root).into_iter().filter_entry(|e| !should_skip(e)).flatten() {
                if !e.file_type().is_file() {
                    continue;
                }
                count += 1;
                if count > max_f {
                    continue;
                }
                let rel = e.path().strip_prefix(&root).unwrap_or(e.path()).to_string_lossy().replace('\\', "/");
                let sz = e.metadata().map(|m| m.len()).unwrap_or(0);
                let sz_s = if sz >= 1024 { format!("{:.1} КБ", sz as f64 / 1024.0) } else { format!("{sz} Б") };
                let syms = extract_symbols(e.path());
                if syms.is_empty() {
                    lines.push(format!("  {rel} ({sz_s})"));
                } else {
                    lines.push(format!("  {rel} ({sz_s})  →  {}", syms.join(", ")));
                }
            }
            if count > max_f {
                lines.push(format!("  … и ещё {} файлов", count - max_f));
            }
            ToolOutput::ok(lines.join("\n").chars().take(MAX_OUT).collect::<String>())
        }
        "list_files" => {
            let p = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or("."));
            let Ok(rd) = fs::read_dir(&p) else { return ToolOutput::err(format!("Нет такой папки: {}", p.display())) };
            let mut entries: Vec<_> = rd.flatten().collect();
            entries.sort_by_key(|e| (e.path().is_file(), e.file_name().to_string_lossy().to_lowercase()));
            let mut lines = vec![p.display().to_string()];
            for e in entries.into_iter().take(300) {
                let is_f = e.path().is_file();
                let icon = if is_f { "📄" } else { "📁" };
                let nm = e.file_name().to_string_lossy().to_string();
                if is_f {
                    let sz = e.metadata().map(|m| m.len()).unwrap_or(0);
                    lines.push(format!("{icon} {nm}  ({sz} байт)"));
                } else {
                    lines.push(format!("{icon} {nm}"));
                }
            }
            ToolOutput::ok(lines.join("\n"))
        }
        "find_files" => {
            let needle = args.get("name").and_then(Value::as_str).unwrap_or("").to_lowercase();
            let mut found = Vec::new();
            for e in WalkDir::new(config::workspace()).into_iter().filter_entry(|e| !should_skip(e)).flatten() {
                if e.file_name().to_string_lossy().to_lowercase().contains(&needle) {
                    found.push(e.path().display().to_string());
                    if found.len() >= 40 { break; }
                }
            }
            if found.is_empty() { ToolOutput::err("Ничего не нашла.") } else { ToolOutput::ok(found.join("\n")) }
        }
        "read_file" => {
            let p = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or(""));
            let Ok(raw) = fs::read_to_string(&p) else { return ToolOutput::err(format!("Не удалось прочитать: {}", p.display())) };
            let lines: Vec<&str> = raw.lines().collect();
            let off = args.get("offset").and_then(Value::as_u64).unwrap_or(1).max(1) as usize;
            let lim = args.get("limit").and_then(Value::as_u64).unwrap_or(400) as usize;
            let start = (off - 1).min(lines.len());
            let end = (start + lim).min(lines.len());
            let mut body: String = lines[start..end]
                .iter()
                .enumerate()
                .map(|(i, l)| format!("{:>5}  {l}", start + i + 1))
                .collect::<Vec<_>>()
                .join("\n");
            if end < lines.len() {
                body.push_str(&format!("\n… всего строк: {}", lines.len()));
            }
            ToolOutput::ok(body.chars().take(MAX_OUT * 2).collect::<String>())
        }
        "write_file" => {
            let p = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or(""));
            let content = args.get("content").and_then(Value::as_str).unwrap_or("");
            if let Some(parent) = p.parent() {
                let _ = fs::create_dir_all(parent);
            }
            save_undo(&p);
            if let Err(e) = fs::write(&p, content) {
                return ToolOutput::err(format!("Ошибка записи: {e}"));
            }
            let syn_err = if p.extension().and_then(|s| s.to_str()) == Some("json") {
                serde_json::from_str::<Value>(content).err().map(|e| format!("\n⚠ ОШИБКА JSON: {e}"))
            } else {
                None
            };
            let ok = syn_err.is_none();
            ToolOutput {
                text: format!("Записано: {} ({} симв.).{}", p.display(), content.chars().count(), syn_err.unwrap_or_default()),
                ok,
                file: p.display().to_string(),
                diff: String::new(),
            }
        }
        "edit_file" => {
            let p = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or(""));
            let Ok(raw) = fs::read_to_string(&p) else { return ToolOutput::err(format!("Нет файла: {}", p.display())) };
            let crlf = raw.contains("\r\n");
            let text = if crlf { raw.replace("\r\n", "\n") } else { raw };
            let old = args.get("old").and_then(Value::as_str).unwrap_or("").replace("\r\n", "\n");
            let new = args.get("new").and_then(Value::as_str).unwrap_or("").replace("\r\n", "\n");
            let replace_all = args.get("replace_all").and_then(Value::as_bool).unwrap_or(false);
            let count = text.matches(&old).count();
            if count == 0 || (count > 1 && !replace_all) {
                return ToolOutput::err(format!("Кусок old найден {count} раз(а), а нужно ровно 1 (или replace_all=true)."));
            }
            save_undo(&p);
            let updated = if replace_all { text.replace(&old, &new) } else { text.replacen(&old, &new, 1) };
            let diff = TextDiff::from_lines(&text, &updated)
                .unified_diff()
                .context_radius(2)
                .header(p.file_name().and_then(|s| s.to_str()).unwrap_or("file"), "")
                .to_string();
            let diff_short: String = diff.lines().take(40).collect::<Vec<_>>().join("\n");
            let final_out = if crlf { updated.replace('\n', "\r\n") } else { updated };
            if let Err(e) = fs::write(&p, final_out) {
                return ToolOutput::err(format!("Ошибка записи: {e}"));
            }
            ToolOutput {
                text: format!("Исправлено ({count} место): {}", p.display()),
                ok: true,
                file: p.display().to_string(),
                diff: diff_short,
            }
        }
        "undo_edit" => {
            let p = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or(""));
            let key = p.canonicalize().unwrap_or_else(|_| p.clone()).to_string_lossy().to_lowercase();
            let snap = UNDO_STACK.lock().get_mut(&key).and_then(Vec::pop);
            let Some(snap) = snap else { return ToolOutput::err("Нет сохранённой предыдущей версии.") };
            match fs::read(&snap).and_then(|b| fs::write(&p, b)) {
                Ok(_) => {
                    let _ = fs::remove_file(snap);
                    ToolOutput { text: format!("Откатила правку: {}", p.display()), ok: true, file: p.display().to_string(), diff: String::new() }
                }
                Err(e) => ToolOutput::err(format!("Не удалось откатить: {e}")),
            }
        }
        "grep_code" => {
            let root = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or("."));
            let pat = args.get("pattern").and_then(Value::as_str).unwrap_or("");
            let Ok(rx) = Regex::new(&format!("(?i){pat}")) else { return ToolOutput::err("Плохое регулярное выражение.") };
            let ctx_n = args.get("context").and_then(Value::as_u64).unwrap_or(0).min(5) as usize;
            let mut hits = Vec::new();
            for e in WalkDir::new(&root).into_iter().filter_entry(|e| !should_skip(e)).flatten() {
                if !e.file_type().is_file() || e.metadata().map(|m| m.len()).unwrap_or(0) > 2_000_000 {
                    continue;
                }
                let Ok(raw) = fs::read_to_string(e.path()) else { continue };
                let lines: Vec<&str> = raw.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    if rx.is_match(line) {
                        if ctx_n > 0 {
                            let lo = i.saturating_sub(ctx_n);
                            let hi = (i + ctx_n + 1).min(lines.len());
                            let blk: Vec<String> = (lo..hi)
                                .map(|j| format!("{}:{}{} {}", e.path().display(), j + 1, if j == i { '>' } else { ':' }, lines[j]))
                                .collect();
                            hits.push(blk.join("\n"));
                        } else {
                            hits.push(format!("{}:{}: {}", e.path().display(), i + 1, line.trim()));
                        }
                        if hits.len() >= 150 { break; }
                    }
                }
                if hits.len() >= 150 { break; }
            }
            if hits.is_empty() { ToolOutput::err("Ничего не нашла.") } else { ToolOutput::ok(hits.join("\n").chars().take(MAX_OUT).collect::<String>()) }
        }
        "glob_files" => {
            let root = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or("."));
            let pat = args.get("pattern").and_then(Value::as_str).unwrap_or("*");
            let mut b = GlobSetBuilder::new();
            if let Ok(g) = Glob::new(pat) { b.add(g); }
            if let Some(sub) = pat.strip_prefix("**/") {
                if let Ok(g) = Glob::new(sub) { b.add(g); }
            }
            let Ok(set) = b.build() else { return ToolOutput::err("Плохой glob-шаблон.") };
            let mut found = Vec::new();
            for e in WalkDir::new(&root).into_iter().filter_entry(|e| !should_skip(e)).flatten() {
                if !e.file_type().is_file() { continue; }
                let rel = e.path().strip_prefix(&root).unwrap_or(e.path()).to_string_lossy().replace('\\', "/");
                if set.is_match(&rel) {
                    found.push(e.path().display().to_string());
                    if found.len() >= 250 { break; }
                }
            }
            if found.is_empty() { ToolOutput::err("Ничего не нашла.") } else { ToolOutput::ok(found.join("\n")) }
        }
        "run_command" => {
            let cmd = args.get("command").and_then(Value::as_str).unwrap_or("");
            if DANGEROUS_CMD.is_match(cmd) {
                return ToolOutput::err("Опасная команда заблокирована.");
            }
            let cwd = resolve_path(args.get("cwd").and_then(Value::as_str).unwrap_or("."));
            let out = if cfg!(windows) {
                Command::new("powershell")
                    .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &format!("[Console]::OutputEncoding=[Text.Encoding]::UTF8; {cmd}")])
                    .current_dir(&cwd)
                    .output()
            } else {
                Command::new("sh").args(["-c", cmd]).current_dir(&cwd).output()
            };
            match out {
                Ok(r) => {
                    let s = format!("Код выхода {}\n{}{}", r.status.code().unwrap_or(-1),
                        String::from_utf8_lossy(&r.stdout),
                        if r.stderr.is_empty() { String::new() } else { format!("\nОШИБКИ:\n{}", String::from_utf8_lossy(&r.stderr)) });
                    ToolOutput { text: s.chars().take(MAX_OUT).collect(), ok: r.status.success(), file: String::new(), diff: String::new() }
                }
                Err(e) => ToolOutput::err(format!("Команда не запустилась: {e}")),
            }
        }
        "git_command" => {
            let cwd = resolve_path(args.get("cwd").and_then(Value::as_str).unwrap_or("."));
            let raw = args.get("args").and_then(Value::as_str).unwrap_or("status");
            let parts: Vec<&str> = raw.split_whitespace().collect();
            match Command::new("git").arg("-c").arg("core.quotepath=false").args(parts).current_dir(&cwd).output() {
                Ok(r) => {
                    let s = format!("Код выхода {}\n{}{}", r.status.code().unwrap_or(-1),
                        String::from_utf8_lossy(&r.stdout), String::from_utf8_lossy(&r.stderr));
                    ToolOutput { text: s.chars().take(MAX_OUT).collect(), ok: r.status.success(), file: String::new(), diff: String::new() }
                }
                Err(e) => ToolOutput::err(format!("git не запустился: {e}")),
            }
        }
        "check_code" => {
            let root = resolve_path(args.get("path").and_then(Value::as_str).unwrap_or("."));
            if root.join("Cargo.toml").exists() {
                match Command::new("cargo").args(["test"]).current_dir(&root).output() {
                    Ok(r) => {
                        let s = format!("{}{}", String::from_utf8_lossy(&r.stdout), String::from_utf8_lossy(&r.stderr));
                        return ToolOutput { text: s.chars().take(MAX_OUT).collect(), ok: r.status.success(), file: String::new(), diff: String::new() };
                    }
                    Err(e) => return ToolOutput::err(format!("cargo не запустился: {e}")),
                }
            }
            let mut errs = Vec::new();
            let mut checked = 0;
            for e in WalkDir::new(&root).into_iter().filter_entry(|e| !should_skip(e)).flatten() {
                if e.path().extension().and_then(|s| s.to_str()) == Some("json") {
                    checked += 1;
                    if let Ok(raw) = fs::read_to_string(e.path()) {
                        if let Err(err) = serde_json::from_str::<Value>(&raw) {
                            errs.push(format!("JSON {}: {err}", e.path().display()));
                        }
                    }
                }
            }
            if errs.is_empty() {
                ToolOutput::ok(format!("✓ Проверено JSON: {checked}, ошибок нет."))
            } else {
                ToolOutput::err(errs.join("\n"))
            }
        }
        "http_request" => {
            let method = args.get("method").and_then(Value::as_str).unwrap_or("GET").to_uppercase();
            let url = args.get("url").and_then(Value::as_str).unwrap_or("");
            let client = reqwest::Client::new();
            let Ok(m) = reqwest::Method::from_bytes(method.as_bytes()) else { return ToolOutput::err("Неизвестный HTTP-метод") };
            let mut req = client.request(m, url);
            if let Some(body) = args.get("body").and_then(Value::as_str) {
                req = req.body(body.to_string());
            }
            match req.send().await {
                Ok(r) => {
                    let st = r.status().as_u16();
                    let body = r.text().await.unwrap_or_default();
                    ToolOutput { text: format!("HTTP {st}\n\n{}", body.chars().take(MAX_OUT).collect::<String>()), ok: st < 400, file: String::new(), diff: String::new() }
                }
                Err(e) => ToolOutput::err(format!("Запрос не прошёл: {e}")),
            }
        }
        "web_search" => {
            let q = args.get("query").and_then(Value::as_str).unwrap_or("");
            let url = format!("https://html.duckduckgo.com/html/?q={}", urlencoding::encode(q));
            let client = reqwest::Client::builder().user_agent("Mozilla/5.0").build().unwrap();
            match client.get(&url).send().await {
                Ok(r) => {
                    let html = r.text().await.unwrap_or_default();
                    let rx = Regex::new(r#"(?s)class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>"#).unwrap();
                    let tag_rx = Regex::new(r"<[^>]+>").unwrap();
                    let mut out = Vec::new();
                    for cap in rx.captures_iter(&html).take(8) {
                        let title = tag_rx.replace_all(&cap[2], "").trim().to_string();
                        out.push(format!("{title}\n{}", &cap[1]));
                    }
                    if out.is_empty() { ToolOutput::err("Ничего не нашлось.") } else { ToolOutput::ok(out.join("\n\n")) }
                }
                Err(e) => ToolOutput::err(format!("Поиск недоступен: {e}")),
            }
        }
        "web_fetch" => {
            let url = args.get("url").and_then(Value::as_str).unwrap_or("");
            let client = reqwest::Client::builder().user_agent("Mozilla/5.0").build().unwrap();
            match client.get(url).send().await {
                Ok(r) => {
                    let html = r.text().await.unwrap_or_default();
                    let tag_rx = Regex::new(r"(?s)<(script|style)[^>]*>.*?</\1>|<[^>]+>").unwrap();
                    let clean = tag_rx.replace_all(&html, " ").split_whitespace().collect::<Vec<_>>().join(" ");
                    ToolOutput::ok(clean.chars().take(MAX_OUT).collect::<String>())
                }
                Err(e) => ToolOutput::err(format!("Не открылось: {e}")),
            }
        }
        "open_path" => {
            let target = args.get("path").and_then(Value::as_str).unwrap_or("");
            let _ = if cfg!(windows) {
                Command::new("cmd").args(["/C", "start", "", target]).spawn()
            } else {
                Command::new("xdg-open").arg(target).spawn()
            };
            ToolOutput::ok("Открыла.")
        }
        "memory_search" => ToolOutput::ok(longmem::search(args.get("query").and_then(Value::as_str).unwrap_or(""))),
        "learn" => {
            let added = growth::add(args.get("lesson").and_then(Value::as_str).unwrap_or(""), "чат", "сама");
            ToolOutput::ok(if added { "Записала в опыт." } else { "Такой урок уже есть — подтвердила." })
        }
        "remember" => {
            let ok = longmem::remember_about(longmem::OWNER, args.get("fact").and_then(Value::as_str).unwrap_or(""));
            ToolOutput::ok(if ok { "Запомнила." } else { "Уже помню это." })
        }
        _ => ToolOutput::err(format!("Неизвестный инструмент: {name}")),
    }
}
