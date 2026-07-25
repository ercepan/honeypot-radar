"""Zincir katmanı — yeni havuzları yakalar.

Canlı akış WebSocket aboneliğiyle gelir (anahtarsız publicnode).
Bağlantı koparsa/servis yeniden başlarsa aradaki boşluk HTTP RPC ile doldurulur.

Neden iki yol birden? BSC 0.45 sn/blok hızında; publicnode yalnızca ~98 blok
(≈44 saniye) geriye log veriyor. Yani 1 dakikalık bir kopma bile veri kaybettirir;
boşluğu daha geniş aralık destekleyen başka RPC'lerden kapatıyoruz.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import aiohttp

from . import config

log = logging.getLogger("radar.chain")

# Yeni token'ın karşısındaki "taban" varlıklar — bunlar yeni token değildir
BASE_TOKENS = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
}


@dataclass
class NewPair:
    token: str          # incelenecek yeni token
    base: str           # karşısındaki taban varlık
    pair: str           # havuz adresi
    block: int
    version: str        # "v2" | "v3"


def _addr(topic_or_word: str) -> str:
    """32 baytlık kelimenin son 20 baytını adrese çevirir."""
    return "0x" + topic_or_word[-40:].lower()


def parse_log(entry: dict) -> Optional[NewPair]:
    """PairCreated / PoolCreated logunu NewPair'e çevirir."""
    topics = entry.get("topics") or []
    if len(topics) < 3:
        return None
    topic0 = topics[0].lower()
    data = (entry.get("data") or "0x")[2:]

    token0, token1 = _addr(topics[1]), _addr(topics[2])

    if topic0 == config.TOPIC_PAIR_CREATED.lower():
        version = "v2"
        pair = _addr(data[:64]) if len(data) >= 64 else ""
    elif topic0 == config.TOPIC_POOL_CREATED.lower():
        version = "v3"
        # data: tickSpacing (32 bayt) + pool (32 bayt)
        pair = _addr(data[64:128]) if len(data) >= 128 else ""
    else:
        return None

    known0, known1 = token0 in BASE_TOKENS, token1 in BASE_TOKENS
    if known0 and known1:
        return None  # iki taban varlık — yeni token yok
    if known0:
        token, base = token1, token0
    elif known1:
        token, base = token0, token1
    else:
        # İkisi de tanınmıyorsa token0'ı inceleyelim; token1 de sıraya girer
        token, base = token0, token1

    try:
        block = int(entry.get("blockNumber", "0x0"), 16)
    except (TypeError, ValueError):
        block = 0

    return NewPair(token=token, base=base, pair=pair, block=block, version=version)


# ------------------------------------------------------------- HTTP RPC

async def _rpc(session: aiohttp.ClientSession, url: str, method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(
        url, json=payload, timeout=aiohttp.ClientTimeout(total=40)
    ) as r:
        if r.status != 200:
            raise RuntimeError(f"{url} HTTP {r.status}")
        data = await r.json(content_type=None)
        if "error" in data:
            raise RuntimeError(f"{url} {data['error'].get('message')}")
        return data.get("result")


async def latest_block(session: aiohttp.ClientSession) -> Optional[int]:
    for url, _ in config.HTTP_RPCS:
        try:
            res = await _rpc(session, url, "eth_blockNumber", [])
            return int(res, 16)
        except Exception as e:  # noqa: BLE001 — sıradaki RPC denenecek
            log.debug("blockNumber olmadı (%s): %s", url, e)
    return None


async def fetch_logs(
    session: aiohttp.ClientSession, from_block: int, to_block: int
) -> list[dict]:
    """Verilen blok aralığındaki yeni havuz loglarını çeker.

    Her RPC'nin aralık limiti farklı; limite uyacak şekilde parçalayıp deniyoruz.
    """
    topics = [[config.TOPIC_PAIR_CREATED, config.TOPIC_POOL_CREATED]]
    addresses = [config.V2_FACTORY, config.V3_FACTORY]

    for url, max_span in config.HTTP_RPCS:
        try:
            out: list[dict] = []
            start = from_block
            while start <= to_block:
                end = min(start + max_span - 1, to_block)
                res = await _rpc(
                    session,
                    url,
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "address": addresses,
                            "topics": topics,
                        }
                    ],
                )
                out.extend(res or [])
                start = end + 1
            return out
        except Exception as e:  # noqa: BLE001
            log.info("getLogs başarısız (%s): %s — sıradaki RPC", url, e)
    return []


# ------------------------------------------------------------- WebSocket

async def watch(queue: "asyncio.Queue[NewPair]") -> None:
    """Yeni havuzları WebSocket'ten dinleyip kuyruğa koyar; koparsa yeniden bağlanır.

    Kopma sırasında kaçan bloklar `scanner` tarafından HTTP ile doldurulur —
    burada sadece son görülen blok numarasını kuyruğa akıtıyoruz.
    """
    import websockets

    backoff = 1
    while True:
        try:
            async with websockets.connect(
                config.WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5
            ) as ws:
                sub = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {
                            "address": [config.V2_FACTORY, config.V3_FACTORY],
                            "topics": [
                                [config.TOPIC_PAIR_CREATED, config.TOPIC_POOL_CREATED]
                            ],
                        },
                    ],
                }
                await ws.send(json.dumps(sub))
                ack = json.loads(await ws.recv())
                if "result" not in ack:
                    raise RuntimeError(f"abonelik reddedildi: {ack}")
                log.info("WebSocket aboneliği açıldı: %s", ack["result"])
                backoff = 1

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    entry = (msg.get("params") or {}).get("result")
                    if not isinstance(entry, dict):
                        continue
                    pair = parse_log(entry)
                    if pair:
                        await queue.put(pair)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — her kopmada yeniden bağlan
            log.warning("WebSocket koptu (%s) — %s sn sonra yeniden", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def backfill(
    session: aiohttp.ClientSession, from_block: int, to_block: int
) -> AsyncIterator[NewPair]:
    """Kopma sırasında kaçan blokları tarar."""
    if to_block <= from_block:
        return
    span = to_block - from_block
    log.info("Boşluk dolduruluyor: %s → %s (%s blok)", from_block, to_block, span)
    for entry in await fetch_logs(session, from_block, to_block):
        pair = parse_log(entry)
        if pair:
            yield pair
