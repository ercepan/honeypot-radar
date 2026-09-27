# VDS'e Kurulum (7/24 çalışma)

Mac'te `launchd` ile çalışıyor ama bilgisayar kapanınca/uyuyunca bot ölüyor.
VDS'te sunucu yeniden başlasa bile servis kendiliğinden açılır.

Servis çok küçük (~50 MB RAM) — **en ucuz paket fazlasıyla yeter.**

---

## Kurulum — 3 komut

Sunucuyu **Ubuntu 24.04** ile kur, sonra Mac'ten:

**1) Ayarları sunucuya gönder**

```bash
scp .env root@SUNUCU_IP:/root/.env
```

**2) Sunucuya bağlan**

```bash
ssh root@SUNUCU_IP
```

**3) Kur**

```bash
curl -fsSL https://raw.githubusercontent.com/ercepan/honeypot-radar/main/dagitim/kurulum.sh | bash
```

Bu kadar. Betik şunları yapar:
- Python ve git kurar
- `radar` adında yetkisiz bir kullanıcı açar (root olarak çalıştırmıyoruz)
- Kodu `/opt/honeypot-radar` altına indirir
- Sanal ortam + bağımlılıklar
- `/root/.env`'i taşır, izinlerini kısar (600), açıktaki kopyayı siler
- systemd servisini kurar, **boot'ta otomatik başlaması için** etkinleştirir
- Başlatır

---

## Günlük kullanım

```bash
journalctl -u honeypotradar -f        # kayıtları canlı izle
systemctl status honeypotradar        # durum
systemctl restart honeypotradar       # yeniden başlat
```

**Kod güncellemesi** (Mac'te `git push` yaptıktan sonra):

```bash
sudo bash /opt/honeypot-radar/dagitim/guncelle.sh
```

Render'daki gibi "manual deploy" derdi yok — bu komut çeker ve yeniden başlatır.

---

## Mac'teki kopyayı kapat

**ÖNEMLİ:** İki yerde birden çalıştırma. Telegram aynı bot için iki `getUpdates`
görürse **409 Conflict** verir ve bot çalışmaz.

VDS çalıştığını doğruladıktan sonra Mac'te:

```bash
launchctl unload ~/Library/LaunchAgents/com.erce.honeypotradar.plist
```

---

## Güvenlik notu

`.env` içindeki `USER_SESSION`, Telegram hesabına **tam erişim** demek —
şifreyle eşdeğer. Kiralık bir sunucuya koyduğunda o sunucuya erişebilen herkes
hesabına erişebilir. Bu yüzden:

- Sunucuya **SSH anahtarıyla** gir, şifreyle değil
- `root` şifreli girişi kapat: `PasswordAuthentication no` (`/etc/ssh/sshd_config`)
- Sunucuyu satarken/silerken önce Telegram → Ayarlar → Gizlilik →
  **Etkin Oturumlar** → o oturumu sonlandır

Servis dosyası ayrıca süreci sıkılaştırıyor: kendi dizini dışına yazamaz
(`ProtectSystem=strict`), yeni yetki kazanamaz (`NoNewPrivileges`).

---

## Sunucu seçimi

Gereken kaynak çok az, bu yüzden en ucuz paket yeterli. Belirleyici olan
**ödemenin geçmesi** — kart sorunu yaşıyorsan havale/EFT kabul eden Türk
sağlayıcı en garantisi.

| Seçenek | Yaklaşık aylık | Ödeme | Not |
|---|---|---|---|
| Hetzner CX22 | ~€4.4 | Kart / PayPal | En iyi fiyat/performans, 20 TB trafik; yeni hesapta kimlik doğrulama isteyebilir |
| Türk sağlayıcı (Weridata, Sadehost, Kayizer…) | ~₺100-200 | **Havale/EFT/FAST** | Kart gerekmez, Türkçe destek |
| Render ücretsiz katman | ₺0 | — | 750 saat/ay = tek servis 7/24'e denk; kod değişince elle "Manual Deploy" gerekir |
