#!/usr/bin/env python3

from __future__ import annotations

import getpass
import secrets
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
GENERATED_COMPOSE_FILE = "docker-compose.generated.yml"


def prompt_required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("This value is required.")


def prompt_optional(prompt: str) -> str:
    return input(prompt).strip()


def prompt_secret_required(prompt: str) -> str:
    while True:
        value = getpass.getpass(prompt).strip()
        if value:
            return value
        print("This value is required.")


def prompt_secret_optional(prompt: str) -> str:
    return getpass.getpass(prompt).strip()


def prompt_yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def env_value(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def write_file(path: Path, content: str) -> bool:
    if path.exists() and not prompt_yes_no(f"{path} already exists. Overwrite it?"):
        print(f"Skipped {path}")
        return False

    path.write_text(content + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return True


def compose_value(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def prompt_client_count() -> int:
    while True:
        raw_value = input("How many Bale clients? Example: 2: ").strip()
        if raw_value.isdigit() and int(raw_value) > 0:
            return int(raw_value)
        print("Enter a positive number.")


def build_compose(client_services: list[str]) -> str:
    lines = [
        "services:",
    ]

    for index, service_name in enumerate(client_services, start=1):
        lines.extend(
            [
                f"  {service_name}:",
                "    build:",
                "      context: .",
                "      dockerfile: Dockerfile.bale",
                "    env_file:",
                f"      - .env.bale.{index}",
                "    environment:",
                "      RELAY_URL: http://telegram-relay:8080/bale-message",
                "      BALE_REPLY_HOST: 0.0.0.0",
                "      BALE_REPLY_PORT: 8081",
                "    volumes:",
                f"      - ./sessions/{service_name}.session.bale:/app/session.bale",
                "    expose:",
                '      - "8081"',
                "    stdin_open: true",
                "    tty: true",
                "    depends_on:",
                "      - telegram-relay",
                "    restart: unless-stopped",
                "",
            ]
        )

    first_client_service = client_services[0]
    lines.extend(
        [
            "  telegram-relay:",
            "    build:",
            "      context: .",
            "      dockerfile: Dockerfile.telegram",
            "    env_file:",
            "      - .env.telegram",
            "    environment:",
            f"      BALE_REPLY_URL: {compose_value(f'http://{first_client_service}:8081/bale-reply')}",
            "    expose:",
            '      - "8080"',
            "    restart: unless-stopped",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    print("Bale Notification Handler env setup")
    print()
    print("This script creates:")
    print("  - .env.telegram for the shared Telegram relay")
    print("  - .env.bale.N for each Bale client")
    print("  - .env.bale too when you configure only one Bale client")
    print(f"  - {GENERATED_COMPOSE_FILE} for the configured Bale clients")
    print()
    print("Secret inputs are hidden while typing.")
    print()

    client_count = prompt_client_count()

    print()
    print("Telegram relay settings")
    print()

    telegram_bot_token = prompt_secret_required(
        "Telegram bot token. Example: 123456:ABC-DEF...: "
    )
    print()
    telegram_allowed_user_id = prompt_optional(
        "Allowed Telegram user id. Example: 123456789. Default: empty: "
    )

    print()
    print("Relay shared secret")
    shared_secret = prompt_secret_optional(
        "Relay shared secret. Example: a-long-random-string. Default: generate: "
    )
    if not shared_secret:
        shared_secret = secrets.token_hex(32)
        print("Generated relay shared secret.")

    routes: list[str] = []
    bale_env_files: list[Path] = []
    bale_env_contents: list[str] = []
    client_services: list[str] = []

    for index in range(1, client_count + 1):
        print()
        print(f"Bale client {index}")
        bale_account_number = prompt_required(
            "  Bale account number. Example: 989121234567: "
        )
        print()
        telegram_chat_id = prompt_required(
            "  Telegram chat id. Example: 123456789 or -1001234567890: "
        )
        print()
        service_name = prompt_optional(
            f"  Docker service name. Example: bale-client-work. Default: bale-client-{index}: "
        )
        if not service_name:
            service_name = f"bale-client-{index}"
        client_services.append(service_name)

        routes.append(f"{bale_account_number}={telegram_chat_id}")
        bale_env_path = ROOT_DIR / f".env.bale.{index}"
        bale_env_content = "\n".join(
            [
                f"RELAY_URL={env_value('http://telegram-relay:8080/bale-message')}",
                f"RELAY_SHARED_SECRET={env_value(shared_secret)}",
                f"BALE_ACCOUNT_NUMBER={env_value(bale_account_number)}",
                f"BALE_REPLY_CALLBACK_URL={env_value(f'http://{service_name}:8081/bale-reply')}",
            ]
        )

        bale_env_files.append(bale_env_path)
        bale_env_contents.append(bale_env_content)
        write_file(bale_env_path, bale_env_content)

    telegram_routes = ",".join(routes)
    telegram_env_lines = [
        f"TELEGRAM_BOT_TOKEN={env_value(telegram_bot_token)}",
        f"TELEGRAM_ACCOUNT_ROUTES={env_value(telegram_routes)}",
        "# Legacy single-chat fallback. Ignored when TELEGRAM_ACCOUNT_ROUTES is set.",
        "TELEGRAM_CHAT_ID=",
    ]
    if telegram_allowed_user_id:
        telegram_env_lines.append(
            f"TELEGRAM_ALLOWED_USER_ID={env_value(telegram_allowed_user_id)}"
        )
    telegram_env_lines.append(f"RELAY_SHARED_SECRET={env_value(shared_secret)}")

    print()
    write_file(ROOT_DIR / ".env.telegram", "\n".join(telegram_env_lines))

    if client_count == 1:
        write_file(ROOT_DIR / ".env.bale", bale_env_contents[0])

    compose_path = ROOT_DIR / GENERATED_COMPOSE_FILE
    write_file(compose_path, build_compose(client_services))

    print()
    print("Done.")
    print()
    print("Generated files:")
    print(f"  {ROOT_DIR / '.env.telegram'}")
    for bale_env_file in bale_env_files:
        print(f"  {bale_env_file}")
    if client_count == 1:
        print(f"  {ROOT_DIR / '.env.bale'}")
    print(f"  {compose_path}")
    print()
    print("Generated Telegram route map:")
    print(f"  TELEGRAM_ACCOUNT_ROUTES={telegram_routes}")
    print()
    print("Next steps:")
    print("  1. Keep these env files private; they contain secrets.")
    print(f"  2. Run with: docker compose -f {GENERATED_COMPOSE_FILE} up --build")
    print("  3. Create each Bale session file under ./sessions/ before long-running use.")


if __name__ == "__main__":
    main()
