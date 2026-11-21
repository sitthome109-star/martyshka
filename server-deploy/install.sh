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

# Безопасное автодобавление /martyshka-api/* в Caddyfile (с бэкапом и проверкой caddy validate)
CADDYFILE="/etc/caddy/Caddyfile"
if [ -f "$CADDYFILE" ] && ! grep -q "/martyshka-api/" "$CADDYFILE" && grep -q "/bot-ws" "$CADDYFILE"; then
  echo "==> Добавляю /martyshka-api/* в $CADDYFILE..."
  cp -f "$CADDYFILE" "${CADDYFILE}.bak.martyshka"
  awk '
    !done && /\/bot-ws/ {
      print "    handle_path /martyshka-api/* {"
      print "        reverse_proxy 127.0.0.1:8787"
      print "    }"
      done=1
    }
    { print }
  ' "${CADDYFILE}.bak.martyshka" > "$CADDYFILE"
  if command -v caddy >/dev/null 2>&1 && caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
    systemctl reload caddy && echo "==> Caddy обновлён! Пульт доступен на /martyshka-api/"
  else
    echo "ВНИМАНИЕ: автопатч Caddy не прошёл проверку — восстанавливаю исходный Caddyfile."
    mv -f "${CADDYFILE}.bak.martyshka" "$CADDYFILE"
    echo "Добавь вручную в Caddyfile:"
    echo "  handle_path /martyshka-api/* { reverse_proxy 127.0.0.1:8787 }"
  fi
fi
