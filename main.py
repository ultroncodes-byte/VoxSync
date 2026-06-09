import os
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from bot import application
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RENDER_URL = os.getenv("RENDER_URL", "")
PING_INTERVAL = 840  # ping every 14 minutes (Render sleeps after 15)


async def keep_alive():
    """Ping self every 14 minutes to prevent Render from sleeping."""
    await asyncio.sleep(30)  # wait for server to fully start
    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{RENDER_URL}/ping", timeout=10)
                logger.info(f"Keep-alive ping: {response.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        await asyncio.sleep(PING_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await application.initialize()
    await application.start()

    # Set webhook
    webhook_url = f"{RENDER_URL}/webhook"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

    # Start keep-alive loop
    if RENDER_URL:
        asyncio.create_task(keep_alive())
        logger.info("Keep-alive pinger started")

    yield

    # Shutdown
    await application.stop()
    await application.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping():
    """Keep-alive endpoint."""
    return {"status": "alive"}


@app.get("/")
async def root():
    return {"status": "VoiceNote Summarizer is running"}


@app.post("/webhook")
async def webhook(request: Request):
    """Receive Telegram updates."""
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}
