# bale-notification-handler
Handling bale notifications to telegram with bot

## Docker setup

This project runs as two isolated containers:

- `bale-client`: connects to Bale and forwards message metadata to the internal relay.
- `telegram-relay`: receives internal Bale payloads and sends them to Telegram with the bot token.

The Telegram bot token is only configured on `telegram-relay`; it is not available to the Bale client container.

### Configure

Create the split env files:

```bash
cp .env.bale.example .env.bale
cp .env.telegram.example .env.telegram
```

Set the same `RELAY_SHARED_SECRET` value in both files.

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env.telegram`.

### Get the Bale session

`aiobale` creates the Bale session for you. You do not need to manually copy a token into an env file.

The easiest way is to run the Bale client once on your machine:

```bash
poetry run python main.py
```

If `session.bale` does not exist yet, the app will ask for your Bale phone number:

```text
Phone number:
```

Enter it in international format without the `+` sign. For example:

```text
989121234567
```

Then enter the login code you receive from Bale. After successful login, `aiobale` writes the session to:

```text
session.bale
```

Stop the app with `Ctrl+C` after the session file is created. Docker will reuse this file through the bind mount in `docker-compose.yml`.

You can also do the first login through Docker:

```bash
docker compose run --rm bale-client
```

Keep `session.bale` private. It is ignored by `.gitignore` and `.dockerignore` because it acts like your saved Bale login.

### Run

Make sure `session.bale` exists in the project root, then start both services:

```bash
docker compose up --build
```

The relay is exposed only on the Docker network, not on your host machine.

## Call notifications

Incoming Bale calls are not exposed as a documented `aiobale` event type today. The Bale client includes best-effort detection for:

- call service messages delivered through the normal message handler
- undocumented raw update extras that contain call wording

When detected, Telegram receives a message like:

```text
You are getting a voice call now from Sender Name.
```

If a call arrives but no Telegram call notification appears, check the Bale client logs for unhandled update details. The exact call payload may need to be mapped once Bale/aiobale exposes or logs it.
