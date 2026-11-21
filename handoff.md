# HANDOFF — Мартышка (Rust + 24/7 Говорилка)

> Обновлено: 21.11.2026  
> Репозиторий: `https://github.com/sitthome109-star/martyshka` (ветка `main`)  
> Локальный проект (ПК Windows 11): `C:\Users\offeballls\Documents\Martyshka`

---

## 1. Текущее состояние (что уже работает вживую)

1. **Нативное приложение на Rust (`Мартышка.exe`, ~5.5 МБ):**
   - Исходники: `martyshka-rs/` (0 предупреждений компилятора, `cargo test` — 5/5 passed; `cargo clippy` — без ошибок, ~35 мелких советов по стилю. 21.11 исправлен `web_fetch`: регулярка с `` роняла инструмент при каждом вызове).
   - Окно WebView2 без приставки `(Rust)`, заголовок **`Мартышка`**, встроенная светящаяся голубая иконка-шар (`make_orb_icon()`).
   - Дизайн **Liquid Glass 2.0** (`martyshka/studio/ui/index.html`): 6 тем (`cyan`, `violet`, `emerald`, `sunset`, `sakura`, `graphite`), палитра быстрых команд **`Ctrl+K`**, экспорт чата в Markdown (`⤓ .md`), прикрепление файлов/скринов (`Ctrl+V`, Drag & Drop, снимок экрана в 1 клик), переделанная вкладка **«Говорилка · 24/7»**.
   - Автопоиск `data/settings.json` (`has_real_settings` + фолбэк на `%USERPROFILE%\Documents\Martyshka` + защита от UTF-8 BOM), поэтому `Мартышка.exe` работает из любой папки.

2. **24/7 серверная Мартышка в Говорилке (на сервере Сергея):**
   - Подключена напрямую к WebSocket-шлюзу `botgw` (`wss://gv.34-107-2-200.sslip.io/bot-ws`) — **без GUI, без `govorilka.exe`, без VB-CABLE и без OCR**.
   - Работает круглосуточно независимо от ПК хозяина (`martyshka.service`).
   - Rust-бот `--govorilka` на ПК не запускается сам.
   - ⚠️ **Старый Python-бот на ПК ещё может запуститься** (проверено 21.11): в Автозагрузке лежит `Мартышка в Говорилке.lnk` → `pythonw -m martyshka.govorilka --autostart`, а в `data/settings.json` `GOVORILKA_AUTOSTART = true` (его же поднимает сторож в `martyshka_main.py`). Тогда в Говорилке будут две Мартышки. Чтобы выключить: поставить `GOVORILKA_AUTOSTART = false` (ярлык — в Корзину, только с согласия брата).
   - Установлена аниме-аватарка (`server-deploy/assets/avatar_gov_96.jpg`, 96×96, 2.7 КБ) и перенесены 25 фактов долгой памяти о людях.

3. **Дистанционный пульт управления с ПК (`Сервер 24/7`):**
   - Встроенный HTTP-пульт в `govorilka_ws.rs` (на сервере порт `127.0.0.1:8787` (по умолчанию в коде, меняется `MARTYSHKA_ADMIN_PORT`), в Caddy проброшен на `https://gv.34-107-2-200.sslip.io/martyshka-api/*`).
   - Авторизация: `Authorization: Bearer <GOVORILKA_BOT_TOKEN>` (ключ хранится только в `data/settings.json`, **не в git**).
   - В `data/settings.json` на ПК прописан `GOVORILKA_REMOTE_URL = "https://gv.34-107-2-200.sslip.io/martyshka-api"`.
   - Из вкладки «Говорилка · 24/7» в окне `Мартышка.exe` управляются в реальном времени: **характер (`PERSONALITY_EXTRA`), ник, аватарка (автосжатие 96×96 JPEG), голос Fish Audio, движок озвучки, мозг (`Flash`/`Pro`), пульт эфира (сказать в войс / написать в чат / вкинуть тему / спор с Морни / замолчать / тихий режим / сброс контекста), живой список участников, живой журнал и кнопка `🔄 Обновить сервер`**.

4. **Автосборка и автообновление в 1 клик:**
   - GitHub Actions workflow: `.github/workflows/release-linux.yml`.
   - При каждом `git push` в `main` (при правках `martyshka-rs/**`) GitHub сам собирает статический бинарник `martyshka-linux-x86_64` (`x86_64-unknown-linux-musl`) и публикует его в GitHub Releases (`latest`).
   - На сервере `/opt/martyshka/update.sh` делает `git pull` и скачивает готовый `martyshka-linux-x86_64` из Releases (`latest`) с проверкой, что это ELF; на 1 ГБ RAM ничего не компилирует (до 21.11 скрипт в репозитории собирал `cargo build` прямо на сервере — первое обновление ещё пройдёт старым скриптом, дальше новый). Если репозиторий приватный, скачивание без токена не сработает; запуск обновления — командой `!обновись` в чате Говорилки или кнопкой **`🔄 Обновить сервер`** в окне на ПК.

---

## 2. Карта ключевых файлов

| Путь | Назначение |
|---|---|
| `martyshka-rs/src/main.rs` | Точка входа (`--govorilka`, `--telegram`, `--server`, `--ask`, Windows WebView2 UI, IPC-мост) |
| `martyshka-rs/src/govorilka_ws.rs` | 24/7 WebSocket-клиент `botgw`, 19+ команд хозяина, голосовой выход только от хозяина, встроенный Admin HTTP API, self-updater |
| `martyshka-rs/src/config.rs` | Загрузка/сохранение `data/settings.json`, автопоиск `Documents/Martyshka`, защита от UTF-8 BOM |
| `martyshka-rs/src/fish.rs` | Fish Audio TTS (`24 кГц PCM`), стабильные русские ударения, защита от случайного включения платной Drama 3 |
| `martyshka-rs/src/deepseek.rs` | DeepSeek API (чат, стриминг, кеш `DYNAMIC_MARK`, ASR `transcribe`, баланс) |
| `martyshka-rs/src/longmem.rs` | Долгая память (`data/память/люди.json`, `дни.json`, `лента/*.jsonl`), нормализация хозяина (`sitth`/`sitthome` → `брат`) |
| `martyshka-rs/src/growth.rs` | База саморазвития / уроков (`data/память/опыт.json`) |
| `martyshka-rs/src/agent.rs` | Агент-программист для чата, инструменты, контрольные точки отката |
| `martyshka/studio/ui/index.html` | UI окна (встраивается в `Мартышка.exe` и подгружается с диска) |
| `server-deploy/` | `install.sh`, `update.sh`, `martyshka.service`, `settings.example.json`, `assets/` (аватарка + каталог голосов) |
| `.github/workflows/release-linux.yml` | Автосборка статического `martyshka-linux-x86_64` в GitHub Releases (`latest`) |

---

## 3. Команды хозяина (`sitth` / `sitthome`) в Говорилке

Работают **только от хозяина**:
- **Голосом в войсе:** «Мартышка, выйди» / «выйди из войса» → выходит из войса, остаётся в чате. Чужие просьбы выйти игнорирует.
- **В текстовом чате:** `!помощь`, `!войс`, `!выйди`, `!стоп`, `!тише`, `!слушай`, `!скажи <текст>`, `!напиши <текст>`, `!тема <тема>`, `!морни [тема]`, `!голос <имя>`, `!движок free|pro|drama`, `!мозг flash|pro`, `!кто`, `!память <имя>`, `!запомни имя | факт`, `!забудь имя | факт`, `!успокойся [имя]`, `!сброс`, `!баланс`, `!статус`, `!обновись`.

---

## 4. Критически важные правила и ограничения

1. **Русские ударения в Fish Audio:**
   - **НЕ переводить** русские теги `{эмоция}` в английские и **НЕ поднимать** `temperature > 0.78` и не менять `prosody.speed` (держать `temperature = 0.75`, `top_p = 0.80`, `chunk_length = 200`, `speed = 1.0`).
   - **Защита от списаний Drama 3:** `MOOD_VOICE` по умолчанию `false`, модели по умолчанию `s2.1-pro-free`. Платную `drama-3` включать только по явному выбору пользователя.
2. **Безопасность и секреты:**
   - **Никогда не коммитить `data/`**, ключи DeepSeek/Fish/Telegram и `GOVORILKA_BOT_TOKEN` в git (`.gitignore` защищает `data/*`).
3. **Среда Windows 11 / PowerShell 5.1:**
   - В новой сессии добавлять Rust в PATH: `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"`.
   - PowerShell 5.1 не поддерживает `&&` / `||` — использовать `; if ($?) { ... }`.
   - При записи `data/settings.json` из PowerShell писать **без UTF-8 BOM** (`[System.Text.UTF8Encoding]::new($false)`).
   - **Никогда не завершать** личный клиент Говорилки пользователя (`"C:\Users\offeballls\Desktop\govorilka.exe"` с кавычками).
   - Удалять файлы **только в Корзину** и **только после явного подтверждения** пользователя.
4. **Неснимаемые границы характера:** без угроз/насилия/самоповреждения, без оскорблений по национальности/религии/полу/ориентации/внешности/здоровью/родственникам, без чужих личных данных.

---

## 5. Быстрые команды разработки

```powershell
# Тесты Rust
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
Set-Location C:\Users\offeballls\Documents\Martyshka\martyshka-rs
cargo test

# Сборка релизного Windows exe
cargo build --release
Copy-Item -Force target\release\martyshka.exe ..\Мартышка.exe

# Выкат обновления на 24/7 сервер (после git push GitHub Actions соберёт martyshka-linux-x86_64)
git push origin main
# Затем нажать «🔄 Обновить сервер» в окне Мартышка.exe или написать !обновись в чате Говорилки
```

---

## 6. Что можно сделать дальше (по запросу пользователя)

- Проверить завершение первого запуска GitHub Actions (`release-linux.yml`) и убедиться, что `martyshka-linux-x86_64` появился в Releases (`latest`).
- По подтверждению пользователя отправить в Корзину оставшиеся устаревшие файлы: `Мартышка (Rust).exe` (в `Documents\Martyshka` и `Downloads`), `Downloads\data\`, `data\govorilka_bot\appdata\`, `data\studio\test_chats\`, `data\history_backup_2026-11-16_*.json`, `data\проба_пения.wav`.
- При желании добавить новые «суперспособности» на сервер (поиск в интернете прямо в Говорилке, саундпад/музыка, мини-игры и т.п.) — теперь выкатываются за 1 минуту через `git push` + `!обновись`.
