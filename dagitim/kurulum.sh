#!/usr/bin/env bash
# Sıfır Ubuntu/Debian VDS'e Honeypot Radar + Kanal Temizlik Botu kurar.
#
#   sudo bash kurulum.sh
#
# ÖNCESİNDE: .env dosyasını sunucuya koy (Mac'ten):
#   scp .env root@SUNUCU_IP:/root/.env
# Yoksa betik boş bir şablon oluşturur, sen doldurursun.

set -euo pipefail

DIZIN=/opt/honeypot-radar
KULLANICI=radar
DEPO=https://github.com/ercepan/honeypot-radar.git
SERVIS=honeypotradar

renk() { printf "\033[1;36m%s\033[0m\n" "$*"; }
hata() { printf "\033[1;31mHATA: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || hata "root olarak çalıştır: sudo bash kurulum.sh"

renk "1/7  Paketler kuruluyor..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git ca-certificates >/dev/null

renk "2/7  Servis kullanıcısı: $KULLANICI"
# Kendi kullanıcısıyla çalışsın — root olarak çalıştırmıyoruz.
id -u "$KULLANICI" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$KULLANICI"

renk "3/7  Kod indiriliyor: $DIZIN"
if [[ -d $DIZIN/.git ]]; then
    git -C "$DIZIN" fetch --quiet origin
    git -C "$DIZIN" reset --hard --quiet origin/main
else
    git clone --quiet "$DEPO" "$DIZIN"
fi

renk "4/7  Python ortamı ve bağımlılıklar"
python3 -m venv "$DIZIN/.venv"
"$DIZIN/.venv/bin/pip" install --quiet --upgrade pip
"$DIZIN/.venv/bin/pip" install --quiet -r "$DIZIN/requirements.txt"

renk "5/7  Ayarlar (.env)"
if [[ -f /root/.env && ! -f $DIZIN/.env ]]; then
    cp /root/.env "$DIZIN/.env"
    echo "     /root/.env kopyalandı."
    shred -u /root/.env 2>/dev/null || rm -f /root/.env   # kopyayı açıkta bırakma
elif [[ ! -f $DIZIN/.env ]]; then
    cat > "$DIZIN/.env" <<'SABLON'
BOT_TOKEN=
API_ID=
API_HASH=
USER_SESSION=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
PORT=8000
SABLON
    echo "     Şablon oluşturuldu — DOLDURMAN GEREKİYOR:"
    echo "       nano $DIZIN/.env"
fi
# Oturum anahtarı içeriyor: yalnızca servis kullanıcısı okuyabilsin.
chmod 600 "$DIZIN/.env"
chown -R "$KULLANICI":"$KULLANICI" "$DIZIN"

renk "6/7  systemd servisi"
cp "$DIZIN/dagitim/$SERVIS.service" "/etc/systemd/system/$SERVIS.service"
systemctl daemon-reload
systemctl enable --quiet "$SERVIS"

renk "7/7  Başlatılıyor"
if grep -q '^BOT_TOKEN=.\+' "$DIZIN/.env"; then
    systemctl restart "$SERVIS"
    sleep 6
    systemctl --no-pager --lines=0 status "$SERVIS" || true
    echo
    renk "✅ Kuruldu ve çalışıyor."
else
    echo
    renk "⚠️  .env boş — servis ETKİN ama başlatılmadı."
    echo "   Doldurduktan sonra:  systemctl start $SERVIS"
fi

cat <<BILGI

  Kayıtları izle   : journalctl -u $SERVIS -f
  Durum            : systemctl status $SERVIS
  Yeniden başlat   : systemctl restart $SERVIS
  Kodu güncelle    : bash $DIZIN/dagitim/guncelle.sh

  Sunucu yeniden başlarsa servis kendiliğinden açılır (enable edildi).
BILGI
