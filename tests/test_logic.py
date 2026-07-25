"""Ağ gerektirmeyen mantık testleri.

Çalıştır:  .venv/bin/python -m tests.test_logic
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chain, config, security  # noqa: E402

WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
YENI = "0x1111111111111111111111111111111111111111"


def _topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def test_parse_v2():
    """PairCreated logu doğru çözülmeli; taban varlık değil, YENİ token seçilmeli."""
    entry = {
        "topics": [config.TOPIC_PAIR_CREATED, _topic(WBNB), _topic(YENI)],
        "data": "0x" + "0" * 24 + "2222222222222222222222222222222222222222" + "0" * 64,
        "blockNumber": "0x6a12345",
    }
    p = chain.parse_log(entry)
    assert p is not None
    assert p.token == YENI, p.token          # incelenecek olan yeni token
    assert p.base == WBNB
    assert p.pair == "0x2222222222222222222222222222222222222222"
    assert p.version == "v2"
    assert p.block == int("0x6a12345", 16)


def test_parse_iki_taban_atlanir():
    """İki taban varlık eşleşirse (ör. WBNB/USDT) incelenecek yeni token yoktur."""
    usdt = "0x55d398326f99059ff775485246999027b3197955"
    entry = {
        "topics": [config.TOPIC_PAIR_CREATED, _topic(WBNB), _topic(usdt)],
        "data": "0x" + "0" * 128,
        "blockNumber": "0x1",
    }
    assert chain.parse_log(entry) is None


def test_parse_alakasiz_log():
    entry = {"topics": ["0x" + "ab" * 32, _topic(WBNB), _topic(YENI)], "data": "0x"}
    assert chain.parse_log(entry) is None


def test_karar_honeypot():
    """Simülasyon 'satılamıyor' dediyse karar tartışmasız honeypot olmalı."""
    r = security.TokenReport(address=YENI, has_data=True)
    r.score = security.HARD
    assert security._decide(r) == security.HONEYPOT


def test_karar_olumcul_vergi():
    """%50+ satış vergisi pratikte satamamakla aynı."""
    r = security.TokenReport(address=YENI, has_data=True, sell_tax=95.0, buy_tax=0.0)
    assert security._decide(r) == security.HONEYPOT


def test_karar_tek_zayif_sinyal_damgalamaz():
    """CAKE gerçek hayatta 'mint açık' — tek başına riskli sayılmamalı.

    Bu testin sebebi: eski sürüm CAKE'i 'riskli' gösteriyordu (yanlış alarm).
    """
    r = security.TokenReport(
        address=YENI, has_data=True, buy_tax=0.0, sell_tax=0.0,
        holder_count=1_900_000, score=1,
    )
    assert security._decide(r) == security.CLEAN


def test_karar_iki_sinyal_riskli():
    r = security.TokenReport(
        address=YENI, has_data=True, buy_tax=0.0, sell_tax=0.0,
        holder_count=1_900_000, score=2,
    )
    assert security._decide(r) == security.RISKY


def test_veri_yoksa_temiz_denmez():
    """En kritik kural: 'veri gelmedi' ile 'güvenli' aynı şey DEĞİL.

    GoPlus emin olmadığı alanları hiç göndermiyor; bunu temiz saymak
    gerçek bir honeypot'u güvenli göstermeye yol açıyordu.
    """
    r = security.TokenReport(address=YENI, has_data=False)
    assert security._decide(r) == security.UNKNOWN


def test_taze_token_temiz_degil_yeni():
    """Testleri geçse bile az sahipli taze token 'temiz' değil 'yeni' olmalı."""
    r = security.TokenReport(
        address=YENI, has_data=True, buy_tax=0.0, sell_tax=0.0, holder_count=3
    )
    assert security._decide(r) == security.YOUNG


def test_hiz_sinirlayici():
    """Pencere dolunca beklemeli — API'lerin limitine takılmamızı önler."""
    async def go():
        rl = security.RateLimiter(calls=3, per_seconds=0.4)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for _ in range(4):
            await rl.wait()
        return loop.time() - t0

    assert asyncio.run(go()) >= 0.4


def test_alim_satim_ozeti():
    from app import market

    rows = [{"is_buy": True}] * 6
    assert market.buy_sell_summary(rows)["no_sells"] is True
    rows.append({"is_buy": False})
    assert market.buy_sell_summary(rows)["no_sells"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test gecti")
