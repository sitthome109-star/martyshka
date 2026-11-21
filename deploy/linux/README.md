# Деплой Мартышки 24/7 на Linux-сервер

На Linux `martyshka-rs` собирается **без единой системной библиотеки** (чистый Rust + `rustls`, без ALSA/GUI/Python) и держит 24/7 сразу **Говорилку (через `botgw`) + Telegram**.

## Быстрая установка на любой Linux-сервер (Ubuntu / Debian)

1. Скопируй на сервер папку `martyshka-rs/` и `deploy/linux/` (например, в `/root/martyshka-src/`).
2. Скопируй со своего ПК в `/opt/martyshka/data/`:
   - `data/settings.json` (внутри уже прописаны ключи DeepSeek, Fish Audio, Telegram и токен `botgw`)
   - `data/avatar_gov_96.jpg` (аватарка-шар для Говорилки)
   - по желанию: `data/longmem/`, `data/growth.json`, `data/голоса_fish.json` (чтобы перенести всю её память и опыт!)
3. Запусти установщик:
   ```bash
   cd /root/martyshka-src
   sudo bash deploy/linux/install.sh
   ```

## Управление сервисом

- Статус: `sudo systemctl status martyshka`
- Живые логи: `sudo journalctl -u martyshka -f`
- Перезапуск: `sudo systemctl restart martyshka`
- Остановка: `sudo systemctl stop martyshka`

> Совет: после запуска на сервере останови локальный `Мартышка (Rust).exe --govorilka` на ПК, чтобы на сервере была одна Мартышка (в окне чата на ПК она продолжит работать как обычно!).
