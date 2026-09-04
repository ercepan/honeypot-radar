#!/usr/bin/env python3
"""Kendi Telegram hesabınla tek seferlik giriş — 48 saat sınırını kaldırır.

NEDEN GEREKLİ
-------------
Telegram, BOTLARIN 48 saatten eski mesajları silmesine izin vermiyor
("message can't be deleted"). Bu bir yetki meselesi değil, platform kuralı.
KULLANICI hesaplarında böyle bir sınır yok — kendi hesabın kanaldaki her
yaştaki mesajı silebilir.

Bu betik senin hesabınla bir oturum açar ve "oturum anahtarı" üretir.
Bot bundan sonra silme işini senin hesabın üzerinden yapar.

NASIL ÇALIŞTIRILIR
------------------
    .venv/bin/python hesap_girisi.py

Telefon numaranı ve Telegram'a gelen kodu SEN gireceksin.

⚠️ GÜVENLİK — ÖNEMLİ
    Üretilen oturum anahtarı hesabına TAM erişim demektir; şifrenle eşdeğerdir.
    • Kimseyle paylaşma, ekran görüntüsü alma, sohbete yapıştırma.
    • .env dosyasında durur, o dosya git'e gönderilmez (.gitignore'da).
    • Şüphelenirsen: Telegram → Ayarlar → Gizlilik → Etkin Oturumlar → sonlandır.

⚠️ HESAP RİSKİ
    Telegram, kullanıcı hesaplarının otomatikleştirilmesine sıcak bakmaz.
    Yavaş ve makul kullanımda sorun çıkmaz, ama çok hızlı toplu silmede
    hesabın geçici kısıtlanabilir. Bu yüzden silme hızı bilerek düşük tutuldu.
"""
import asyncio
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

from telethon import TelegramClient
from telethon.sessions import StringSession

ENV_PATH = Path(__file__).with_name(".env")


async def main() -> None:
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not (api_id.isdigit() and api_hash):
        print("HATA: .env içinde API_ID ve API_HASH yok.")
        print("Bunlar my.telegram.org → API development tools'tan alınır.")
        sys.exit(1)

    print("\n" + "=" * 58)
    print("  TELEGRAM HESAP GİRİŞİ")
    print("=" * 58)
    print("  Telefon numaranı ülke koduyla gir. Örnek: +905551112233")
    print("  Kod Telegram uygulamana gelecek (SMS değil).")
    print("  İki adımlı doğrulaman varsa şifreni de soracak.\n")

    def sifre_sor() -> str:
        import getpass

        print()
        print("  ┌────────────────────────────────────────────────────────┐")
        print("  │  İKİ ADIMLI DOĞRULAMA ŞİFREN                           │")
        print("  │                                                        │")
        print("  │  DİKKAT: Yazdıkça ekranda HİÇBİR ŞEY görünmeyecek.     │")
        print("  │  Bu normaldir — imleç kıpırdamaz, yıldız da çıkmaz.    │")
        print("  │  Şifreni yaz ve Enter'a bas.                           │")
        print("  └────────────────────────────────────────────────────────┘")
        return getpass.getpass("  Şifre (görünmez): ")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start(
        phone=lambda: input("\n  Telefon numaran (+90...): ").strip(),
        code_callback=lambda: input("\n  Telegram'a gelen kod: ").strip(),
        password=sifre_sor,
    )

    me = await client.get_me()
    session_str = client.session.save()
    await client.disconnect()

    ad = " ".join(filter(None, [me.first_name, me.last_name])) or str(me.id)
    print("\n" + "=" * 58)
    print(f"  ✅ Giriş başarılı: {ad}" + (f" (@{me.username})" if me.username else ""))
    print("=" * 58)

    # .env dosyasına yaz (varsa eski satırı değiştir)
    satirlar = []
    if ENV_PATH.exists():
        satirlar = [
            s for s in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if not s.startswith("USER_SESSION=")
        ]
    satirlar.append(f"USER_SESSION={session_str}")
    ENV_PATH.write_text("\n".join(satirlar) + "\n", encoding="utf-8")

    print(f"\n  Oturum anahtarı kaydedildi: {ENV_PATH.name}")
    print("  Bu dosyayı kimseyle paylaşma.\n")
    print("  Şimdi botu yeniden başlat; silme işlemleri artık SENİN hesabınla")
    print("  yapılacak ve 48 saat sınırı olmayacak.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nİptal edildi.")
