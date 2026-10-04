# Дурак — Telegram Mini App

Telegram Mini App + FastAPI/WebSocket + aiogram. Максимум 3 игрока.

## Render
1. Создайте **Web Service** из GitHub-репозитория.
2. Runtime: **Docker**. Render возьмёт `Dockerfile` из корня.
3. Добавьте Environment Variables:
   - `BOT_TOKEN` = новый токен бота
   - `APP_SHORT_NAME` = имя Mini App из @BotFather (например `durak`)
   - `PUBLIC_URL` = URL сервиса Render (например `https://durak-telegram-game.onrender.com`)
4. Health Check Path: `/health` (в `render.yaml` уже задан).
5. Для Docker Build/Start Commands ничего задавать не нужно.

Render должен передать приложению `PORT`; приложение слушает `0.0.0.0:$PORT`. Render также поддерживает WebSocket для web services.

## Локально
```bash
pip install -r requirements.txt
BOT_TOKEN=... python main.py
```
