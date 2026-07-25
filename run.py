#!/usr/bin/env python3
"""Giriş noktası — web sunucusu + tarayıcı aynı süreçte çalışır.

Neden aynı süreçte? Render'ın ücretsiz planında ayrı "background worker" yok;
sadece web servisi var. Tarayıcı, web uygulamasının yaşam döngüsüne bağlı
arka plan görevleri olarak dönüyor (bkz. app/web.py lifespan).

Yerelde:   python run.py
Render'da: uvicorn app.web:app --host 0.0.0.0 --port $PORT
"""
import logging
import os

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

if __name__ == "__main__":
    uvicorn.run(
        "app.web:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
    )
