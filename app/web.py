"""Web arayüzü — FastAPI.

Sayfalar:
    /                    canlı honeypot akışı
    /token/{adres}       token detayı (güvenlik + kim ne almış)
    /scammer/{adres}     deployer profili (bu ekibin tüm token'ları)
    /api/feed            JSON akış
    /health              sağlık kontrolü (Render uyanık tutucu da bunu çağırır)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import config, db, market, scanner, security

log = logging.getLogger("radar.web")

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_tasks: list[asyncio.Task] = []
_session: aiohttp.ClientSession | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    await db.init()
    _session = aiohttp.ClientSession()
    _tasks.append(asyncio.create_task(scanner.run()))
    if config.SELF_URL:
        _tasks.append(asyncio.create_task(_self_ping()))

    # Kanal temizlik botu aynı süreçte çalışır (BOT_TOKEN tanımlıysa).
    # Neden birlikte: iki ayrı servis ücretsiz barındırma kotasını ikiye
    # katlıyor (ayda 1460 saat); tek süreçte 730 saate düşüp kotaya sığıyor.
    if os.getenv("BOT_TOKEN", "").strip():
        from . import temizlik

        _tasks.append(asyncio.create_task(_supervised(temizlik.run_polling, "temizlik-botu")))

    log.info("Radar açıldı")
    try:
        yield
    finally:
        for t in _tasks:
            t.cancel()
        if _session:
            await _session.close()


async def _supervised(coro_fn, isim: str) -> None:
    """Bir görev çökerse tüm süreci düşürmesin — bekleyip yeniden başlatır.

    İki servis tek süreçte yaşadığı için bu şart: temizlik botundaki bir hata
    radarı da öldürmemeli (ve tersi).
    """
    bekleme = 5
    while True:
        try:
            await coro_fn()
            log.warning("%s beklenmedik şekilde bitti, yeniden başlatılıyor", isim)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s çöktü, %s sn sonra yeniden denenecek", isim, bekleme)
        await asyncio.sleep(bekleme)
        bekleme = min(bekleme * 2, 300)


async def _self_ping() -> None:
    """Render ücretsiz planında uykuya geçmeyi önler."""
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(config.SELF_URL, timeout=aiohttp.ClientTimeout(total=20))
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Honeypot Radar", lifespan=lifespan)


def _short(addr: str | None) -> str:
    if not addr:
        return "—"
    return f"{addr[:6]}…{addr[-4:]}"


templates.env.filters["short"] = _short


async def _stats() -> dict:
    total = await db.row("SELECT COUNT(*) AS n FROM tokens")
    hp = await db.row("SELECT COUNT(*) AS n FROM tokens WHERE verdict = ?", [security.HONEYPOT])
    crews = await db.row(
        "SELECT COUNT(*) AS n FROM deployers WHERE honeypot_count > 0"
    )
    return {
        "total": (total or {}).get("n", 0),
        "honeypots": (hp or {}).get("n", 0),
        "crews": (crews or {}).get("n", 0),
    }


async def _feed(only: str | None, limit: int) -> list[dict]:
    if only in (security.HONEYPOT, security.RISKY, security.CLEAN):
        rows = await db.rows(
            "SELECT * FROM tokens WHERE verdict = ? ORDER BY found_at DESC LIMIT ?",
            [only, limit],
        )
    else:
        rows = await db.rows(
            "SELECT * FROM tokens ORDER BY found_at DESC LIMIT ?", [limit]
        )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["reasons"] = json.loads(d.get("reasons") or "[]")
        except json.JSONDecodeError:
            d["reasons"] = []
        dep = None
        if d.get("creator"):
            dep = await db.row(
                "SELECT honeypot_count, token_count FROM deployers WHERE address = ?",
                [d["creator"]],
            )
        d["crew_honeypots"] = (dep or {}).get("honeypot_count", 0)
        out.append(d)
    return out


@app.get("/health")
async def health():
    return {"ok": True, "service": "honeypot-radar"}


@app.get("/api/feed")
async def api_feed(only: str | None = None, limit: int = config.FEED_LIMIT):
    return JSONResponse(
        {"stats": await _stats(), "items": await _feed(only, min(limit, 300))}
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, only: str | None = None):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": await _stats(),
            "items": await _feed(only, config.FEED_LIMIT),
            "only": only or "",
        },
    )


@app.get("/token/{address}", response_class=HTMLResponse)
async def token_page(request: Request, address: str):
    address = address.lower()
    row = await db.row("SELECT * FROM tokens WHERE address = ?", [address])

    reasons: list[str] = []
    if row:
        try:
            reasons = json.loads(row.get("reasons") or "[]")
        except json.JSONDecodeError:
            reasons = []

    # Henüz taranmamış bir adres sorulduysa anında tara
    if not row and _session:
        rep = await security.analyze(_session, address)
        row = {
            "address": address, "symbol": rep.symbol, "name": rep.name,
            "verdict": rep.verdict, "buy_tax": rep.buy_tax, "sell_tax": rep.sell_tax,
            "creator": rep.creator, "holder_count": rep.holder_count,
            "lp_locked_pct": rep.lp_locked_pct, "pair_address": None,
        }
        reasons = rep.reasons

    trades: list[dict] = []
    summary = {"buys": 0, "sells": 0, "no_sells": False}
    pool = (row or {}).get("pair_address")
    if _session and row:
        if not pool:
            best = await market.best_pool(_session, address)
            pool = ((best or {}).get("attributes") or {}).get("address")
        if pool:
            trades = await market.trades(_session, pool)
            summary = market.buy_sell_summary(trades)

    crew = None
    if row and row.get("creator"):
        crew = await db.row("SELECT * FROM deployers WHERE address = ?", [row["creator"]])

    return templates.TemplateResponse(
        request,
        "token.html",
        {
            "t": row, "reasons": reasons, "trades": trades,
            "summary": summary, "crew": crew, "address": address,
        },
    )


@app.get("/scammer/{address}", response_class=HTMLResponse)
async def scammer_page(request: Request, address: str):
    address = address.lower()
    crew = await db.row("SELECT * FROM deployers WHERE address = ?", [address])
    tokens = await db.rows(
        "SELECT * FROM tokens WHERE creator = ? ORDER BY found_at DESC LIMIT 200",
        [address],
    )
    siblings: list[dict] = []
    if crew and crew.get("funder"):
        siblings = await db.rows(
            "SELECT * FROM deployers WHERE funder = ? AND address != ? LIMIT 50",
            [crew["funder"], address],
        )
    return templates.TemplateResponse(
        request,
        "scammer.html",
        {"crew": crew, "address": address, "tokens": tokens, "siblings": siblings},
    )
