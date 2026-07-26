"""Ayarlar ve zincir sabitleri.

Buradaki adres/limit değerlerinin tamamı canlı olarak doğrulandı (Temmuz 2026).
Değiştirmeden önce iki kez düşün — özellikle blok süresi ve RPC pencereleri.
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

# ----------------------------------------------------------------- zincir

CHAIN_ID = 56  # BSC mainnet

# PancakeSwap fabrikaları (zincir üzerinde eth_call ile doğrulandı)
V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"

# Event topic0'ları (keccak256 ile hesaplandı, canlı logla eşleşti)
# PairCreated(address indexed token0, address indexed token1, address pair, uint256)
TOPIC_PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
# PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee,
#             int24 tickSpacing, address pool)
TOPIC_POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"

# BSC artık 0.45 sn/blok (Fermi hard fork) → günde ~191.957 blok.
# Eski 3 sn varsayımıyla yapılan her hesap 6-7 kat yanlış olur.
BLOCK_TIME_SEC = 0.45
BLOCKS_PER_DAY = int(86400 / BLOCK_TIME_SEC)

# WebSocket: anahtarsız, eth_subscribe destekliyor (canlı test edildi)
WS_URL = os.getenv("BSC_WS_URL", "wss://bsc-rpc.publicnode.com")

# HTTP RPC'ler — her birinin eth_getLogs blok aralığı limiti FARKLI (ölçüldü).
# Boşluk doldururken sıralı denenir; ilki dar ama hızlı, sonrakiler geniş.
HTTP_RPCS = [
    # (url, tek sorguda izin verilen maksimum blok aralığı)
    ("https://rpc-bsc.48.club", 5000),          # ~1 günlük derinlik
    ("https://bsc.rpc.blxrbdn.com", 100000),    # geniş aralık, ~23 sn sürüyor
    ("https://bsc-dataseed.binance.org", 1000),
]

# publicnode yalnızca ~98 blok (≈44 sn) geriye log veriyor; canlı akış için
# harika, geçmişi doldurmak için işe yaramaz. Bu yüzden listede yok.

# --------------------------------------------------------------- güvenlik

# GoPlus: API anahtarı GEREKMİYOR, 30 çağrı/dk.
# DİKKAT: limit aşılınca HTTP 200 döner ama gövdede {"code":4029} olur.
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
GOPLUS_CALLS_PER_MIN = 25  # 30'un altında tutuyoruz, emniyet payı

# honeypot.is: anahtarsız, ölçülen limit ~50 istek / 5 saniye
HONEYPOT_URL = "https://api.honeypot.is/v2/IsHoneypot"
HONEYPOT_CALLS_PER_5SEC = 30

# Vergi eşikleri — bunun üstü "riskli" sayılır (yüzde)
RISKY_TAX = 15.0
DEADLY_TAX = 50.0

# ------------------------------------------------------------ veritabanı

# Boşsa yerel SQLite kullanılır (geliştirme). Üretimde Supabase Postgres URL'i.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH", "honeypot_radar.db")

# --------------------------------------------------------------- çalışma

# Tarayıcı kaç yeni token'ı aynı anda incelesin (API limitleri belirliyor)
SCAN_CONCURRENCY = 3

# Token'ları periyodik yeniden tara: "bugün temiz" yarın honeypot olabilir
RESCAN_AFTER_HOURS = 12

# Yeni açılan token'lar API'lerde henüz indekslenmemiş olur; ilk taramada
# güvenlik verisi eksik gelir. Bu yüzden taze token'lar kısa aralıklarla
# birkaç kez yeniden taranır — geç gelen honeypot bayrağını kaçırmamak için.
UNKNOWN_RETRY_MINUTES = 8
UNKNOWN_MAX_TRIES = 6
YOUNG_WATCH_HOURS = 3   # bu yaştan taze token'lar tekrar tekrar kontrol edilir

# Web arayüzünde ana sayfada kaç kayıt gösterilsin
FEED_LIMIT = 100

PORT = int(os.getenv("PORT", "8000"))
SELF_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
