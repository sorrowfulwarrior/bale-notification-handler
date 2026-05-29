# Bale Notification Handler

Forward incoming Bale messages and call notifications to Telegram chats.

The project is designed as a small bridge. One or more Bale client containers
log in to Bale accounts and watch them for incoming events. A Telegram relay
container owns the Telegram bot token and sends the final notification.

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
Telegram chat selected by Bale account number

For Reply 

    |
    | Reply button + typed response
    v
telegram-relay container
    |
    | HTTP POST /bale-reply
    | X-Relay-Token: RELAY_SHARED_SECRET
    v
bale-client container
    |
    | aiobale reply
    v
Original Bale message
```

### Services

- `bale-client`
  - Runs `main.py`.
  - Connects to Bale with `aiobale`.
  - Reads the local `session.bale` login file.
  - Optionally tags each forwarded payload with `BALE_ACCOUNT_NUMBER`.
  - Can tag reply-enabled payloads with `BALE_REPLY_CALLBACK_URL`.
  - Skips messages sent by your own Bale account.
  - Sends only incoming Bale message/call payloads to the internal relay.
  - Receives internal reply requests on `/bale-reply` and replies to the
    original Bale message.

- `telegram-relay`
  - Runs `telegram_relay.py`.
  - Receives Bale payloads on `/bale-message`.
  - Validates `X-Relay-Token` against `RELAY_SHARED_SECRET`.
  - Sends the formatted message to Telegram using `TELEGRAM_BOT_TOKEN`.
  - Can route different Bale account numbers to different Telegram chat ids.
  - Polls Telegram for reply button clicks and reply text.

The Telegram bot token is configured only in `telegram-relay`; the Bale client
does not need it. The Bale session file is configured only in `bale-client`; the
Telegram relay does not need it.

## Prerequisites

- Docker and Docker Compose.
- A Bale account that can receive the login code.
- A Telegram bot token from BotFather.
- A Telegram chat id where the bot is allowed to send messages.
- Python 3.10+ and Poetry for creating bale session credentials.

## Configuration

For the default single-client setup, create local env files from the examples:

```bash
cp .env.bale.example .env.bale
cp .env.telegram.example .env.telegram
```

For routed single-client or multi-client setup, generate the env and Compose
files interactively:

```bash
python3 setup_multi_bale_env.py
```

The setup script asks how many Bale clients you want, then asks for each Bale
account number, Telegram chat id, and Docker service name. It writes
`.env.telegram`, one `.env.bale.N` file per Bale client, and
`docker-compose.generated.yml`. For a single Bale client, it also writes
`.env.bale` for the default Docker Compose service. Secret prompts such as the
Telegram bot token and relay shared secret are hidden while typing, and values
are quoted safely in the generated env files.

Example answers:

```text
How many Bale clients do you want to configure? 2
Telegram bot token (hidden while typing): 
Optional Telegram allowed user id for replies (leave empty for none): 123456789
Relay shared secret (hidden, leave empty to generate one): 

Bale client 1
  Bale account number, international format without +: 989121111111
  Telegram chat id that should receive this account's notifications: 123456789
  Docker service name for this Bale client (default: bale-client-1): bale-client-work

Bale client 2
  Bale account number, international format without +: 989122222222
  Telegram chat id that should receive this account's notifications: -1001234567890
  Docker service name for this Bale client (default: bale-client-2): bale-client-family
```

The token and secret prompts intentionally do not show what you type. This keeps
secrets out of terminal scrollback, screen recordings, and accidental shoulder
surfing. Press Enter after typing the value normally; if you leave the relay
secret empty, the script generates one.

The generated `.env.telegram` route map will look like:

```text
TELEGRAM_ACCOUNT_ROUTES='989121111111=123456789,989122222222=-1001234567890'
```

The generated Compose file will include one Bale client service per configured
account and a shared `telegram-relay` service. Session files are mounted from
`./sessions/<service-name>.session.bale` into each Bale client container.

Set the same strong random value in both files:

```text
RELAY_SHARED_SECRET=replace-with-a-long-random-secret
```

Set these values in `.env.telegram`:

```text
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

For per-account routing, set `BALE_ACCOUNT_NUMBER` in each Bale client's env
and map those numbers to Telegram chat ids in `.env.telegram`:

```text
# .env.bale
BALE_ACCOUNT_NUMBER=989121234567
BALE_REPLY_CALLBACK_URL=http://bale-client:8081/bale-reply

# .env.telegram
TELEGRAM_ACCOUNT_ROUTES=989121234567=123456789,989129876543=-1001234567890
```

When `TELEGRAM_ACCOUNT_ROUTES` is set, `TELEGRAM_CHAT_ID` is ignored for
incoming Bale notifications. If an incoming routed payload has no matching Bale
account number, the relay logs an error and does not send the notification.

If `TELEGRAM_CHAT_ID` is a group chat, also set:

```text
TELEGRAM_ALLOWED_USER_ID=your-telegram-user-id
```

`TELEGRAM_ALLOWED_USER_ID` is optional. It limits who can use Telegram reply
buttons to send messages back to Bale. This is useful when notifications go to
a Telegram group, because everyone in the group may see the bot message, but
you may want only one Telegram account to be allowed to click `Reply` and send
the response. Leave it empty for a private one-to-one bot chat, or when everyone
in the configured Telegram chat is allowed to reply.

Example:

```text
TELEGRAM_ACCOUNT_ROUTES='989121111111=-1001234567890'
TELEGRAM_ALLOWED_USER_ID='123456789'
```

In this example, notifications for Bale account `989121111111` go to Telegram
group `-1001234567890`, but only Telegram user `123456789` can send replies
back to Bale.

Do not commit `.env.bale`, `.env.bale.N`, `.env.telegram`, `session.bale`, or
the `sessions/` directory.

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

For multiple Bale accounts, create one session file per account and run one
`bale-client` service per session. When you use `setup_multi_bale_env.py`, the
generated Compose file mounts each session from:

```text
./sessions/<service-name>.session.bale
```

Create the `sessions/` directory before generating or moving those session
files:

```bash
mkdir -p sessions
```

## Run

Start the default two-service setup:

```bash
docker compose up --build
```

Or start the generated routed setup:

```bash
docker compose -f docker-compose.generated.yml up --build
```

The relay endpoints are exposed only inside the Docker network with Docker
Compose `expose`. There are no host ports published by default.

## Message Behavior

- Incoming Bale messages are forwarded to Telegram.
- If `TELEGRAM_ACCOUNT_ROUTES` is configured, each Bale account number is
  forwarded only to its mapped Telegram chat id.
- Each incoming Bale message includes a `Reply` button in Telegram.
- Tapping `Reply` opens a Telegram reply prompt.
- The text you send to that prompt is sent back to Bale as a reply to the
  original Bale message.
- In routed mode, reply prompts remember both the Telegram chat id and the Bale
  reply URL that received the original message.
- Messages sent by your own Bale account are ignored.
- Non-text messages are forwarded as `[non-text message]`.
- Incoming call notifications are best-effort because Bale call events are not
  exposed as a stable documented `aiobale` event type.
- Reply targets are stored in memory by `bale-client`, so replies only work
  while the Bale client process that received the original message is still
  running.

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
- The Bale message and reply endpoints are not published to the host by default.
- The Bale session file and env files are ignored by git.
- The Bale client ignores self-sent messages before forwarding to Telegram.
- Telegram replies are accepted only from the configured `TELEGRAM_CHAT_ID`.
- In routed mode, Telegram replies are accepted only from the chat id that
  received the routed notification.
- If `TELEGRAM_ALLOWED_USER_ID` is set, only that Telegram user can trigger
  replies.

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
- Reply text is sent through the internal Docker network from `telegram-relay`
  to `bale-client`.
- The relay has no rate limiting. Keep it internal unless you add stronger
  authentication and abuse protection.

For personal use on a trusted Docker host, the defaults are usually acceptable.
For shared infrastructure or production use, pin dependencies, run containers as
a non-root user, protect logs, rotate secrets regularly, and keep the relay off
the public internet.
