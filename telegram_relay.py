import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

HOST = os.getenv("TELEGRAM_RELAY_HOST", "0.0.0.0")
PORT = int(os.getenv("TELEGRAM_RELAY_PORT", "8080"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RELAY_SHARED_SECRET = os.getenv("RELAY_SHARED_SECRET")


def build_telegram_message(payload: dict) -> str:
    if payload.get("notification_type") == "bale_call":
        call_type = payload.get("call_type") or "voice/video"
        sender_name = payload.get("sender_name") or "Unknown"
        sender_username = payload.get("sender_username")

        if sender_username and not str(sender_username).startswith("@"):
            sender_username = f"@{sender_username}"

        sender_line = sender_name
        if sender_username:
            sender_line = f"{sender_name} ({sender_username})"

        return f"You are getting a {call_type} call now from {sender_line}."

    sender_name = payload.get("sender_name") or "Unknown"
    sender_username = payload.get("sender_username")
    sender_id = payload.get("sender_id") or "unknown"
    text = payload.get("text") or "[non-text message]"

    if sender_username and not str(sender_username).startswith("@"):
        sender_username = f"@{sender_username}"

    sender_line = f"{sender_name} ({sender_username})"
    if not sender_username:
        sender_line = f"{sender_name} (username unavailable, sender_id={sender_id})"

    return f"Bale message from {sender_line}:\n{text}"


def send_to_telegram(payload: dict) -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": build_telegram_message(payload),
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    response.raise_for_status()


class RelayHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/bale-message":
            self.send_error(404)
            return

        if RELAY_SHARED_SECRET:
            relay_token = self.headers.get("X-Relay-Token")
            if relay_token != RELAY_SHARED_SECRET:
                logger.warning("Rejected request with invalid relay token.")
                self.send_error(401)
                return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body)
            send_to_telegram(payload)
        except Exception:
            logger.exception("Failed to process Bale message payload.")
            self.send_error(500)
            return

        logger.info(
            "Forwarded Bale message from sender_id=%s to Telegram chat_id=%s.",
            payload.get("sender_id", "unknown"),
            TELEGRAM_CHAT_ID,
        )
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        logger.debug(format, *args)


def main() -> None:
    logger.info("Starting Telegram relay on %s:%s.", HOST, PORT)
    server = ThreadingHTTPServer((HOST, PORT), RelayHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
