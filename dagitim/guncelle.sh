#!/usr/bin/env bash
# Kodu GitHub'dan günceller ve servisi yeniden başlatır.
#   sudo bash /opt/honeypot-radar/dagitim/guncelle.sh
set -euo pipefail
DIZIN=/opt/honeypot-radar
[[ $EUID -eq 0 ]] || { echo "root gerekiyor: sudo bash $0" >&2; exit 1; }

# .env depoda değil (.gitignore'da) — reset onu silmez, ama yine de emin olalım.
[[ -f $DIZIN/.env ]] || { echo "HATA: $DIZIN/.env yok" >&2; exit 1; }

git -C "$DIZIN" fetch --quiet origin
ONCE=$(git -C "$DIZIN" rev-parse HEAD)
git -C "$DIZIN" reset --hard --quiet origin/main
SONRA=$(git -C "$DIZIN" rev-parse HEAD)

if [[ $ONCE == "$SONRA" ]]; then
    echo "Kod zaten güncel ($(git -C "$DIZIN" log -1 --format=%h))."
else
    echo "Güncellendi: ${ONCE:0:7} → ${SONRA:0:7}"
    "$DIZIN/.venv/bin/pip" install --quiet -r "$DIZIN/requirements.txt"
fi

chown -R radar:radar "$DIZIN"
systemctl restart honeypotradar
sleep 5
systemctl --no-pager --lines=0 status honeypotradar || true
