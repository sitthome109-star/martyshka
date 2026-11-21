#!/usr/bin/env bash
set -euo pipefail

# Разовая установка Мартышки на Linux-сервер одной командой:
#   git clone https://github.com/sitthome109-star/martyshka.git /opt/martyshka-src
#   sudo DEEPSEEK_KEY="..." FISH_KEY="..." GOVORILKA_BOT_TOKEN="..." bash /opt/martyshka-src/server-deploy/install.sh

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="/opt/martyshka"
export PATH="$HOME/.cargo/bin:/root/.cargo/bin:$PATH"

if ! command -v cargo >/dev/null 2>&1; then
  echo "==> Устанавливаю Rust (rustup)..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  export PATH="$HOME/.cargo/bin:/root/.cargo/bin:$PATH"
fi

echo "==> Сборка оптимизированного бинарника Мартышки..."
cd "$SRC_DIR/martyshka-rs"
cargo build --release

mkdir -p "$OUT_DIR/data/память"
cp -f target/release/martyshka "$OUT_DIR/martyshka"
chmod +x "$OUT_DIR/martyshka"

cp -f "$SRC_DIR/server-deploy/update.sh" "$OUT_DIR/update.sh"
chmod +x "$OUT_DIR/update.sh"

# Аватарка и каталог голосов
if [ -d "$SRC_DIR/server-deploy/assets" ]; then
  cp -rn "$SRC_DIR/server-deploy/assets/"* "$OUT_DIR/data/" || true
fi

# Автозаполнение settings.json из переменных окружения (если переданы)
if [ ! -f "$OUT_DIR/data/settings.json" ]; then
  cp -f "$SRC_DIR/server-deploy/settings.example.json" "$OUT_DIR/data/settings.json"
fi

if [ -n "${DEEPSEEK_KEY:-}" ]; then
  sed -i "s|ВСТАВИТЬ_DEEPSEEK_KEY|${DEEPSEEK_KEY}|g" "$OUT_DIR/data/settings.json"
fi
if [ -n "${FISH_KEY:-}" ]; then
  sed -i "s|ВСТАВИТЬ_FISH_KEY|${FISH_KEY}|g" "$OUT_DIR/data/settings.json"
fi
if [ -n "${GOVORILKA_BOT_TOKEN:-}" ]; then
  sed -i "s|ВСТАВИТЬ_GOVORILKA_BOT_TOKEN|${GOVORILKA_BOT_TOKEN}|g" "$OUT_DIR/data/settings.json"
fi

cp -f "$SRC_DIR/server-deploy/martyshka.service" /etc/systemd/system/martyshka.service
systemctl daemon-reload
systemctl enable martyshka.service
systemctl restart martyshka.service

echo ""
echo "Готово! Статус сервиса:"
systemctl status martyshka.service --no-pager -l || true
echo ""
echo "Для пульта управления с ПК добавь в Caddy:"
echo "  handle_path /martyshka-api/* { reverse_proxy 127.0.0.1:8787 }"
