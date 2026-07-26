"""Gizli tuzak dedektörü — "gecikmeli honeypot" ailesini yakalar.

Arka plan
---------
Gelişmiş bir tuzak ailesi, kurulum anında token'ın oluşabileceği TÜM havuz
adreslerini (11 ana varlık × V2 + V3'ün 4 komisyon kademesi) CREATE2 formülüyle
önceden hesaplayıp "yasaklı" listesine yazıyor — havuzlar daha var olmadan.
Yalnızca bir tanesi (genelde token/WBNB) serbest bırakılıyor ki lansmanda
alım-satım çalışsın ve simülatörler "temiz" desin. Sahibi istediği an tek
çağrıyla o son havuzu da kapatıp herkesi kilitliyor.

Kodda "blacklist", "pause", "tradingEnabled" gibi bilinen kelimeler geçmediği
için statik tarayıcıların bayrakları yanmıyor.

Buradaki test
-------------
Bu ailenin kaçınılmaz bir izi var: HENÜZ VAR OLMAYAN havuz adreslerine transfer
reddediliyor. Sağlam bir token'ın rastgele bir adrese transferi asla reddedilmez.

  1) kontrol  : rastgele boş bir adrese transfer -> başarılı olmalı
  2) tuzak testi: türetilmiş (var olmayan) havuz adreslerine transfer
     Kontrol geçip havuz testleri reddediliyorsa -> önceden yasaklama var.

İsim değişikliğine dayanıklı: fonksiyon/değişken adlarına değil, DAVRANIŞA bakar.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp
from eth_hash.auto import keccak

from . import config

log = logging.getLogger("radar.trap")

# PancakeSwap CREATE2 sabitleri (zincire karşı doğrulandı)
V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
V2_INIT_HASH = "00fb7f630766e6a796048ea87d01acd3068e8ff67d078148a3fa3f4a84f69bd5"
V3_DEPLOYER = "0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9"
V3_INIT_HASH = "6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2"

# Tuzak kontratlarının önceden yasakladığı tipik ana varlıklar
QUOTES = {
    "USDT": "0x55d398326f99059ff775485246999027b3197955",
    "BUSD": "0xe9e7cea3dedca5984780bafc599bd69add087d56",
    "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    "BTCB": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
}
V3_FEES = (100, 500, 2500, 10000)

# Hiçbir zaman kullanılmamış, kodu olmayan kontrol adresi
CONTROL_ADDR = "0x00000000000000000000000000000000000c0ffee"

TRANSFER_SELECTOR = "0xa9059cbb"


def _b(addr: str) -> bytes:
    return bytes.fromhex(addr[2:] if addr.startswith("0x") else addr)


def v2_pair(token_a: str, token_b: str) -> str:
    x, y = sorted([token_a.lower(), token_b.lower()])
    salt = keccak(_b(x) + _b(y))
    return "0x" + keccak(b"\xff" + _b(V2_FACTORY) + salt + _b(V2_INIT_HASH))[12:].hex()


def v3_pool(token_a: str, token_b: str, fee: int) -> str:
    x, y = sorted([token_a.lower(), token_b.lower()])
    salt = keccak(
        _b(x).rjust(32, b"\0") + _b(y).rjust(32, b"\0") + fee.to_bytes(32, "big")
    )
    return "0x" + keccak(b"\xff" + _b(V3_DEPLOYER) + salt + _b(V3_INIT_HASH))[12:].hex()


def _transfer_data(to: str, amount: int = 1) -> str:
    return (
        TRANSFER_SELECTOR
        + to[2:].lower().rjust(64, "0")
        + format(amount, "x").rjust(64, "0")
    )


async def _eth_call(
    session: aiohttp.ClientSession, url: str, frm: str, to: str, data: str
) -> tuple[bool, str]:
    """(basarili_mi, hata_mesaji) döndürür."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"from": frm, "to": to, "data": data}, "latest"],
    }
    try:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            if r.status != 200:
                return False, f"http {r.status}"
            out = await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return False, f"ag hatasi: {e}"

    if "error" in out:
        return False, str((out["error"] or {}).get("message", ""))[:120]
    return True, ""


async def detect_preblocked_pools(
    session: aiohttp.ClientSession, token: str, holder: str
) -> Optional[dict]:
    """Önceden yasaklanmış havuz kalıbını arar.

    holder: token'dan bakiyesi olan bir adres (transfer simülasyonu için gerekli).
    Dönen: None (tespit yok / test yapılamadı) ya da bulgu sözlüğü.
    """
    token = token.lower()
    holder = holder.lower()
    url = config.HTTP_RPCS[0][0]

    # 1) KONTROL — boş bir adrese transfer çalışmalı. Çalışmıyorsa test anlamsız
    #    (token her transferi engelliyordur ya da holder'ın bakiyesi yoktur).
    ok, err = await _eth_call(
        session, url, holder, token, _transfer_data(CONTROL_ADDR)
    )
    if not ok:
        log.debug("kontrol transferi basarisiz (%s): %s", token, err)
        return None

    # 2) Türetilmiş, HENÜZ VAR OLMAYAN havuz adreslerine transfer dene
    hedefler: list[tuple[str, str]] = []
    for isim, quote in QUOTES.items():
        hedefler.append((f"V2 {isim}", v2_pair(token, quote)))
    for fee in V3_FEES:
        hedefler.append((f"V3 %{fee/10000:g}", v3_pool(token, config.WBNB, fee)))

    engellenen = []
    for etiket, adres in hedefler:
        ok, err = await _eth_call(session, url, holder, token, _transfer_data(adres))
        if not ok:
            engellenen.append({"hedef": etiket, "adres": adres, "hata": err})
        await asyncio.sleep(0.05)

    if not engellenen:
        return None

    return {
        "engellenen_sayisi": len(engellenen),
        "toplam_denenen": len(hedefler),
        "ornekler": engellenen[:3],
    }


MAX_UINT = 2**256 - 1


async def owner_infinite_balance(
    session: aiohttp.ClientSession, token: str, owner: str
) -> Optional[int]:
    """Sahibin bakiyesi absürt derecede büyük mü?

    Bazı tuzaklarda `balanceOf(sahip)` sabit olarak 2^256-1 döner: sahibi
    havuzu istediği kadar boşaltabilir. Yakaladığımız tokenlarda bu görüldü.
    Dönen: bakiye (şüpheliyse) ya da None.
    """
    if not owner or not owner.startswith("0x"):
        return None
    url = config.HTTP_RPCS[0][0]
    bakiye = await _balance_of(session, url, token.lower(), owner.lower())
    # Toplam arzın çok üstündeki bir bakiye normal bir token'da olamaz
    return bakiye if bakiye > 10**36 else None


async def _balance_of(
    session: aiohttp.ClientSession, url: str, token: str, who: str
) -> int:
    data = "0x70a08231" + who[2:].lower().rjust(64, "0")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": token, "data": data}, "latest"],
    }
    try:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            out = await r.json(content_type=None)
        v = out.get("result")
        return int(v, 16) if v and v != "0x" else 0
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return 0


async def holder_sell_test(
    session: aiohttp.ClientSession,
    token: str,
    pair: str,
    holders: list[str],
    max_test: int = 6,
) -> Optional[dict]:
    """GERÇEK sahiplerin satabilip satamadığını sınar.

    Neden gerekli: en sinsi tuzak, alıcıları TEK TEK donduruyor. Temiz bir test
    cüzdanıyla yapılan simülasyon "satılabilir" der (o cüzdan yasaklı değildir),
    ama gerçek kurbanlar kilitlidir. Bu yüzden sahte cüzdan yerine zincirdeki
    gerçek sahiplerle deniyoruz — seçici kara listeler ancak böyle görülür.

    Dönen: None (test edilemedi) ya da {denenen, engellenen, oran, ornek}.
    """
    token, pair = token.lower(), pair.lower()
    url = config.HTTP_RPCS[0][0]

    denenen = 0
    engellenen = 0
    ornek: Optional[str] = None

    for h in holders:
        if denenen >= max_test:
            break
        h = (h or "").lower()
        if not h.startswith("0x") or len(h) != 42:
            continue
        if h in (pair, token, "0x" + "0" * 40):
            continue  # havuzun ve token'ın kendisi sayılmaz

        bakiye = await _balance_of(session, url, token, h)
        if bakiye <= 0:
            continue
        # Bakiyenin onda biriyle dene — miktar eşiği olan tuzaklara takılmamak için
        miktar = max(1, bakiye // 10)
        ok, err = await _eth_call(session, url, h, token, _transfer_data(pair, miktar))
        denenen += 1
        if not ok:
            engellenen += 1
            if ornek is None:
                ornek = f"{h[:10]}… satamıyor ({err[:40]})"
        await asyncio.sleep(0.05)

    if denenen < 3:
        return None  # anlamlı sonuç için yeterli örnek yok

    oran = engellenen / denenen
    return {
        "denenen": denenen,
        "engellenen": engellenen,
        "oran": round(oran, 2),
        "ornek": ornek,
    }
