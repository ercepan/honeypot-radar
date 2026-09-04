#!/usr/bin/env python3
"""Kanal temizleyici — verilen mesaja KADAR siler (o mesaj ve öncesi kalır).

Kullanım:
    .venv/bin/python temizle.py https://t.me/c/2382581205/3378
    .venv/bin/python temizle.py https://t.me/c/2382581205/3378 --dene   (sadece göster, silme)

Güvenlik: sınır mesajının ID'si sabit taban kabul edilir; o ID'ye ve altına
hiçbir koşulda dokunulmaz (her grupta ayrıca doğrulanır).
"""
from __future__ import annotations

import asyncio
import re
import sys

from app import temizlik as t

BATCH = 90
GAP = 1.0


def linki_coz(link: str) -> tuple[int, int]:
    """t.me/c/<ic_id>/<mesaj> -> (chat_id, anchor)"""
    m = re.search(r"t\.me/c/(\d+)/(?:\d+/)?(\d+)", link)
    if not m:
        raise SystemExit(f"Link çözülemedi: {link}\nÖrnek: https://t.me/c/123456789/500")
    return int(f"-100{m.group(1)}"), int(m.group(2))


async def temizle(link: str, dene: bool = False) -> None:
    import telethon.errors as terr

    chat_id, anchor = linki_coz(link)
    c = await t.get_user_client()
    if c is None:
        raise SystemExit("Kullanıcı hesabı bağlı değil (.env içindeki USER_SESSION eksik).")

    t._dialogs_loaded = False
    ent = await t._user_entity(c, chat_id)
    if ent is None:
        print(f"❌ Kanala erişilemedi ({chat_id}).")
        print("   Hesabın bu kanalda yönetici mi? 'Mesajları sil' yetkisi var mı?")
        await c.disconnect()
        return

    son = await c.get_messages(ent, limit=1)
    son_id = son[0].id if son else 0
    print(f"kanal {chat_id} | son mesaj {son_id} | sınır {anchor} (bu ve öncesi KALACAK)")

    ana = await c.get_messages(ent, ids=[anchor])
    if ana and ana[0]:
        onizleme = (ana[0].message or "(medya)")[:55].replace("\n", " ")
        print(f'KORUNACAK {anchor}: "{onizleme}"')
    else:
        print(f"not: {anchor} numaralı mesaj şu an yok; sınır yine {anchor}")

    ids = list(range(anchor + 1, son_id + 1))
    if not ids:
        print("✅ Sınırın üstünde mesaj yok — zaten temiz.")
        await c.disconnect()
        return

    mevcut = []
    for i in range(0, len(ids), 200):
        parca = await c.get_messages(ent, ids=ids[i : i + 200])
        mevcut += [m for m in parca if m]
        await asyncio.sleep(0.3)

    print(f"SİLİNECEK: {len(mevcut)} mesaj (ID {anchor+1} — {son_id})")
    if mevcut:
        e = min(mevcut, key=lambda m: m.date)
        y = max(mevcut, key=lambda m: m.date)
        print(f"  en eski {e.date:%d.%m %H:%M} | en yeni {y.date:%d.%m %H:%M}")
    if not mevcut:
        print("✅ zaten temiz")
        await c.disconnect()
        return
    if dene:
        print("\n(deneme modu — hiçbir şey silinmedi)")
        await c.disconnect()
        return

    print("\nsiliniyor...")
    hedef = sorted([m.id for m in mevcut], reverse=True)
    assert all(i > anchor for i in hedef), "GÜVENLİK İHLALİ"
    silinen = 0
    for i in range(0, len(hedef), BATCH):
        grup = hedef[i : i + BATCH]
        assert min(grup) > anchor, "GÜVENLİK İHLALİ"
        for _ in range(6):
            try:
                await c.delete_messages(ent, grup)
                silinen += len(grup)
                print(f"  {max(grup)}..{min(grup)} silindi")
                break
            except terr.FloodWaitError as e:
                print(f"  flood: {e.seconds} sn bekleniyor")
                await asyncio.sleep(e.seconds + 2)
            except Exception as ex:  # noqa: BLE001
                print(f"  hata: {ex}")
                break
        await asyncio.sleep(GAP)

    await asyncio.sleep(2)
    kalan = []
    for i in range(0, len(ids), 200):
        parca = await c.get_messages(ent, ids=ids[i : i + 200])
        kalan += [m.id for m in parca if m]
    print(f"\n=== SONUÇ ===")
    print(f"  silinen: {silinen}")
    print(f"  sınır üstünde kalan: {len(kalan)}" + ("" if kalan else "  ✅ TEMİZ"))
    onceki = await c.get_messages(ent, ids=[anchor - 1, anchor - 4, anchor - 8])
    print(f"  sınır öncesi duruyor: {[m.id for m in onceki if m]}")
    await c.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(temizle(sys.argv[1], dene="--dene" in sys.argv))
