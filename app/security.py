"""Honeypot tespiti — GoPlus + honeypot.is.

İki bağımsız kaynak kullanıyoruz çünkü tek kaynağa güvenmek riskli:
  • GoPlus  → kontrat analizi (vergiler, satış engeli, sahip yetkileri, creator)
  • honeypot.is → gerçek al/sat SİMÜLASYONU (asıl kanıt bu)

Kritik davranış: GoPlus limit aşımında HTTP 200 döndürüp gövdede
{"code": 4029} veriyor. Sadece durum koduna bakan kod sessizce bozulur.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from . import config

log = logging.getLogger("radar.security")

# Karar değerleri
CLEAN = "temiz"
RISKY = "riskli"
HONEYPOT = "honeypot"
UNKNOWN = "bilinmiyor"
YOUNG = "yeni"   # testleri geçti ama henüz kanıtlanmadı (az sahip / taze kontrat)

# "temiz" demek için en az bu kadar sahip olmalı. Altındakiler "yeni" sayılır:
# tuzakların çoğu ilk saatlerde kurulur, taze bir token'a temiz demek yanıltır.
PROVEN_HOLDERS = 50


# Puanlama: tek bir "kötü" bayrak damgalamaya yetmez, ciddi olanlar ağır basar.
# (Örn. CAKE mint edilebilir ama sağlam bir token — tek başına riskli sayılmamalı.)
HARD = 100      # tek başına honeypot demek
RISK_LIMIT = 2  # bu puandan itibaren "riskli"


@dataclass
class TokenReport:
    address: str
    verdict: str = UNKNOWN
    buy_tax: Optional[float] = None
    sell_tax: Optional[float] = None
    creator: Optional[str] = None
    owner: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    holder_count: Optional[int] = None
    lp_locked_pct: Optional[float] = None
    reasons: list[str] = field(default_factory=list)
    score: int = 0
    has_data: bool = False
    pair_address: Optional[str] = None


class RateLimiter:
    """Basit pencere tabanlı hız sınırlayıcı (kaç istek / kaç saniye)."""

    def __init__(self, calls: int, per_seconds: float):
        self.calls = calls
        self.per = per_seconds
        self._hits: list[float] = []
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._hits = [t for t in self._hits if now - t < self.per]
                if len(self._hits) < self.calls:
                    self._hits.append(now)
                    return
                await asyncio.sleep(self.per - (now - self._hits[0]) + 0.05)


_goplus_limit = RateLimiter(config.GOPLUS_CALLS_PER_MIN, 60.0)
_honeypot_limit = RateLimiter(config.HONEYPOT_CALLS_PER_5SEC, 5.0)


def _as_float(value) -> Optional[float]:
    """GoPlus vergileri '0.05' (oran) ya da '' olarak döndürür → yüzdeye çevir."""
    try:
        if value in (None, "", "-"):
            return None
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict) -> Optional[dict]:
    for attempt in range(3):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 429:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if r.status >= 500:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await asyncio.sleep(2 * (attempt + 1))
    return None


async def goplus(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """GoPlus token_security — anahtar gerekmez.

    Bilinmesi gereken üç davranış (canlı olarak doğrulandı):
      • Limit aşımı HTTP 200 + gövdede {"code": 4029} olarak gelir.
      • code=2 → "kısmi veri, ~15 sn sonra tekrar sor". Yeni token'larda normal.
      • Emin olamadığı alanları HİÇ göndermez; eksik alan "güvenli" demek DEĞİL.
    """
    url = config.GOPLUS_URL.format(chain_id=config.CHAIN_ID)
    for attempt in range(2):
        await _goplus_limit.wait()
        data = await _get_json(session, url, {"contract_addresses": address})
        if not data:
            return None
        code = str(data.get("code"))
        if code == "4029":
            log.warning("GoPlus hız limiti (4029) — yavaşlıyorum")
            await asyncio.sleep(5)
            return None
        if code == "2" and attempt == 0:
            # Analiz henüz bitmemiş; söylediği kadar bekleyip bir kez daha soruyoruz
            await asyncio.sleep(16)
            continue
        if code not in ("1", "2"):
            return None
        result = (data.get("result") or {}).get(address.lower())
        return result or None
    return None


async def honeypot_is(session: aiohttp.ClientSession, address: str) -> Optional[dict]:
    """honeypot.is v2 — gerçek al/sat simülasyonu. Anahtar gerekmez."""
    await _honeypot_limit.wait()
    return await _get_json(
        session, config.HONEYPOT_URL, {"address": address, "chainID": config.CHAIN_ID}
    )


def _read_goplus(rep: TokenReport, g: dict) -> None:
    # GoPlus emin olmadığı alanları HİÇ göndermez. Bu yüzden "veri geldi" demek
    # için anlamlı sayıda alan dönmüş olmalı; yoksa yanıt bize bir şey söylemiyor.
    if len({k for k in g if g.get(k) not in (None, "")}) >= 5:
        rep.has_data = True
    rep.symbol = g.get("token_symbol") or rep.symbol
    rep.name = g.get("token_name") or rep.name
    rep.creator = (g.get("creator_address") or "").lower() or rep.creator
    rep.owner = (g.get("owner_address") or "").lower() or rep.owner
    rep.buy_tax = _as_float(g.get("buy_tax"))
    rep.sell_tax = _as_float(g.get("sell_tax"))
    try:
        rep.holder_count = int(g.get("holder_count") or 0) or None
    except (TypeError, ValueError):
        pass

    # Kilitli likidite yüzdesi
    locked = 0.0
    for lp in g.get("lp_holders") or []:
        if str(lp.get("is_locked")) == "1":
            try:
                locked += float(lp.get("percent") or 0) * 100
            except (TypeError, ValueError):
                pass
    rep.lp_locked_pct = round(locked, 2) if locked else 0.0

    for flag, weight, text in (
        ("is_honeypot", HARD, "GoPlus: honeypot olarak işaretli"),
        ("cannot_sell_all", HARD, "Tamamını satamıyorsun"),
        ("transfer_pausable", 2, "Transferler durdurulabilir"),
        ("is_blacklisted", 2, "Kara liste fonksiyonu var"),
        ("owner_change_balance", 2, "Sahip bakiyeni değiştirebilir"),
        ("hidden_owner", 2, "Gizli sahip"),
        ("can_take_back_ownership", 2, "Sahiplik geri alınabilir"),
        ("selfdestruct", 2, "Kontrat kendini yok edebilir"),
        ("is_open_source", 1, "Kaynak kodu doğrulanmamış"),
        ("is_mintable", 1, "Sınırsız basım (mint) açık"),
    ):
        raw = str(g.get(flag))
        # is_open_source ters çalışır: "0" = doğrulanmamış
        hit = (raw == "0") if flag == "is_open_source" else (raw == "1")
        if hit:
            rep.reasons.append(text)
            rep.score += weight


def _read_honeypot_is(rep: TokenReport, h: dict) -> None:
    """honeypot.is — BİRİNCİL kaynak.

    Gerçek al/sat simülasyonu yaptığı için proxy kontratlı tuzakları da yakalıyor;
    GoPlus'ın kaçırdığı gerçek bir honeypot bununla tespit edildi.
    """
    res = h.get("honeypotResult") or {}
    sim = h.get("simulationResult") or {}
    summary = h.get("summary") or {}
    code_info = h.get("contractCode") or {}
    token = h.get("token") or {}

    if h.get("simulationSuccess") is not None:
        rep.has_data = True

    if res.get("isHoneypot") is True:
        rep.reasons.append("Simülasyon: SATILAMIYOR")
        rep.score += HARD
        rep.has_data = True
    reason = res.get("honeypotReason")
    if reason and reason not in " ".join(rep.reasons):
        rep.reasons.append(f"Simülasyon: {reason}")

    if str(summary.get("risk")) == "honeypot":
        rep.score += HARD
        rep.has_data = True
    try:
        level = int(summary.get("riskLevel") or 0)
    except (TypeError, ValueError):
        level = 0
    if level >= 75:
        rep.score += 2
    elif level >= 50:
        rep.score += 1

    for f in summary.get("flags") or []:
        desc = (f or {}).get("description")
        sev = (f or {}).get("severity")
        if desc:
            rep.reasons.append(f"{desc}")
        if sev == "high":
            rep.score += 2

    # Simülasyon vergileri daha güvenilir — varsa GoPlus'ınkini eziyor
    for key, attr in (("buyTax", "buy_tax"), ("sellTax", "sell_tax")):
        val = sim.get(key)
        if isinstance(val, (int, float)):
            setattr(rep, attr, round(float(val), 2))
            rep.has_data = True

    if code_info.get("openSource") is False:
        rep.reasons.append("Kaynak kodu doğrulanmamış")
        rep.score += 1
    if code_info.get("isProxy") is True:
        rep.reasons.append("Proxy kontrat (mantık sonradan değiştirilebilir)")
        rep.score += 2

    # Sahip sayısı ve havuz adresi buradan da geliyor — ek istek gerekmiyor
    try:
        holders = int(token.get("totalHolders") or 0)
        if holders:
            rep.holder_count = holders
    except (TypeError, ValueError):
        pass
    rep.symbol = token.get("symbol") or rep.symbol
    rep.name = token.get("name") or rep.name
    rep.pair_address = h.get("pairAddress") or rep.pair_address

    analysis = h.get("holderAnalysis") or {}
    try:
        failed = int(analysis.get("failed") or 0)
        successful = int(analysis.get("successful") or 0)
        if failed and successful + failed >= 20 and failed / (successful + failed) > 0.15:
            rep.reasons.append(
                f"Sahiplerin %{round(failed * 100 / (successful + failed))}'i satamamış"
            )
            rep.score += 2
    except (TypeError, ValueError, ZeroDivisionError):
        pass


def _decide(rep: TokenReport) -> str:
    """Puan + vergilerden tek karara varır.

    Önemli ayrım: 'veri yok' ile 'temiz' aynı şey DEĞİL. Yeni açılan token'lar
    henüz indekslenmemiş olur; onlara temiz demek insanları yanıltır — bilinmiyor
    deyip kısa süre sonra yeniden tararız.
    """
    taxes = [t for t in (rep.buy_tax, rep.sell_tax) if t is not None]

    if rep.score >= HARD:
        return HONEYPOT
    if taxes and max(taxes) >= config.DEADLY_TAX:
        return HONEYPOT  # %50+ satış vergisi pratikte satamamakla aynı
    if not rep.has_data:
        return UNKNOWN   # hiçbir kaynak veri vermedi
    if taxes and max(taxes) >= config.RISKY_TAX:
        return RISKY
    if rep.score >= RISK_LIMIT:
        return RISKY
    if not rep.holder_count or rep.holder_count < PROVEN_HOLDERS:
        return YOUNG  # testleri geçti ama henüz kimse denememiş
    return CLEAN


async def analyze(session: aiohttp.ClientSession, address: str) -> TokenReport:
    """Token'ı iki kaynakla inceleyip tek bir karara bağlar."""
    address = address.lower()
    rep = TokenReport(address=address)

    g, h = await asyncio.gather(
        goplus(session, address),
        honeypot_is(session, address),
        return_exceptions=True,
    )
    if isinstance(g, dict):
        _read_goplus(rep, g)
    if isinstance(h, dict):
        _read_honeypot_is(rep, h)

    rep.verdict = _decide(rep)
    return rep
