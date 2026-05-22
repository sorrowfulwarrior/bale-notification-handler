# Bale Notification Handler

Forward incoming Bale messages and call notifications to a Telegram chat.

The project is designed as a small two-service bridge. One container logs in to
Bale and watches your account for incoming events. A second container owns the
Telegram bot token and sends the final Telegram notification.

## Architecture

```text
Bale account
    |
    | aiobale session
    v
bale-client container
    |
    | HTTP POST /bale-message
    | X-Relay-Token: RELAY_SHARED_SECRET
    v
telegram-relay container
    |
    | Telegram Bot API
    v
Telegram chat
```

### Services

- `bale-client`
  - Runs `main.py`.
  - Connects to Bale with `aiobale`.
  - Reads the local `session.bale` login file.
  - Skips messages sent by your own Bale account.
  - Sends only incoming Bale message/call payloads to the internal relay.

- `telegram-relay`
  - Runs `telegram_relay.py`.
  - Receives Bale payloads on `/bale-message`.
  - Validates `X-Relay-Token` against `RELAY_SHARED_SECRET`.
  - Sends the formatted message to Telegram using `TELEGRAM_BOT_TOKEN`.

The Telegram bot token is configured only in `telegram-relay`; the Bale client
does not need it.

## Prerequisites

- Docker and Docker Compose.
- A Bale account that can receive the login code.
- A Telegram bot token from BotFather.
- A Telegram chat id where the bot is allowed to send messages.
- Python 3.10+ and Poetry for creating bale session credentials.

## Configuration

Create local env files from the examples:

```bash
cp .env.bale.example .env.bale
cp .env.telegram.example .env.telegram
```

Set the same strong random value in both files:

```text
RELAY_SHARED_SECRET=replace-with-a-long-random-secret
```

Set these values in `.env.telegram`:

```text
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

Do not commit `.env.bale`, `.env.telegram`, or `session.bale`.

## Create the Bale Session

`aiobale` creates and stores the Bale session for you. If `session.bale` does
not exist yet, run the Bale client once locally and complete the login prompt.

Using Poetry on your machine:

```bash
poetry install
poetry run python main.py
```

Or using Docker:

```bash
docker compose run --rm bale-client
```

When prompted, enter your Bale phone number in international format without the
`+` sign:

```text
989121234567
```

Then enter the login code you receive from Bale. After login succeeds,
`aiobale` writes:

```text
session.bale
```

Stop the client with `Ctrl+C` after the session file is created. Docker reuses
that file through the bind mount in `docker-compose.yml`.

## Run

Start both services:

```bash
docker compose up --build
```

The relay is exposed only inside the Docker network with `expose: "8080"`.
There is no host port published by default.

## Message Behavior

- Incoming Bale messages are forwarded to Telegram.
- Messages sent by your own Bale account are ignored.
- Non-text messages are forwarded as `[non-text message]`.
- Incoming call notifications are best-effort because Bale call events are not
  exposed as a stable documented `aiobale` event type.

When a call is detected, Telegram receives a message like:

```text
You are getting a voice call now from Sender Name.
```

If a call arrives but no Telegram notification appears, check the Bale client
logs for unhandled update details. The exact call payload may need to be mapped
again if Bale or `aiobale` changes its internal event shape.

## Security

This app has a reasonable security shape for a small self-hosted bridge, but it
is not a hardened production service.

What is protected:

- The Telegram bot token is isolated to `telegram-relay`.
- The internal relay requires `RELAY_SHARED_SECRET`.
- The relay is not published to the host by default.
- The Bale session file and env files are ignored by git.
- The Bale client ignores self-sent messages before forwarding to Telegram.

Known risks and things to keep in mind:

- `session.bale` is sensitive. Anyone with that file may be able to reuse your
  Bale login.
- `TELEGRAM_BOT_TOKEN` is sensitive. If it leaks, rotate it in BotFather.
- `RELAY_SHARED_SECRET` must be long and random. The default example value is
  not secure. Create one with `openssl rand -hex 32`
- The containers run as root because the current Dockerfiles use the default
  `python:3.10-slim` user.
- Python packages are installed without pinned versions in the Dockerfiles, so
  rebuilds may pull newer dependency versions.
- Incoming message text is logged by `bale-client`, so container logs may
  contain private message content.
- The relay has no rate limiting. Keep it internal unless you add stronger
  authentication and abuse protection.

For personal use on a trusted Docker host, the defaults are usually acceptable.
For shared infrastructure or production use, pin dependencies, run containers as
a non-root user, protect logs, rotate secrets regularly, and keep the relay off
the public internet.
