"""Tarayıcı — yeni havuzu alır, honeypot testinden geçirir, kaydeder.

Akış:
    WebSocket/boşluk doldurma  →  kuyruk  →  N işçi  →  güvenlik analizi
                                                      →  veritabanı
                                                      →  deployer kümeleme
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp

from . import chain, config, db, notify, security

log = logging.getLogger("radar.scanner")

LAST_BLOCK_KEY = "last_scanned_block"
BSCSCAN_KEY = os.getenv("BSCSCAN_API_KEY", "").strip()

_seen: set[str] = set()  # aynı token birden çok havuzla gelirse tekrar taramayalım


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------ kayıt

async def save(pair: chain.NewPair, rep: security.TokenReport) -> None:
    existing = await db.row("SELECT scan_count FROM tokens WHERE address = ?", [rep.address])
    reasons = json.dumps(rep.reasons, ensure_ascii=False)
    now = _now()
    # honeypot.is havuz adresini zaten veriyor; işlem listesi için onu kullanırız
    pool = pair.pair or (rep.pair_address or "")

    if existing:
        await db.run(
            "UPDATE tokens SET verdict=?, buy_tax=?, sell_tax=?, reasons=?, "
            "holder_count=?, lp_locked_pct=?, scanned_at=?, scan_count=?, "
            "pair_address=COALESCE(NULLIF(pair_address,''), ?) WHERE address=?",
            [
                rep.verdict, rep.buy_tax, rep.sell_tax, reasons,
                rep.holder_count, rep.lp_locked_pct, now,
                int(existing.get("scan_count") or 0) + 1, pool, rep.address,
            ],
        )
    else:
        await db.run(
            "INSERT INTO tokens(address, symbol, name, pair_address, dex_version, "
            "found_at, block_number, creator, owner, verdict, buy_tax, sell_tax, "
            "reasons, holder_count, lp_locked_pct, scanned_at, scan_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            [
                rep.address, rep.symbol, rep.name, pool, pair.version,
                now, pair.block, rep.creator, rep.owner, rep.verdict,
                rep.buy_tax, rep.sell_tax, reasons, rep.holder_count,
                rep.lp_locked_pct, now,
            ],
        )

    crew_count = 0
    if rep.creator:
        await touch_deployer(rep.creator, rep.verdict == security.HONEYPOT)
        crew = await db.row(
            "SELECT honeypot_count FROM deployers WHERE address = ?", [rep.creator]
        )
        crew_count = int((crew or {}).get("honeypot_count") or 0)

    if rep.verdict == security.HONEYPOT:
        await notify.honeypot_found(rep, crew_count)


async def touch_deployer(address: str, is_honeypot: bool) -> None:
    """Deployer sayaçlarını günceller; ilk görüşte fon kaynağını çözmeye çalışır."""
    address = address.lower()
    existing = await db.row("SELECT * FROM deployers WHERE address = ?", [address])
    now = _now()
    if existing:
        await db.run(
            "UPDATE deployers SET last_seen=?, token_count=token_count+1, "
            "honeypot_count=honeypot_count+? WHERE address=?",
            [now, 1 if is_honeypot else 0, address],
        )
        return

    funder = await find_funder(address)
    await db.run(
        "INSERT INTO deployers(address, funder, first_seen, last_seen, "
        "token_count, honeypot_count) VALUES(?,?,?,?,1,?)",
        [address, funder, now, now, 1 if is_honeypot else 0],
    )


async def find_funder(address: str) -> str | None:
    """Bu cüzdana ilk BNB'yi kim gönderdi? (aynı ekibi tanımanın en iyi yolu)

    BscScan anahtarı yoksa sessizce atlanır — kümeleme yalnızca deployer
    bazında yapılır, uygulama yine çalışır.
    """
    if not BSCSCAN_KEY:
        return None
    url = "https://api.etherscan.io/v2/api"
    params = {
        "chainid": str(config.CHAIN_ID),
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": "0",
        "endblock": "99999999",
        "page": "1",
        "offset": "10",
        "sort": "asc",
        "apikey": BSCSCAN_KEY,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json(content_type=None)
        if str(data.get("status")) != "1":
            return None
        for tx in data.get("result") or []:
            if (tx.get("to") or "").lower() == address and int(tx.get("value") or 0) > 0:
                return (tx.get("from") or "").lower() or None
    except Exception as e:  # noqa: BLE001 — kümeleme olmasa da devam
        log.debug("funder bulunamadı (%s): %s", address, e)
    return None


# ------------------------------------------------------------ işçiler

async def worker(
    name: str, queue: "asyncio.Queue[chain.NewPair]", session: aiohttp.ClientSession
) -> None:
    while True:
        pair = await queue.get()
        try:
            if pair.token in _seen:
                continue
            _seen.add(pair.token)
            if len(_seen) > 50_000:
                _seen.clear()

            rep = await security.analyze(session, pair.token)
            await save(pair, rep)
            if rep.verdict == security.HONEYPOT:
                log.warning(
                    "HONEYPOT: %s (%s) al=%s sat=%s",
                    rep.symbol or rep.address, rep.address, rep.buy_tax, rep.sell_tax
                )
            else:
                log.info("%s: %s (%s)", rep.verdict, rep.symbol or rep.address, rep.address)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — tek token hatası tarayıcıyı durdurmaz
            log.exception("Token incelenirken hata: %s", pair.token)
        finally:
            queue.task_done()


async def gap_filler(queue: "asyncio.Queue[chain.NewPair]", session: aiohttp.ClientSession) -> None:
    """Servis kapalıyken/bağlantı koptuğunda kaçan blokları periyodik doldurur."""
    while True:
        try:
            head = await chain.latest_block(session)
            if head:
                last = await db.get_state(LAST_BLOCK_KEY)
                start = int(last) + 1 if last else head - 2000
                # Çok geriye gitmeyelim: ücretsiz RPC'ler ~1 günden eskisini vermiyor
                start = max(start, head - config.BLOCKS_PER_DAY)
                if head > start:
                    async for pair in chain.backfill(session, start, head):
                        await queue.put(pair)
                    await db.set_state(LAST_BLOCK_KEY, str(head))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Boşluk doldurma hatası")
        await asyncio.sleep(120)


async def _rescan_rows(session: aiohttp.ClientSession, rows: list[dict]) -> None:
    for t in rows:
        rep = await security.analyze(session, t["address"])
        await save(
            chain.NewPair(
                token=t["address"], base="", pair=t.get("pair_address") or "",
                block=int(t.get("block_number") or 0),
                version=t.get("dex_version") or "v2",
            ),
            rep,
        )


async def rescanner(session: aiohttp.ClientSession) -> None:
    """İki hızda yeniden tarama.

    1) "bilinmiyor" kalanlar: yeni token API'lerde henüz görünmüyordur, birkaç
       dakika sonra veri gelir. Bunları saatlerce bekletmek anlamsız.
    2) Diğerleri: 12 saatte bir — bugün temiz olan yarın vergiyi yükseltebilir.
    """
    while True:
        await asyncio.sleep(config.UNKNOWN_RETRY_MINUTES * 60)
        try:
            fresh_cut = _now() - timedelta(minutes=config.UNKNOWN_RETRY_MINUTES)
            unknowns = await db.rows(
                "SELECT address, pair_address, dex_version, block_number FROM tokens "
                "WHERE verdict = ? AND scan_count < ? AND scanned_at < ? "
                "ORDER BY found_at DESC LIMIT 30",
                [security.UNKNOWN, config.UNKNOWN_MAX_TRIES, fresh_cut],
            )
            if unknowns:
                log.info("%s yeni token yeniden deneniyor", len(unknowns))
                await _rescan_rows(session, unknowns)

            old_cut = _now() - timedelta(hours=config.RESCAN_AFTER_HOURS)
            stale = await db.rows(
                "SELECT address, pair_address, dex_version, block_number FROM tokens "
                "WHERE scanned_at < ? AND verdict != ? ORDER BY scanned_at ASC LIMIT 15",
                [old_cut, security.HONEYPOT],
            )
            if stale:
                await _rescan_rows(session, stale)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Yeniden tarama hatası")


async def run() -> None:
    """Tüm tarayıcı görevlerini başlatır (web sunucusuyla aynı süreçte)."""
    await db.init()
    queue: "asyncio.Queue[chain.NewPair]" = asyncio.Queue(maxsize=5000)
    session = aiohttp.ClientSession()
    tasks = [
        asyncio.create_task(chain.watch(queue)),
        asyncio.create_task(gap_filler(queue, session)),
        asyncio.create_task(rescanner(session)),
    ]
    sender = notify.start()
    if sender:
        tasks.append(sender)
    for i in range(config.SCAN_CONCURRENCY):
        tasks.append(asyncio.create_task(worker(f"w{i}", queue, session)))
    log.info("Tarayıcı çalışıyor (%s işçi)", config.SCAN_CONCURRENCY)
    try:
        await asyncio.gather(*tasks)
    finally:
        await session.close()
