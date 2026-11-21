#!/usr/bin/env bash
set -euo pipefail

# Атомарное автообновление Мартышки с GitHub (вызывается по !обновись или кнопке в окне ПК)
SRC_DIR="${MARTYSHKA_SRC:-/opt/martyshka-src}"
OUT_DIR="${MARTYSHKA_DIR:-/opt/martyshka}"
export PATH="$HOME/.cargo/bin:/root/.cargo/bin:$PATH"

cd "$SRC_DIR"
git fetch origin
git pull --ff-only

cd "$SRC_DIR/martyshka-rs"
cargo build --release

cp -f target/release/martyshka "$OUT_DIR/martyshka.new"
chmod +x "$OUT_DIR/martyshka.new"
mv -f "$OUT_DIR/martyshka.new" "$OUT_DIR/martyshka"

# Если обновился сам update.sh — подхватим его тоже
if [ -f "$SRC_DIR/server-deploy/update.sh" ]; then
  cp -f "$SRC_DIR/server-deploy/update.sh" "$OUT_DIR/update.sh"
  chmod +x "$OUT_DIR/update.sh"
fi
