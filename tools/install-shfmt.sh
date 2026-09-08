#!/usr/bin/env bash
# Установка разбора оболочки (mvdan/sh) — того, на котором стоит адаптер
# `command-tree-shell`, а через него все три гварда необратимого, план-гейт и
# гвард якоря сессии. Без него слой не гадает: адаптер отвечает `unsupported:
# shfmt`, гвард пропускает вызов и называет недостающее (решение 14).
#
#   bash tools/install-shfmt.sh [каталог]     # по умолчанию /usr/local/bin
#
# На маке проще брать из пакетного менеджера: `brew install shfmt`. Здесь —
# бинарник с ПРОВЕРКОЙ КОНТРОЛЬНОЙ СУММЫ: разбор команд решает, что уйдёт мимо
# гвардов, и качать его без сверки значило бы отдать это решение сети.
#
# Суммы взяты из sha256sums.txt самого выпуска и лежат ЗДЕСЬ, а не тянутся рядом
# с бинарником: файл сумм, скачанный тем же каналом, ничего не доказывает.
set -euo pipefail

VERSION=v3.12.0
DEST="${1:-/usr/local/bin}"

case "$(uname -s)/$(uname -m)" in
  Linux/x86_64)  ASSET=linux_amd64;  SUM=d9fbb2a9c33d13f47e7618cf362a914d029d02a6df124064fff04fd688a745ea ;;
  Linux/aarch64) ASSET=linux_arm64;  SUM=5f3fe3fa6a9f766e6a182ba79a94bef8afedafc57db0b1ad32b0f67fae971ba4 ;;
  Linux/armv7l)  ASSET=linux_arm;    SUM=a93c1ed5be25ce9dd0fd62c4cf0af7453740d234725877b973e6c6a8c7598500 ;;
  Darwin/x86_64) ASSET=darwin_amd64; SUM=c31548693de6584e6164b7ed5fbb7b4a083f2d937ca94b4e0ddf59aa461a85e4 ;;
  Darwin/arm64)  ASSET=darwin_arm64; SUM=d903802e0ce3ecbc82b98512f55ba370b0d37a93f3f78de394f5b657052b33dd ;;
  *)
    echo "ERROR: суммы для $(uname -s)/$(uname -m) в скрипте нет; поставь shfmt $VERSION руками" >&2
    exit 1 ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/mvdan/sh/releases/download/$VERSION/shfmt_${VERSION}_${ASSET}"

curl -fsSL -o "$TMP/shfmt" "$URL"

# Сверка ДО установки и своим средством на каждой из систем: `sha256sum` есть в
# линуксе, `shasum -a 256` — на маке.
if command -v sha256sum >/dev/null 2>&1; then
  GOT="$(sha256sum "$TMP/shfmt" | cut -d' ' -f1)"
else
  GOT="$(shasum -a 256 "$TMP/shfmt" | cut -d' ' -f1)"
fi
if [ "$GOT" != "$SUM" ]; then
  echo "ERROR: сумма не сошлась ($GOT вместо $SUM); файл НЕ установлен" >&2
  exit 1
fi

chmod +x "$TMP/shfmt"
install -m 0755 "$TMP/shfmt" "$DEST/shfmt" 2>/dev/null || {
  echo "не хватило прав на $DEST — повтори с sudo или укажи свой каталог" >&2
  exit 1
}
"$DEST/shfmt" --version
