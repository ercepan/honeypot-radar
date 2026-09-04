import asyncio, aiohttp
from app import security
T=[("TSLAB(hp)","0x6f9b3a5112d37b4f415f892bc67f91d61eb9bcb7"),
   ("CAKE(saglam)","0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82")]
async def main():
    async with aiohttp.ClientSession() as s:
        for ad,a in T:
            r=await security.analyze(s,a)
            print(f"\n{ad}: {r.verdict} (puan {r.score})")
            for x in r.reasons[:5]: print("   ·",x)
asyncio.run(main())
