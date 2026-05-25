import asyncio
import json
import logging
import os
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import requests
from aiobale import Client, Dispatcher
from aiobale.types import Message
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

RELAY_URL = os.getenv("RELAY_URL", "http://telegram-relay:8080/bale-message")
RELAY_SHARED_SECRET = os.getenv("RELAY_SHARED_SECRET")
BALE_REPLY_HOST = os.getenv("BALE_REPLY_HOST", "0.0.0.0")
BALE_REPLY_PORT = int(os.getenv("BALE_REPLY_PORT", "8081"))
REPLY_CACHE_LIMIT = int(os.getenv("REPLY_CACHE_LIMIT", "1000"))

dp = Dispatcher()
client = Client(dp)
has_logged_first_update = False
app_loop: Optional[asyncio.AbstractEventLoop] = None
reply_targets: OrderedDict[str, Message] = OrderedDict()


def get_self_user_id() -> Optional[int]:
    try:
        return client.id
    except Exception:
        logger.warning("Could not determine authenticated Bale user id.", exc_info=True)
        return None


def is_self_sent_message(msg: Message) -> bool:
    self_user_id = get_self_user_id()
    return self_user_id is not None and msg.sender_id == self_user_id


async def get_sender_profile(msg: Message) -> dict[str, str]:
    try:
        user = await msg.load_user()
    except Exception:
        logger.warning(
            "Could not load sender profile for sender_id=%s",
            msg.sender_id,
            exc_info=True,
        )
        sender_id = str(msg.sender_id)
        return {"name": sender_id, "username": ""}

    name = (
        getattr(user, "local_name", None)
        or getattr(user, "name", None)
        or getattr(user, "username", None)
        or str(msg.sender_id)
    )
    username = getattr(user, "username", None) or ""

    return {"name": name, "username": username}


def forward_to_telegram(payload: dict[str, Any]) -> None:
    headers = {}
    if RELAY_SHARED_SECRET:
        headers["X-Relay-Token"] = RELAY_SHARED_SECRET

    response = requests.post(RELAY_URL, json=payload, headers=headers, timeout=10)
    response.raise_for_status()


async def notify_telegram(payload: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(forward_to_telegram, payload)
    except Exception:
        logger.exception("Failed to forward Bale event to Telegram relay.")


def remember_reply_target(msg: Message) -> str:
    token = secrets.token_urlsafe(18)
    reply_targets[token] = msg
    reply_targets.move_to_end(token)

    while len(reply_targets) > REPLY_CACHE_LIMIT:
        reply_targets.popitem(last=False)

    return token


async def send_bale_reply(reply_token: str, text: str) -> None:
    target = reply_targets.get(reply_token)
    if target is None:
        raise ValueError("Reply target was not found or expired.")

    await target.reply(text)


class BaleReplyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/bale-reply":
            self.send_error(404)
            return

        if RELAY_SHARED_SECRET:
            relay_token = self.headers.get("X-Relay-Token")
            if relay_token != RELAY_SHARED_SECRET:
                logger.warning("Rejected Bale reply request with invalid relay token.")
                self.send_error(401)
                return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body)
            reply_token = str(payload["reply_token"])
            text = str(payload["text"]).strip()
            if not text:
                raise ValueError("Reply text cannot be empty.")
            if app_loop is None:
                raise RuntimeError("Bale event loop is not ready.")

            future = asyncio.run_coroutine_threadsafe(
                send_bale_reply(reply_token, text),
                app_loop,
            )
            future.result(timeout=20)
        except Exception:
            logger.exception("Failed to send Bale reply.")
            self.send_error(500)
            return

        logger.info("Sent Bale reply.")
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        logger.debug(format, *args)


def start_bale_reply_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((BALE_REPLY_HOST, BALE_REPLY_PORT), BaleReplyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Started Bale reply endpoint on %s:%s.", BALE_REPLY_HOST, BALE_REPLY_PORT)
    return server


def detect_call_type(text: str) -> Optional[str]:
    normalized = text.casefold()
    call_terms = ("call", "تماس")
    voice_terms = ("voice", "audio", "صوتی")
    video_terms = ("video", "تصویری")

    if not any(term in normalized for term in call_terms):
        return None
    if any(term in normalized for term in video_terms):
        return "video"
    if any(term in normalized for term in voice_terms):
        return "voice"
    return "call"


def as_debug_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


async def maybe_forward_call_notification(
    sender: dict[str, str],
    sender_id: int,
    chat_id: int,
    source_text: str,
) -> bool:
    call_type = detect_call_type(source_text)
    if call_type is None:
        return False

    await notify_telegram(
        {
            "notification_type": "bale_call",
            "call_type": call_type,
            "sender_name": sender["name"],
            "sender_username": sender["username"],
            "sender_id": sender_id,
            "chat_id": chat_id,
        }
    )
    return True


def get_service_message_text(msg: Message) -> str:
    service_message = getattr(msg.content, "service_message", None)
    if not service_message:
        return ""
    return getattr(service_message, "text", "") or as_debug_json(service_message)


async def handle_raw_update_for_calls(update) -> None:
    body = getattr(update, "body", None)
    if body is None or body.current_event is not None:
        return

    extras = getattr(body, "model_extra", None) or {}
    if not extras:
        return

    raw_payload = as_debug_json(extras)
    call_type = detect_call_type(raw_payload)
    if call_type is None:
        logger.debug("Unhandled Bale update body extras: %s", raw_payload)
        return

    logger.info("Detected possible Bale %s call from raw update extras.", call_type)
    await notify_telegram(
        {
            "notification_type": "bale_call",
            "call_type": call_type,
            "sender_name": "Unknown",
            "sender_username": "",
            "sender_id": "unknown",
            "chat_id": "unknown",
        }
    )


original_handle_update = client.handle_update


async def handle_update_with_call_detection(update) -> None:
    await handle_raw_update_for_calls(update)
    await original_handle_update(update)


client.handle_update = handle_update_with_call_detection


@dp.message()
async def print_incoming_message(msg: Message):
    global has_logged_first_update

    if not has_logged_first_update:
        logger.info("Bale client connected successfully and received its first update.")
        has_logged_first_update = True

    if is_self_sent_message(msg):
        logger.info(
            "Skipping self-sent Bale message with message_id=%s in chat_id=%s.",
            msg.message_id,
            msg.chat.id,
        )
        return

    text = msg.text or "[non-text message]"
    sender = await get_sender_profile(msg)
    service_message_text = get_service_message_text(msg)

    logger.info(
        "Incoming message from %s (username=%s, sender_id=%s, chat_id=%s): %s",
        sender["name"],
        sender["username"] or "unavailable",
        msg.sender_id,
        msg.chat.id,
        text,
    )

    if service_message_text:
        sent_call_notification = await maybe_forward_call_notification(
            sender,
            msg.sender_id,
            msg.chat.id,
            service_message_text,
        )
        if sent_call_notification:
            logger.info(
                "Forwarded Bale call notification from sender_id=%s to Telegram relay.",
                msg.sender_id,
            )
            return

    payload = {
        "notification_type": "bale_message",
        "sender_name": sender["name"],
        "sender_username": sender["username"],
        "sender_id": msg.sender_id,
        "chat_id": msg.chat.id,
        "message_id": msg.message_id,
        "reply_token": remember_reply_target(msg),
        "text": text,
    }

    await notify_telegram(payload)


async def run() -> None:
    global app_loop

    app_loop = asyncio.get_running_loop()
    reply_server = start_bale_reply_server()
    try:
        await client.start()
    finally:
        reply_server.shutdown()
        reply_server.server_close()


if __name__ == "__main__":
    logger.info("Starting Bale client...")
    try:
        asyncio.run(run())
    except Exception:
        logger.exception("Bale client stopped because of an unexpected error.")
        raise
