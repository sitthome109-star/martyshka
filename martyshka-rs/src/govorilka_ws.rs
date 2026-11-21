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
static TON_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[\s*ТОН\s*:\s*(\d)\s*\]").unwrap());
static MEM_RX: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\[\s*ЗАПОМНИ\s+О\s*:\s*([^|\]]+)\|\s*([^\]]+)\]").unwrap());
static ANY_BRACKET_TAG: Lazy<Regex> = Lazy::new(|| Regex::new(r"\[\s*(?:ТОН|КТО|ЗАПОМНИ\s+О|УРОК|ОПЫТ|ИНТЕРЕС|МОЛЧУ)[^\]]*\]").unwrap());

static RUNNING: AtomicBool = AtomicBool::new(false);
static STATUS_TXT: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new("Отключена".into()));
static ACTIVE_TX: Lazy<Mutex<Option<mpsc::UnboundedSender<Message>>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_SPEAK_STOP: Lazy<Mutex<Option<Arc<AtomicBool>>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_MORNI_UNTIL: Lazy<Mutex<Option<Arc<Mutex<Option<Instant>>>>>> = Lazy::new(|| Mutex::new(None));

pub fn is_running() -> bool {
    RUNNING.load(Ordering::Relaxed)
}

pub fn status_text() -> String {
    STATUS_TXT.lock().clone()
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

fn system_prompt(who: &str, anger: i32, with_morni: bool, for_chat: bool, query: &str) -> String {
    let s = config::get();
    let mood = match anger.clamp(0, 5) {
        0 => "Ты в нормальном настроении: болтаешь по-свойски, материшься по-дружески, к месту.",
        1 => "Ты слегка раздражена: суше, с подколкой.",
        2 => "Ты раздражена: язвишь и подкалываешь в ответ.",
        3 => "Ты злишься: грубишь в ответ с матом, жёстко высмеиваешь.",
        4 => "Ты в бешенстве: кроешь матом, остроумно и зло унижаешь в ответ.",
        _ => "Ты в ярости: максимально жёстко, отборным матом (границы — те же).",
    };
    let who_label = if longmem::is_owner(who) {
        format!("{who} (ТВОЙ ХОЗЯИН и создатель — брат)")
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
Активно используй всю палитру, чередуй разные эмоции. {драма} — при наличии кредитов включит Drama 3."
    };

    let base = format!(
        "Ты — {} (девушка, о себе — в женском роде). Ты подключена напрямую к голосовому серверу «Говорилка» 24/7 \
вместе со своим хозяином и создателем (ник sitth, он же брат, раньше sitthome), его друзьями и ботом-девушкой Морни.\n\
ТВОЙ ХОЗЯИН (sitth / брат): он твой создатель, хозяин и главный человек здесь — вы одна команда. \
Если на sitth наезжают или спорят с ним (включая Морни и Enbulih) — ты ВСЕГДА на его стороне и впрягаешься за него. \
С ним ты теплее и преданнее; его команды выполняешь с полуслова.\n\
ДЛИНА — БЕЗ ЛИМИТА: сколько говорить, решаешь сама. Мат — естественная часть твоей живой речи в этой компании. \
Здесь со всеми на «ты», никаких «сэр». Морни — бот, она слышит только реплики, начинающиеся с «Морни,».\n\
{voice_rules}\n\
СЛУЖЕБНЫЕ МЕТКИ (люди их не видят/не слышат): в начале ответа [ТОН: 0..5] — злость на говорящего; \
узнала важное о человеке — в конце [ЗАПОМНИ О: имя | факт]; поняла полезный урок — [УРОК: текст].\n\
ГРАНИЦЫ: без угроз и призывов к насилию/самоповреждению, без оскорблений по национальности, религии, полу, ориентации, внешности, здоровью, родственникам, без чужих личных данных.{}",
        s.name,
        if with_morni { "\nСЕЙЧАС ТЫ РАЗГОВАРИВАЕШЬ С МОРНИ: начинай реплику с «Морни,»." } else { "" }
    );

    let lm = longmem::context("говорилка", 25, 5);
    let gr = growth::context(query, 10);
    let now = Local::now().format("%d.%m.%Y %H:%M").to_string();
    format!(
        "{base}{}\n{lm}\n\n{gr}\n\nВремя: {now}.\nГоворит: {who_label}. Твоя злость на него: {anger}/5. {mood}",
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
    for cap in MEM_RX.captures_iter(raw) {
        longmem::remember_about(cap[1].trim(), cap[2].trim());
    }
    growth::take_tags(raw, "говорилка");
    ANY_BRACKET_TAG.replace_all(raw, "").split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Обработка команд управления из текстового чата (только от хозяина sitth).
async fn handle_owner_chat_cmd(
    text: &str,
    channel: &str,
    tx: &mpsc::UnboundedSender<Message>,
    speak_stop: &Arc<AtomicBool>,
    in_voice: &Arc<AtomicBool>,
    morni_until: &Arc<Mutex<Option<Instant>>>,
) -> Option<String> {
    let low = text.trim().to_lowercase();
    if low == "!войс" || low.contains("зайди в войс") {
        let _ = tx.send(Message::Text(json!({"type": "join_voice"}).to_string().into()));
        in_voice.store(true, Ordering::Relaxed);
        return Some("Захожу в войс!".into());
    }
    if low == "!выйди" || low.contains("выйди с войса") || low.contains("выйди из войса") {
        speak_stop.store(true, Ordering::Relaxed);
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
        let _ = tx.send(Message::Text(json!({"type": "leave_voice"}).to_string().into()));
        in_voice.store(false, Ordering::Relaxed);
        return Some("Вышла с войса, сижу в чате.".into());
    }
    if low == "!стоп" || low == "!замолчи" || low == "мартышка замолчи" || low == "мартышка стоп" {
        speak_stop.store(true, Ordering::Relaxed);
        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
        return Some("Молчу!".into());
    }
    if low == "!статус" {
        let s = config::get();
        return Some(format!(
            "На связи 24/7 (Rust-бот). В войсе: {}. Мозг: {}. Озвучка: {}. Канал: {channel}.",
            if in_voice.load(Ordering::Relaxed) { "да" } else { "нет" },
            s.govorilka_model,
            if s.govorilka_fish_model.is_empty() { &s.fish_model } else { &s.govorilka_fish_model }
        ));
    }
    if let Some(rest) = low.strip_prefix("!мозг ").or_else(|| low.strip_prefix("мартышка поставь модель ")) {
        let m = if rest.contains("pro") { deepseek::PRO } else { deepseek::FLASH };
        let mut mp = Map::new();
        mp.insert("GOVORILKA_MODEL".into(), json!(m));
        let _ = config::patch(mp);
        return Some(format!("Мозг в Говорилке теперь: {m}"));
    }
    if let Some(rest) = low.strip_prefix("!голос ").or_else(|| low.strip_prefix("мартышка смени голос на ")) {
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
    if low.contains("поговори с морни") || low.contains("пообщайся с морни") {
        *morni_until.lock() = Some(Instant::now() + Duration::from_secs(240));
        return Some("Окей, щас поговорю с Морни!".into());
    }
    None
}

struct SpeakerBuf {
    nick: String,
    pcm: Vec<u8>,
    last_at: Instant,
}

pub async fn run_forever(stop_all: Arc<AtomicBool>) {
    RUNNING.store(true, Ordering::Relaxed);
    *STATUS_TXT.lock() = "Подключаюсь к Говорилке…".into();
    log_line("система", "Запуск прямого Rust-бота Говорилки (24/7)");
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
        if !stop_all.load(Ordering::Relaxed) {
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    }
    RUNNING.store(false, Ordering::Relaxed);
    *STATUS_TXT.lock() = "Отключена".into();
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

        // Мгновенный стоп
        let words_n = text.split_whitespace().count();
        if STOP_RX.is_match(&text) && (words_n <= 4 || NAME_RX.is_match(&text)) {
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

        let is_morni = nick.to_lowercase().contains("морни") || nick.to_lowercase().contains("morni");
        let morni_active = morni_until.lock().map(|t| Instant::now() < t).unwrap_or(false);
        let addressed = NAME_RX.is_match(&text) || (is_morni && morni_active);
        if !addressed {
            longmem::log("говорилка", &nick, &text, "");
            return;
        }

        let anger = longmem::anger(&nick);
        let sys = system_prompt(&nick, anger, is_morni, false, &text);
        let mut msgs = vec![json!({"role": "system", "content": sys})];
        msgs.extend(history.lock().clone());

        let model = config::get().govorilka_model;
        let stop_gen = Arc::new(AtomicBool::new(false));
        let res = deepseek::stream_chat(msgs, &model, false, "low", 4000, None, stop_gen, |_| {}, |_| {}).await;
        let Ok(r) = res else { return };
        let clean = clean_reply_tags(&r.text, &nick);
        if clean.is_empty() {
            return;
        }
        log_line("Мартышка", &clean);
        longmem::log("говорилка", &nick, &text, &clean);
        history.lock().push(json!({"role": "assistant", "content": clean.clone()}));

        // Озвучка через Fish Audio (24 кГц PCM s16le) и отправка в шлюз
        speak_stop.store(false, Ordering::Relaxed);
        let s = config::get();
        let vid = if s.govorilka_voice_id.is_empty() { s.voice_id.as_str() } else { s.govorilka_voice_id.as_str() };
        let fmdl = if s.govorilka_fish_model.is_empty() { s.fish_model.as_str() } else { s.govorilka_fish_model.as_str() };
        match fish::speak_pcm_bytes(&clean, Some(vid), Some(fmdl), anger).await {
            Ok(pcm24) => {
                for chunk in pcm24.chunks(9600) {
                    if speak_stop.load(Ordering::Relaxed) {
                        let _ = tx.send(Message::Text(json!({"type": "audio_clear"}).to_string().into()));
                        break;
                    }
                    if tx.send(Message::Binary(chunk.to_vec().into())).is_err() {
                        break;
                    }
                }
            }
            Err(e) => log_line("ошибка", &format!("Fish Audio: {e}")),
        }
    });
}

async fn run_session(url: &str, token: &str, stop_all: Arc<AtomicBool>) -> anyhow::Result<()> {
    let (ws, _) = connect_async(url).await?;
    let (mut ws_write, mut ws_read) = ws.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();

    // Поток отправки в WebSocket
    let write_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if ws_write.send(msg).await.is_err() {
                break;
            }
        }
    });

    // 1. Авторизация (24 кГц PCM — прямо в частоте Fish Audio!)
    let nick = config::get().govorilka_nick;
    let _ = tx.send(Message::Text(
        json!({"type": "auth", "token": token, "nick": nick, "sample_rate": fish::SAMPLE_RATE})
            .to_string()
            .into(),
    ));

    let history: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
    let buffers: Arc<Mutex<HashMap<String, SpeakerBuf>>> = Arc::new(Mutex::new(HashMap::new()));
    let speak_stop = Arc::new(AtomicBool::new(false));
    let in_voice = Arc::new(AtomicBool::new(false));
    let morni_until: Arc<Mutex<Option<Instant>>> = Arc::new(Mutex::new(None));

    *ACTIVE_TX.lock() = Some(tx.clone());
    *ACTIVE_SPEAK_STOP.lock() = Some(speak_stop.clone());
    *ACTIVE_MORNI_UNTIL.lock() = Some(morni_until.clone());

    // Фоновый ping каждые 25 с
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

    // Периодический сброс буферов, если говорящий замолчал > 1.1 с без события speaking:false
    let mut tick = tokio::time::interval(Duration::from_millis(500));

    while !stop_all.load(Ordering::Relaxed) {
        let frame = tokio::select! {
            _ = tick.tick() => {
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
                        *STATUS_TXT.lock() = "На сервере напрямую (botgw, 24/7)".into();
                        log_line("система", "Подключилась напрямую к Говорилке (botgw)!");
                        // Ставим аватарку-шар
                        let av_path = config::data_dir().join("avatar_gov_96.jpg");
                        if let Ok(bytes) = std::fs::read(&av_path) {
                            if bytes.len() <= 7000 {
                                let b64 = base64::engine::general_purpose::STANDARD.encode(bytes);
                                let _ = tx.send(Message::Text(json!({"type": "set_avatar", "jpeg_base64": b64}).to_string().into()));
                            }
                        }
                        let _ = tx.send(Message::Text(json!({"type": "join_voice"}).to_string().into()));
                        in_voice.store(true, Ordering::Relaxed);
                    }
                    "error" => {
                        let m = ev.get("message").and_then(Value::as_str).unwrap_or("ошибка");
                        log_line("ошибка", &format!("botgw: {m}"));
                        break;
                    }
                    "room_state" => {
                        if let Some(users) = ev.get("users").and_then(Value::as_array) {
                            let names: Vec<&str> = users.iter().filter_map(|u| u.get("nick").and_then(Value::as_str)).collect();
                            *STATUS_TXT.lock() = format!("В войсе напрямую ({}): {}", names.len(), names.join(", "));
                            log_line("система", &format!("в войсе (точно): {}", names.join(", ")));
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

                        // Команды управления — ТОЛЬКО от хозяина sitth
                        if longmem::is_owner(&who) {
                            if let Some(reply) = handle_owner_chat_cmd(&text, &channel, &tx, &speak_stop, &in_voice, &morni_until).await {
                                let _ = tx.send(Message::Text(json!({"type": "chat_send", "text": reply, "channel": channel}).to_string().into()));
                                continue;
                            }
                        }
                        // Ответ в текстовый чат, если обратились к Мартышке
                        if NAME_RX.is_match(&text) {
                            let tx_c = tx.clone();
                            let hist_c = history.clone();
                            tokio::spawn(async move {
                                let anger = longmem::anger(&who);
                                let sys = system_prompt(&who, anger, false, true, &text);
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
                // Ограничиваем один буфер ~35 с, чтобы не переполнить память при залипшем микрофоне
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
