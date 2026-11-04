"""Telegram alerts. Silent no-op unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are in .env."""
import requests

from brokers import env


def send(text):
    e = env()
    token, chat = e.get("TELEGRAM_BOT_TOKEN"), e.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=10)
        return r.ok
    except requests.RequestException:
        return False
