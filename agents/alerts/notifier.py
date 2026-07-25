"""
Alert and Notification Agent.
Sends alerts to Discord, Telegram, and Slack via webhooks.
"""
import os
import logging
import httpx
from typing import Dict, Any

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self):
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL")

    async def send_alert(self, message: str, level: str = "info"):
        """Send an alert to all configured channels."""
        if self.discord_webhook:
            await self._send_discord(message, level)
        if self.telegram_bot_token and self.telegram_chat_id:
            await self._send_telegram(message)
        if self.slack_webhook:
            await self._send_slack(message, level)

    async def _send_discord(self, message: str, level: str):
        color_map = {"info": 3447003, "warning": 15105570, "error": 15158332, "success": 3066993}
        payload = {
            "embeds": [{
                "title": f"ThetaForge Alert [{level.upper()}]",
                "description": message,
                "color": color_map.get(level, 3447003)
            }]
        }
        async with httpx.AsyncClient() as client:
            await client.post(self.discord_webhook, json=payload)

    async def _send_telegram(self, message: str):
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": self.telegram_chat_id, "text": message})

    async def _send_slack(self, message: str, level: str):
        payload = {"text": f"[{level.upper()}] {message}"}
        async with httpx.AsyncClient() as client:
            await client.post(self.slack_webhook, json=payload)
