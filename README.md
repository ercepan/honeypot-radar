# 🎯 Honeypot Radar

BSC'de (PancakeSwap) açılan **her yeni token'ı anında yakalayıp** honeypot testinden
geçirir. Tuzak çıkanlar ana sayfada canlı bir listeye düşer — sen hiçbir şey aramazsın.

- **Canlı akış** — yeni token'lar saniyeler içinde taranır, honeypot'lar işaretlenir
- **Token sayfası** — güvenlik kartı + "kim ne almış" (son işlemler, alıcı cüzdanlar)
- **Dolandırıcı profili** — aynı cüzdanın açtığı tüm token'lar; 41. tuzağını kurduğunda tanırsın
- **Telegram uyarısı** — honeypot yakalandığı anda telefonuna mesaj (kontrat adresi kopyalanabilir)

## Nasıl çalışıyor?

```
WebSocket (yeni havuz olayı)  ─┐
                               ├─→ kuyruk → honeypot testi → veritabanı → web
HTTP boşluk doldurma          ─┘
```

1. **Yakalama** — PancakeSwap V2/V3 fabrikalarının `PairCreated`/`PoolCreated`
   olayları WebSocket'ten dinlenir (anahtarsız `bsc-rpc.publicnode.com`).
   Bağlantı koparsa kaçan bloklar HTTP RPC ile doldurulur.
2. **Test** — iki bağımsız kaynak:
   - `honeypot.is` — gerçek al/sat **simülasyonu** (birincil kanıt)
   - GoPlus — kontrat analizi (vergiler, sahip yetkileri, kontratı açan cüzdan)
3. **Kümeleme** — her token'ın deployer'ı ve (BscScan anahtarı varsa) onu fonlayan
   cüzdan kaydedilir. Aynı fon kaynağından beslenenler tek "ekip" sayılır.

### Kararlar

| Durum | Anlamı |
|---|---|
| `honeypot` | Simülasyon satamadı ya da satış vergisi %50+ — kesin tuzak |
| `riskli` | Ciddi sinyaller var (proxy kontrat, durdurulabilir transfer, yüksek vergi…) |
| `yeni` | Testleri geçti ama henüz kanıtlanmadı (az sahipli, taze kontrat) |
| `temiz` | Testleri geçti ve gerçek bir sahip tabanı var |
| `bilinmiyor` | API'ler henüz veri vermedi — kısa aralıkla yeniden denenir |

> ⚠️ **"veri yok" ≠ "güvenli".** GoPlus emin olamadığı alanları hiç göndermiyor;
> bunu temiz saymak gerçek bir honeypot'u güvenli göstermeye yol açıyordu.
> Kod bu ayrımı özellikle koruyor (`tests/test_logic.py::test_veri_yoksa_temiz_denmez`).

## Kurulum

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Tarayıcıda: http://127.0.0.1:8000

### Ayarlar (.env)

```
DATABASE_URL=            # boşsa yerel SQLite. Üretimde Supabase Postgres URL'i
BSCSCAN_API_KEY=         # isteğe bağlı: fon kaynağı kümelemesi için
TELEGRAM_BOT_TOKEN=      # isteğe bağlı: honeypot uyarıları
TELEGRAM_CHAT_ID=        # uyarıların gideceği sohbet
PORT=8000
```

### Telegram uyarıları

Honeypot yakalandığı anda mesaj gelir; her token için **yalnızca bir kez**
(veritabanındaki `alerted` sütunu tekrarı engeller). Kurulum:

1. [@BotFather](https://t.me/BotFather) → `/newbot` → token'ı al
2. Yeni bota `/start` yaz (bot ancak başlattığın kişiye mesaj gönderebilir)
3. `api.telegram.org/bot<TOKEN>/getUpdates` adresinden `chat.id` değerini oku
4. İkisini ortam değişkeni olarak tanımla

Değişkenler boşsa uyarı sistemi sessizce devre dışı kalır, uygulama normal çalışır.

## Yayına alma (Render ücretsiz)

Render'ın ücretsiz planında **kalıcı disk yok** — SQLite her yeniden başlatmada
sıfırlanır. O yüzden üretimde `DATABASE_URL` (Supabase Postgres, ücretsiz katman
500 MB, kart istemiyor) vermek şart.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.web:app --host 0.0.0.0 --port $PORT`
- `RENDER_EXTERNAL_URL` tanımlıysa uygulama kendini 10 dakikada bir ping'leyip
  uykuya geçmeyi engeller.

> Render ücretsiz planda "background worker" olmadığı için tarayıcı, web
> uygulamasının yaşam döngüsüne bağlı arka plan görevleri olarak çalışır.

## Test

```bash
.venv/bin/python -m tests.test_logic
```

## Bilinen sınırlar

- **Sadece BSC / PancakeSwap** (V2 + V3).
- GeckoTerminal işlem verisi hız limiti sert; token sayfasındaki işlem listesi
  önbelleklenir, limit dolduğunda geçici olarak boş kalabilir.
- Ücretsiz RPC'ler ~1 günden eski logları vermiyor; uzun kapalı kalırsan o
  aradaki token'lar kaçar.
- Deployer kümelemesi BscScan anahtarı olmadan yalnızca "kontratı açan" bazında
  çalışır; fon kaynağı zinciri çıkarılamaz.

## Uyarı

Bu bir **erken uyarı aracıdır, kesin hüküm değil.** Bugün "temiz" görünen bir token
yarın satış vergisini yükseltebilir — bu yüzden token'lar periyodik yeniden taranır.
Yatırım tavsiyesi değildir.
