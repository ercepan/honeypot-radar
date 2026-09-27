# Render'a Kurulum (ücretsiz, kartsız)

Tek ücretsiz web servisi: radar + kanal temizlik botu aynı süreçte.

## Neden tek servis

Render ücretsiz katman **750 örnek-saat/ay** verir (çalışma alanı başına).

| | Aylık saat | Sonuç |
|---|---|---|
| Tek servis 7/24 | ~744 | ✅ sığar (~6 saat pay) |
| İki ayrı servis | ~1460 | ❌ ayın ortasında ikisi de askıya alınır |

Daha önce iki servis yüzünden askıya alındı. O yüzden birleştirildi — ayırma.

## Kurulum

1. **GitHub ile giriş** → https://render.com
2. **New + → Blueprint**
3. Depoyu seç: `ercepan/honeypot-radar`
   ⚠️ "Public Git Repository" URL'i ile DEĞİL — **GitHub bağlantısıyla** seç.
   URL modunda otomatik dağıtım çalışmaz, her değişiklikte elle
   "Manual Deploy" gerekir.
4. `render.yaml` okunur, Render gizli değerleri sorar:

| Değişken | Nereden |
|---|---|
| `BOT_TOKEN` | @BotFather (kanal_supurgesi_bot) |
| `API_ID`, `API_HASH` | my.telegram.org |
| `USER_SESSION` | yerel `.env` — 48 saat sınırını kaldıran oturum anahtarı |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | radar uyarıları için |
| `DATABASE_URL` | Supabase (aşağıya bak) — boş bırakılabilir |

5. **Apply** → derleme başlar (~3 dk)

## Mac'teki kopyayı kapat

**ÖNEMLİ:** Aynı bot token'ı iki yerde `getUpdates` yaparsa Telegram
**409 Conflict** verir ve bot hiç çalışmaz.

```bash
launchctl unload ~/Library/LaunchAgents/com.erce.honeypotradar.plist
```

## Kalıcı depolama — bilmen gereken

Render ücretsiz katmanda **dosya sistemi kalıcı değil.** Servis her yeniden
başladığında (dağıtım, çökme, uyku) yerel dosyalar silinir:

| Dosya | Kaybedilince ne olur |
|---|---|
| `honeypot_radar.db` | Radar token geçmişi sıfırlanır — **tek gerçek kayıp** |
| `son_gorulen.json` | Son mesaj ID takibi sıfırlanır; tarama yine sessiz çalışır, sadece yavaşlar |
| `son_isler.json` | `/tekrar` komutu unutur — link tekrar gönderilir |

**Temizlik botu bundan etkilenmez** — durumunu her seferinde linkten alıyor.
Kaybeden yalnızca radar geçmişi.

Kalıcı istersen: **Supabase** ücretsiz Postgres aç (süresi bitmiyor),
bağlantı adresini `DATABASE_URL` olarak gir. `db.py` kendiliğinden Postgres'e
geçer. Render'ın kendi ücretsiz Postgres'ini KULLANMA — 30 gün sonra sona
erer, 14 gün ek süre, sonra veriyle birlikte silinir.

## Uyku sorunu

Ücretsiz web servisleri 15 dakika hareketsizlikte uyur. Bot yalnızca dışarı
istek attığı için (polling) bu "hareket" sayılmaz — uyursa bot durur.

Kod bunu zaten çözüyor: `RENDER_EXTERNAL_URL` Render tarafından kendiliğinden
verilir ve servis kendine 10 dakikada bir istek atar (`_self_ping`). Ek ayar
gerekmez.

## Komutlar

Render'da SSH yok; her şey panelden:
- **Logs** sekmesi — canlı kayıt
- **Manual Deploy → Deploy latest commit** — blueprint ile gerekmez, `git push` yeter
- **Environment** sekmesi — değişkenleri değiştir (kaydedince yeniden başlar)
