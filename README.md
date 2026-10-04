# Дурак — Telegram Mini App

## Render
Create a Render **Web Service** from this repository.
- Runtime: **Docker**
- Root Directory: **empty** (when files are in repository root)
- Dockerfile Path: `Dockerfile`
- Health Check Path: `/health`

Environment variables:
- `BOT_TOKEN` = Telegram bot token
- `APP_SHORT_NAME` = `durak`
- `BOT_USERNAME` = bot username without `@`

The container listens on Render's `PORT` and serves the Mini App plus the WebSocket game server.
