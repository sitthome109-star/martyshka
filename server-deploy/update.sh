#!/usr/bin/env bash
set -euo pipefail

# Атомарное автообновление Мартышки с GitHub (вызывается по !обновись или кнопке в окне ПК).
# Готовый бинарник берётся из GitHub Releases (его собирает .github/workflows/release-linux.yml) —
# на сервере с 1 ГБ памяти ничего не компилируем.
SRC_DIR="${MARTYSHKA_SRC:-/opt/martyshka-src}"
OUT_DIR="${MARTYSHKA_DIR:-/opt/martyshka}"
REPO="${MARTYSHKA_REPO:-sitthome109-star/martyshka}"
URL="https://github.com/$REPO/releases/download/latest/martyshka-linux-x86_64"

# Свежие скрипты и файлы сервера (update.sh, assets) — из репозитория
if [ -d "$SRC_DIR/.git" ]; then
  cd "$SRC_DIR"
  git fetch origin
  git pull --ff-only
fi

curl -fsSL --retry 3 -o "$OUT_DIR/martyshka.new" "$URL"
# защита от битой загрузки: это должен быть исполняемый ELF, а не страница с ошибкой
if [ "$(head -c 4 "$OUT_DIR/martyshka.new" | od -An -c | tr -d ' ')" != "177ELF" ]; then
  rm -f "$OUT_DIR/martyshka.new"
  echo "Скачанный файл — не программа Linux (ELF). Осталась старая версия." >&2
  exit 1
fi
chmod +x "$OUT_DIR/martyshka.new"
mv -f "$OUT_DIR/martyshka.new" "$OUT_DIR/martyshka"

# Если обновился сам update.sh — подхватим его тоже
if [ -f "$SRC_DIR/server-deploy/update.sh" ]; then
  cp -f "$SRC_DIR/server-deploy/update.sh" "$OUT_DIR/update.sh"
  chmod +x "$OUT_DIR/update.sh"
fi
