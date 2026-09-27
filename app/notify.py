"""Telegram uyarıları.

Yeni bir honeypot yakalandığında (ve bilinen bir dolandırıcı ekibi yeni token
açtığında) anında mesaj gönderir.

TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değilse sessizce devre dışı kalır —
uygulama yine çalışır.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

import aiohttp

from . import config, db, security

log = logging.getLogger("radar.notify")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Uyarılardaki "Detay →" linkinin adresi.
# SITE_URL kendi başına durur: Render'da RENDER_EXTERNAL_URL kendiliğinden gelir,
# VDS'te ise SITE_URL verilir. İkisi ayrı, çünkü RENDER_EXTERNAL_URL aynı zamanda
# uyku önleyici self-ping'i açıyor — VDS'te ona gerek yok.
SITE_URL = (
    os.getenv("SITE_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
).rstrip("/")

# Telegram sohbet başına ~1 mesaj/sn kabul eder; kuyrukla sıraya sokuyoruz.
SEND_GAP = 1.2

# Açılışta biriken eski kayıtlar için toplu bildirim yağmuru olmasın diye üst sınır
MAX_BURST = 8

_queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=200)
_worker_started = False


def enabled() -> bool:
    return bool(TOKEN and CHAT_ID)


def _fmt(rep: security.TokenReport, crew_count: int = 0) -> str:
    sym = html.escape(rep.symbol or "?")
    name = html.escape((rep.name or "")[:40])
    lines = [
        "🚨 <b>HONEYPOT</b>",
        "",
        f"<b>{sym}</b> {name}".strip(),
        f"<code>{rep.address}</code>",
        "",
    ]
    if rep.sell_tax is not None:
        lines.append(f"Satış vergisi: <b>%{rep.sell_tax:g}</b>")
    if rep.buy_tax is not None:
        lines.append(f"Alış vergisi: %{rep.buy_tax:g}")
    if rep.holder_count:
        lines.append(f"Sahip sayısı: {rep.holder_count}")

    if rep.reasons:
        lines.append("")
        for r in rep.reasons[:3]:
            lines.append(f"• {html.escape(r)}")

    if rep.creator:
        lines.append("")
        lines.append(f"Çıkaran: <code>{rep.creator}</code>")
        if crew_count >= 2:
            lines.append(f"⚠️ <b>Bu cüzdanın {crew_count}. tuzağı</b>")

    if SITE_URL:
        lines.append("")
        lines.append(f'<a href="{SITE_URL}/token/{rep.address}">Detay →</a>')

    return "\n".join(lines)


async def _sender() -> None:
    """Kuyruğu boşaltan tek işçi — Telegram hız limitine takılmamak için."""
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        while True:
            text = await _queue.get()
            try:
                # Her deneme AYRI korunuyor: geçici bir ağ hatası (connection
                # reset / timeout) tüm denemeleri iptal edip uyarıyı kaybetmesin.
                for attempt in range(4):
                    try:
                        async with session.post(
                            url,
                            json={
                                "chat_id": CHAT_ID,
                                "text": text,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True,
                            },
                            timeout=aiohttp.ClientTimeout(total=25),
                        ) as r:
                            data = await r.json(content_type=None)
                        if data.get("ok"):
                            break
                        wait = (data.get("parameters") or {}).get("retry_after")
                        if wait:
                            await asyncio.sleep(float(wait) + 1)
                            continue
                        log.warning("Telegram reddetti: %s", data.get("description"))
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # ağ hatası — bekleyip yeniden dene
                        if attempt == 3:
                            raise
                        await asyncio.sleep(2 * (attempt + 1))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — bildirim hatası tarayıcıyı durdurmaz
                log.exception("Uyarı gönderilemedi")
            finally:
                _queue.task_done()
            await asyncio.sleep(SEND_GAP)


def start() -> Optional[asyncio.Task]:
    global _worker_started
    if not enabled() or _worker_started:
        return None
    _worker_started = True
    log.info("Telegram uyarıları açık")
    return asyncio.create_task(_sender())


async def already_alerted(address: str) -> bool:
    row = await db.row("SELECT alerted FROM tokens WHERE address = ?", [address])
    return bool(row and row.get("alerted"))


async def mark_alerted(address: str) -> None:
    await db.run("UPDATE tokens SET alerted = 1 WHERE address = ?", [address])


async def honeypot_found(rep: security.TokenReport, crew_count: int = 0) -> None:
    """Yeni honeypot uyarısı — aynı token için yalnızca bir kez gönderilir."""
    if not enabled():
        return
    if await already_alerted(rep.address):
        return
    await mark_alerted(rep.address)
    try:
        _queue.put_nowait(_fmt(rep, crew_count))
    except asyncio.QueueFull:
        log.warning("Uyarı kuyruğu dolu, atlandı: %s", rep.address)


async def send_text(text: str) -> None:
    """Serbest metin (test/başlangıç mesajı)."""
    if not enabled():
        return
    try:
        _queue.put_nowait(text)
    except asyncio.QueueFull:
        pass
