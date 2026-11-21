//! Схема всех вкладок настроек для окна Мартышки (совместима с ui/index.html).
use crate::{config, deepseek, fish};
use serde_json::{json, Value};

pub fn connectors_list() -> Vec<Value> {
    let off = config::get().studio_disabled_groups;
    let web_on = config::get().web_enabled;
    let groups = [
        ("web", "Интернет", "Поиск и чтение страниц — чтобы не выдумывать факты", 2),
        ("files", "Файлы", "Читать, создавать и править файлы, искать по ПК", 7),
        ("dev", "Разработка", "Карта проекта, проверка кода, grep, glob, git, HTTP", 8),
        ("shell", "Команды", "PowerShell / терминал: запуск программ и скриптов", 1),
        ("memory", "Память", "Долгая память и саморазвитие", 3),
    ];
    groups
        .iter()
        .map(|(k, title, desc, n)| {
            let on = !off.iter().any(|x| x == k) && (*k != "web" || web_on);
            json!({"key": k, "title": title, "desc": desc, "on": on, "tools": n})
        })
        .collect()
}

pub async fn build_schema() -> Value {
    let s = config::get();
    let ds_bal = deepseek::balance().await;
    let fish_bal = fish::credits().await;
    let conns = connectors_list();

    json!({
        "sections": [
            {
                "key": "brain",
                "title": "Мозг (DeepSeek API)",
                "desc": "Думает в облаке — видеокарта не нужна.",
                "fields": [
                    {"key": "DEEPSEEK_KEY", "label": "Ключ DeepSeek API", "type": "password", "value": s.deepseek_key, "placeholder": "sk-..."},
                    {"key": "BRAIN_MODE", "label": "Режим мозга", "type": "select", "value": s.brain_mode,
                     "options": [["auto", "Авто (Flash + Pro)"], ["flash", "Только Flash (быстро)"], ["pro", "Только Pro (умнее)"]]},
                    {"key": "ECONOMY", "label": "Экономия (кэш DeepSeek)", "desc": "Кэширует начало инструкции и сжимает старые шаги.", "type": "toggle", "value": s.economy},
                    {"key": "_ds_bal", "label": "Баланс DeepSeek", "type": "info", "value": ds_bal}
                ]
            },
            {
                "key": "character",
                "title": "Характер и обращение",
                "desc": "Как Мартышка общается с тобой (хозяин: sitth).",
                "fields": [
                    {"key": "NAME", "label": "Имя помощника", "type": "text", "value": s.name},
                    {"key": "OWNER_NICKNAME", "label": "Как к тебе обращаться", "type": "text", "value": s.owner_nickname},
                    {"key": "PERSONALITY_MODE", "label": "Манера", "type": "select", "value": s.personality_mode,
                     "options": [["bro", "Свой в доску (на ты)"], ["jarvis", "Джарвис (на вы)"]]},
                    {"key": "PERSONALITY_EXTRA", "label": "Дополнительные пожелания к характеру", "type": "textarea", "value": s.personality_extra}
                ]
            },
            {
                "key": "voice",
                "title": "Голос (Fish Audio)",
                "desc": "Озвучка ответов через Fish Audio (s2.1-pro-free / Drama 3).",
                "fields": [
                    {"key": "FISH_KEY", "label": "Ключ Fish Audio", "type": "password", "value": s.fish_key},
                    {"key": "FISH_MODEL", "label": "Модель озвучки", "type": "select", "value": s.fish_model,
                     "options": [["s2.1-pro-free", "S2.1 Pro Free (бесплатно)"], ["s2.1-pro", "S2.1 Pro"], ["drama-3", "Drama 3 (самая живая)"]]},
                    {"key": "MOOD_VOICE", "label": "Голос под настроение (Drama 3 на пиках)", "type": "toggle", "value": s.mood_voice},
                    {"key": "VOICE_VOLUME", "label": "Громкость", "type": "slider", "min": 0.2, "max": 2.0, "step": 0.05, "value": s.voice_volume},
                    {"key": "_fish_bal", "label": "Кредиты Fish Audio", "type": "info", "value": fish_bal},
                    {"key": "VOICE_ID", "label": "Выбор голоса", "type": "voice", "value": s.voice_id}
                ]
            },
            {
                "key": "ears",
                "title": "Диктовка и микрофон",
                "desc": "Кнопка 🎤 (Ctrl+Shift+Space) записывает речь и распознаёт через DeepSeek ASR.",
                "fields": [
                    {"key": "CHAT_SPEAK", "label": "Читать ответы чата вслух", "type": "toggle", "value": s.chat_speak}
                ]
            },
            {
                "key": "agent",
                "title": "Агент-программист (Rust)",
                "desc": "Инструменты для работы с кодом, файлами и интернетом.",
                "fields": [
                    {"key": "STUDIO_WORKSPACE", "label": "Папка проектов", "type": "text", "value": s.studio_workspace},
                    {"key": "STUDIO_MAX_STEPS", "label": "Максимум шагов за задачу", "type": "number", "value": s.studio_max_steps},
                    {"key": "STUDIO_AUTO_COMMANDS", "label": "Выполнять команды в папке проектов без спроса", "type": "toggle", "value": s.studio_auto_commands},
                    {"key": "WEB_ENABLED", "label": "Разрешить поиск в интернете", "type": "toggle", "value": s.web_enabled},
                    {"key": "_conns", "label": "Коннекторы агента", "type": "connectors", "value": conns}
                ]
            },
            {
                "key": "system",
                "title": "Telegram и система",
                "desc": "Движок: нативный Rust (100% совместим с data/).",
                "fields": [
                    {"key": "TELEGRAM_TOKEN", "label": "Токен Telegram-бота", "type": "password", "value": s.telegram_token},
                    {"key": "TELEGRAM_OWNER_ID", "label": "Твой Telegram ID", "type": "number", "value": s.telegram_owner_id},
                    {"key": "UI_THEME", "label": "Тема окна", "type": "select", "value": s.ui_theme,
                     "options": [["cyan", "Неон-голубой (под шар)"], ["violet", "Фиолетовый"], ["emerald", "Изумруд"], ["graphite", "Строгий графит"]]}
                ]
            }
        ]
    })
}
