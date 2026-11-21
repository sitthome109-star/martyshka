#!/usr/bin/env bash
set -euo pipefail

# Автоустановка 24/7 Rust-Мартышки на Linux (Ubuntu / Debian)
# Запускать из корня папки с проектом (где лежит martyshka-rs/ и deploy/linux/):
#   sudo bash deploy/linux/install.sh

INSTALL_DIR="/opt/martyshka"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "==> 1. Проверка Rust..."
if ! command -v cargo >/dev/null 2>&1; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "Устанавливаю Rust (rustup)..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  export PATH="$HOME/.cargo/bin:$PATH"
fi

echo "==> 2. Сборка оптимизированного бинарника Мартышки..."
cd "$REPO_DIR/martyshka-rs"
cargo build --release

echo "==> 3. Установка в $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/data"
cp -f "$REPO_DIR/martyshka-rs/target/release/martyshka" "$INSTALL_DIR/martyshka"
chmod +x "$INSTALL_DIR/martyshka"

if [ ! -f "$INSTALL_DIR/data/settings.json" ]; then
  echo "ВНИМАНИЕ: скопируй свой data/settings.json (и по желанию data/avatar_gov_96.jpg, data/longmem/, data/growth.json) в $INSTALL_DIR/data/"
fi

echo "==> 4. Установка и запуск systemd-сервиса martyshka.service..."
cp -f "$SCRIPT_DIR/martyshka.service" /etc/systemd/system/martyshka.service
systemctl daemon-reload
systemctl enable martyshka.service
systemctl restart martyshka.service

echo ""
echo "Готово! Проверка статуса:"
systemctl status martyshka.service --no-pager -l || true
echo ""
echo "Логи в реальном времени: journalctl -u martyshka -f"
