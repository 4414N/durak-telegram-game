# Дурак — Telegram Mini App v2

Полноценная 2–3 player Podkidnoy Durak для Telegram.

## Render

Создай Web Service из GitHub-репозитория. Runtime: Docker. Build/Start Command не заполняй.

Environment Variables:

- `BOT_TOKEN` — новый токен бота.
- `APP_SHORT_NAME` — короткое имя Mini App, например `durak`.
- `BOT_USERNAME` — username бота без `@` (необязательно, бот сам определит его при запуске).

После деплоя проверь `/health`.

## BotFather

Настрой Main Mini App с тем же short name и URL сервиса. Также можно оставить бот-меню: приложение автоматически пытается установить кнопку `🃏 Дурак`.

## Группы

Для мультиплеера используй direct link Mini App из сообщения в группе:

`https://t.me/<bot_username>/<app_short_name>`

Telegram передаёт `chat_instance` для Mini App, запущенного прямой ссылкой в текущем чате, что позволяет разделять игровые столы по контексту чата. При создании комнаты также используется `startapp=room_<id>`, чтобы приглашённые попадали за один стол.
