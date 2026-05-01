import asyncio
import os

from telegram import Bot


def notify(text: str) -> None:
    """Send a Telegram notification to all users listed in ALLOWED_USERS."""
    token = os.environ["TELEGRAM_TOKEN"]
    recipients = [u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()]
    if not recipients:
        return
    asyncio.run(_send_all(token, recipients, text))


async def _send_all(token: str, chat_ids: list[str], text: str) -> None:
    async with Bot(token) as bot:
        for chat_id in chat_ids:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
