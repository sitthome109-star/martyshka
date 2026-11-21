#!/usr/bin/env bash
set -euo pipefail

# Разовая установка Мартышки на Linux-сервер из git-репозитория:
#   git clone <REPO_URL> /opt/martyshka-src
#   sudo bash /opt/martyshka-src/server-deploy/install.sh

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

if [ ! -f "$OUT_DIR/data/settings.json" ]; then
  cp -f "$SRC_DIR/server-deploy/settings.example.json" "$OUT_DIR/data/settings.json"
  echo ">>> Впиши DEEPSEEK_KEY и FISH_KEY в $OUT_DIR/data/settings.json и выполни: sudo systemctl restart martyshka"
fi

cp -f "$SRC_DIR/server-deploy/martyshka.service" /etc/systemd/system/martyshka.service
systemctl daemon-reload
systemctl enable martyshka.service
systemctl restart martyshka.service

echo ""
echo "Готово! Статус сервиса:"
systemctl status martyshka.service --no-pager -l || true
