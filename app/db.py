"""Veri katmanı.

DATABASE_URL tanımlıysa Postgres (Supabase), değilse yerel SQLite kullanılır.

NOT: Render'ın ücretsiz planında kalıcı disk YOK — orada SQLite her yeniden
başlatmada sıfırlanır. Üretimde mutlaka DATABASE_URL verilmeli.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from . import config

_pg_pool = None
_pg_lock = asyncio.Lock()


def is_postgres() -> bool:
    return bool(config.DATABASE_URL)


# --------------------------------------------------------------- şema

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS tokens (
        address        TEXT PRIMARY KEY,
        symbol         TEXT,
        name           TEXT,
        pair_address   TEXT,
        dex_version    TEXT,
        found_at       TIMESTAMP NOT NULL,
        block_number   BIGINT,
        creator        TEXT,
        owner          TEXT,
        verdict        TEXT,
        buy_tax        DOUBLE PRECISION,
        sell_tax       DOUBLE PRECISION,
        reasons        TEXT,
        holder_count   INTEGER,
        lp_locked_pct  DOUBLE PRECISION,
        scanned_at     TIMESTAMP,
        scan_count     INTEGER DEFAULT 0,
        alerted        INTEGER DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tokens_found ON tokens(found_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tokens_verdict ON tokens(verdict)",
    "CREATE INDEX IF NOT EXISTS idx_tokens_creator ON tokens(creator)",
    """
    CREATE TABLE IF NOT EXISTS deployers (
        address        TEXT PRIMARY KEY,
        funder         TEXT,
        first_seen     TIMESTAMP,
        last_seen      TIMESTAMP,
        token_count    INTEGER DEFAULT 0,
        honeypot_count INTEGER DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_deployers_funder ON deployers(funder)",
    """
    CREATE TABLE IF NOT EXISTS state (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
]


# ----------------------------------------------------------- SQLite yolu

@contextmanager
def _sqlite():
    conn = sqlite3.connect(config.SQLITE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sqlite_run(query: str, params: list) -> None:
    with _sqlite() as c:
        c.execute(query, params)


def _sqlite_rows(query: str, params: list) -> list[dict]:
    with _sqlite() as c:
        return [dict(r) for r in c.execute(query, params).fetchall()]


# --------------------------------------------------------- Postgres yolu

async def _pool():
    global _pg_pool
    if _pg_pool is None:
        async with _pg_lock:
            if _pg_pool is None:
                import asyncpg

                _pg_pool = await asyncpg.create_pool(
                    config.DATABASE_URL, min_size=1, max_size=4, command_timeout=30
                )
    return _pg_pool


def _numbered(query: str) -> str:
    """'?' yer tutucularını asyncpg'nin beklediği $1, $2 ... biçimine çevirir."""
    out, i = query, 0
    while "?" in out:
        i += 1
        out = out.replace("?", f"${i}", 1)
    return out


# ------------------------------------------------------------ genel API

# Şema sonradan büyüdüğünde eski veritabanlarını da güncel tutar.
# (Hata verirse sütun zaten vardır — yok sayılır.)
MIGRATIONS = [
    "ALTER TABLE tokens ADD COLUMN alerted INTEGER DEFAULT 0",
]


async def init() -> None:
    """Tabloları oluşturur (varsa dokunmaz) ve eksik sütunları ekler."""
    if is_postgres():
        pool = await _pool()
        async with pool.acquire() as c:
            for q in SCHEMA:
                await c.execute(q)
            for q in MIGRATIONS:
                try:
                    await c.execute(q)
                except Exception:  # noqa: BLE001 — sütun zaten var
                    pass
    else:
        def _create():
            with _sqlite() as c:
                for q in SCHEMA:
                    # SQLite'ta bu tipler yok; sorunsuz karşılıklarına indiriyoruz
                    c.execute(
                        q.replace("DOUBLE PRECISION", "REAL").replace("BIGINT", "INTEGER")
                    )
                for q in MIGRATIONS:
                    try:
                        c.execute(q)
                    except sqlite3.OperationalError:
                        pass  # sütun zaten var

        await asyncio.to_thread(_create)


async def run(query: str, params: Iterable[Any] = ()) -> None:
    params = list(params)
    if is_postgres():
        pool = await _pool()
        async with pool.acquire() as c:
            await c.execute(_numbered(query), *params)
    else:
        await asyncio.to_thread(_sqlite_run, query, params)


async def rows(query: str, params: Iterable[Any] = ()) -> list[dict]:
    params = list(params)
    if is_postgres():
        pool = await _pool()
        async with pool.acquire() as c:
            found = await c.fetch(_numbered(query), *params)
            return [dict(r) for r in found]
    return await asyncio.to_thread(_sqlite_rows, query, params)


async def row(query: str, params: Iterable[Any] = ()) -> Optional[dict]:
    found = await rows(query, params)
    return found[0] if found else None


# --------------------------------------------------------- durum anahtarı

async def get_state(key: str) -> Optional[str]:
    found = await row("SELECT value FROM state WHERE key = ?", [key])
    return found["value"] if found else None


async def set_state(key: str, value: str) -> None:
    await run(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        [key, value],
    )
