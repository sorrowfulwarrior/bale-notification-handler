import json
import logging
import os
import threading
import time
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
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")
RELAY_SHARED_SECRET = os.getenv("RELAY_SHARED_SECRET")
BALE_REPLY_URL = os.getenv("BALE_REPLY_URL", "http://bale-client:8081/bale-reply")
TELEGRAM_POLL_INTERVAL = float(os.getenv("TELEGRAM_POLL_INTERVAL", "2"))

pending_replies: dict[int, str] = {}


def is_allowed_telegram_sender(chat: dict, sender: dict) -> bool:
    if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
        return False
    if TELEGRAM_ALLOWED_USER_ID:
        return str(sender.get("id")) == str(TELEGRAM_ALLOWED_USER_ID)
    return True


def telegram_api(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API call failed: {result}")
    return result


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
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")

    request_body = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": build_telegram_message(payload),
        "disable_web_page_preview": True,
    }

    reply_token = payload.get("reply_token")
    if payload.get("notification_type") == "bale_message" and reply_token:
        request_body["reply_markup"] = {
            "inline_keyboard": [
                [
                    {
                        "text": "Reply",
                        "callback_data": f"reply:{reply_token}",
                    }
                ]
            ]
        }

    telegram_api("sendMessage", request_body)


def send_reply_to_bale(reply_token: str, text: str) -> None:
    headers = {}
    if RELAY_SHARED_SECRET:
        headers["X-Relay-Token"] = RELAY_SHARED_SECRET

    response = requests.post(
        BALE_REPLY_URL,
        json={"reply_token": reply_token, "text": text},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    telegram_api("answerCallbackQuery", payload)


def handle_reply_button(callback_query: dict) -> None:
    callback_query_id = callback_query.get("id")
    callback_data = callback_query.get("data") or ""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    sender = callback_query.get("from") or {}

    if callback_query_id:
        answer_callback_query(callback_query_id)

    if not is_allowed_telegram_sender(chat, sender):
        logger.warning(
            "Ignored reply button from Telegram chat_id=%s user_id=%s.",
            chat.get("id"),
            sender.get("id"),
        )
        return

    if not callback_data.startswith("reply:"):
        return

    reply_token = callback_data.split(":", 1)[1]
    result = telegram_api(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "Write your Bale reply:",
            "reply_to_message_id": message.get("message_id"),
            "reply_markup": {
                "force_reply": True,
                "selective": True,
                "input_field_placeholder": "Type the reply to send to Bale",
            },
        },
    )

    prompt_message = result["result"]
    pending_replies[prompt_message["message_id"]] = reply_token


def handle_text_reply(message: dict) -> None:
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if not is_allowed_telegram_sender(chat, sender):
        logger.warning(
            "Ignored Telegram message from chat_id=%s user_id=%s.",
            chat.get("id"),
            sender.get("id"),
        )
        return

    reply_to_message = message.get("reply_to_message") or {}
    prompt_message_id = reply_to_message.get("message_id")
    reply_token = pending_replies.get(prompt_message_id)
    if reply_token is None:
        return

    text = (message.get("text") or "").strip()
    if not text:
        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "I can only send text replies to Bale right now.",
                "reply_to_message_id": message.get("message_id"),
            },
        )
        return

    try:
        send_reply_to_bale(reply_token, text)
    except Exception:
        logger.exception("Failed to relay Telegram reply to Bale.")
        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (
                    "Failed to send the Bale reply. The original message may "
                    "have expired or the Bale client may be offline."
                ),
                "reply_to_message_id": message.get("message_id"),
            },
        )
        return

    telegram_api(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "Reply sent to Bale.",
            "reply_to_message_id": message.get("message_id"),
        },
    )
    pending_replies.pop(prompt_message_id, None)


def handle_telegram_update(update: dict) -> None:
    if "callback_query" in update:
        handle_reply_button(update["callback_query"])
        return

    message = update.get("message")
    if message:
        handle_text_reply(message)


def poll_telegram_updates() -> None:
    offset = None
    webhook_deleted = False

    while True:
        try:
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
                time.sleep(TELEGRAM_POLL_INTERVAL)
                continue

            if not webhook_deleted:
                telegram_api("deleteWebhook", {"drop_pending_updates": False})
                webhook_deleted = True

            params = {
                "timeout": 25,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            }
            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError(f"Telegram getUpdates failed: {result}")

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                handle_telegram_update(update)
        except Exception:
            logger.exception("Telegram update polling failed.")
            time.sleep(TELEGRAM_POLL_INTERVAL)


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
    threading.Thread(target=poll_telegram_updates, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), RelayHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
