"""Piyasa verisi — "kim ne almış" (son işlemler) ve havuz bilgisi.

Kaynak: GeckoTerminal genel API (anahtarsız).

DİKKAT — ölçülmüş gerçek: belgelerde "30 çağrı/dk" yazsa da pratikte çok daha
sert davranıyor; arka arkaya 40 istekten yalnızca ilk 3'ü geçiyor, gerisi 429.
Bu yüzden burada agresif önbellek + tek sıra (serialize) + geri çekilme var.
Önbellek olmadan token sayfası ilk birkaç ziyaretten sonra boş kalırdı.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp

log = logging.getLogger("radar.market")

BASE = "https://api.geckoterminal.com/api/v2/networks/bsc"

CACHE_TTL = 90.0        # saniye — aynı havuzu bu süre içinde tekrar sormayız
MIN_GAP = 2.5           # iki istek arası en az bekleme
_cache: dict[str, tuple[float, Any]] = {}
_lock = asyncio.Lock()
_last_call = 0.0
_cooldown_until = 0.0   # 429 yedikten sonra bu ana kadar hiç istek atma


def _cached(key: str) -> Optional[Any]:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)
    if len(_cache) > 500:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]
        for k, _ in oldest:
            _cache.pop(k, None)


async def _get(session: aiohttp.ClientSession, path: str) -> Optional[dict]:
    """Sıraya girerek, önbellekli ve 429'a saygılı istek."""
    global _last_call, _cooldown_until

    cached = _cached(path)
    if cached is not None:
        return cached

    async with _lock:
        cached = _cached(path)  # sırada beklerken başkası doldurmuş olabilir
        if cached is not None:
            return cached

        now = time.monotonic()
        if now < _cooldown_until:
            return None  # ceza süresindeyiz, boşuna deneme
        gap = now - _last_call
        if gap < MIN_GAP:
            await asyncio.sleep(MIN_GAP - gap)

        try:
            async with session.get(
                f"{BASE}{path}", timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                _last_call = time.monotonic()
                if r.status == 429:
                    _cooldown_until = time.monotonic() + 60
                    log.info("GeckoTerminal 429 — 60 sn ara veriliyor")
                    return None
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    _store(path, data)
    return data


async def best_pool(session: aiohttp.ClientSession, token: str) -> Optional[dict]:
    """Token'ın en likit havuzunu bulur (işlem listesi havuz bazlı çalışıyor)."""
    data = await _get(session, f"/tokens/{token}/pools")
    if not data:
        return None
    pools = data.get("data") or []
    if not pools:
        return None

    def liquidity(p: dict) -> float:
        try:
            return float((p.get("attributes") or {}).get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    return max(pools, key=liquidity)


async def trades(session: aiohttp.ClientSession, pool_address: str, limit: int = 30) -> list[dict]:
    """Havuzun son işlemleri — alıcı cüzdanıyla birlikte."""
    data = await _get(session, f"/pools/{pool_address}/trades")
    if not data:
        return []
    out = []
    for t in (data.get("data") or [])[:limit]:
        a = t.get("attributes") or {}
        kind = a.get("kind")  # "buy" | "sell"
        try:
            usd = float(a.get("volume_in_usd") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        out.append(
            {
                "kind": "alım" if kind == "buy" else "satım",
                "is_buy": kind == "buy",
                "usd": usd,
                "wallet": (a.get("tx_from_address") or "").lower(),
                "time": a.get("block_timestamp") or "",
                "tx": a.get("tx_hash") or "",
            }
        )
    return out


def buy_sell_summary(rows: list[dict]) -> dict:
    """Alım/satım dengesi — honeypot'un en net davranışsal izi."""
    buys = sum(1 for r in rows if r["is_buy"])
    sells = len(rows) - buys
    return {
        "buys": buys,
        "sells": sells,
        "no_sells": bool(rows) and sells == 0 and buys >= 5,
    }
