# file: tg_youtube_live_feed.py
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List

import aiohttp
from telegram import Message, Chat, constants
from telegram.ext import Application, ApplicationBuilder, CommandHandler

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # e.g. -1001234567890 or @your_channel
YT_API_KEY = os.getenv("YT_API_KEY")
WHITELIST = [c.strip() for c in os.getenv("WHITELIST", "").split(",") if c.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))  # как часто опрашивать
SHOW_UPCOMING = os.getenv("SHOW_UPCOMING", "1") == "1"  # показывать ли «скоро»

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VID_URL = "https://www.youtube.com/watch?v="

dashboard_message_id: int | None = None  # в памяти; можно вынести в файл/БД для сохранения между перезапусками
last_seen_live_ids: set[str] = set()  # чтобы по желанию публиковать отдельные посты о «новых» лайвах


async def yt_search(session: aiohttp.ClientSession, channel_id: str, event_type: str) -> List[Dict]:
    """
    event_type: 'live' | 'upcoming'
    возвращает список айтемов search API с type=video
    """
    logger.debug("yt_search: channel=%s event_type=%s", channel_id, event_type)
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "eventType": event_type,
        "type": "video",
        "order": "date",
        "maxResults": 10,
        "key": YT_API_KEY,
    }
    async with session.get(YOUTUBE_SEARCH_URL, params=params, timeout=20) as r:
        r.raise_for_status()
        data = await r.json()
        items = data.get("items", [])
    logger.info(
        "Получены результаты поиска: channel=%s event_type=%s count=%d",
        channel_id,
        event_type,
        len(items),
    )
    return items


async def collect_whitelist_state() -> dict:
    """Возвращает {'live':[...], 'upcoming':[...]} со списками видео словарей"""
    result = {"live": [], "upcoming": []}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        logger.info("Начат сбор состояния для %d каналов", len(WHITELIST))
        for ch in WHITELIST:
            logger.info("Сбор live для канала %s", ch)
            live_items = await yt_search(session, ch, "live")
            result["live"].extend(live_items)
            if SHOW_UPCOMING:
                logger.info("Сбор upcoming для канала %s", ch)
                upc_items = await yt_search(session, ch, "upcoming")
                result["upcoming"].extend(upc_items)

    # удалим дубликаты по videoId
    def uniq(items):
        seen = set()
        out = []
        for it in items:
            vid = it["id"]["videoId"]
            if vid not in seen:
                seen.add(vid)
                out.append(it)
        return out

    result["live"] = uniq(result["live"])
    result["upcoming"] = uniq(result["upcoming"])
    logger.info(
        "Итоговое состояние собрано: live=%d upcoming=%d",
        len(result["live"]),
        len(result["upcoming"]),
    )
    return result


def build_dashboard_text(state: dict) -> str:
    """Формирует текст поста канала."""
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("🎥 **Прямо сейчас в эфире**")
    if state["live"]:
        for it in state["live"]:
            vid = it["id"]["videoId"]
            sn = it["snippet"]
            title = sn["title"]
            ch_title = sn.get("channelTitle", "Channel")
            url = f"{YOUTUBE_VID_URL}{vid}"
            lines.append(f"• [{title}]({url}) — _{ch_title}_")
    else:
        lines.append("— (пусто)")

    if SHOW_UPCOMING:
        lines.append("\n⏳ **Скоро начнутся**")
        if state["upcoming"]:
            for it in state["upcoming"]:
                vid = it["id"]["videoId"]
                sn = it["snippet"]
                title = sn["title"]
                ch_title = sn.get("channelTitle", "Channel")
                url = f"{YOUTUBE_VID_URL}{vid}"
                lines.append(f"• [{title}]({url}) — _{ch_title}_")
        else:
            lines.append("— (ничего в ближайшее время)")

    lines.append(f"\n_обновлено: {now}_")
    return "\n".join(lines)


async def ensure_dashboard(app: Application) -> Message:
    """Создаёт или находит наш закреплённый дашборд-пост в канале."""
    global dashboard_message_id
    logger.info("Получение/создание дашборда в Telegram")
    chat = await app.bot.get_chat(chat_id=TELEGRAM_CHANNEL_ID)  # type: Chat

    # если у нас уже есть id в памяти — попробуем просто получить сообщение
    if dashboard_message_id:
        try:
            logger.info("Пробуем отредактировать существующий дашборд %s", dashboard_message_id)
            msg = await app.bot.edit_message_text(
                chat_id=TELEGRAM_CHANNEL_ID,
                message_id=dashboard_message_id,
                text="инициализация…",
                parse_mode=constants.ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return msg
        except Exception:
            logger.exception("Не удалось обновить существующий дашборд, создаём новый")
            dashboard_message_id = None  # не нашли/не можем редактировать

    # иначе создаём новый пост
    logger.info("Создаём новый дашборд-пост")
    msg = await app.bot.send_message(
        chat_id=TELEGRAM_CHANNEL_ID,
        text="инициализация…",
        parse_mode=constants.ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )
    dashboard_message_id = msg.message_id

    # закрепим
    try:
        logger.info("Пытаемся закрепить сообщение %s", dashboard_message_id)
        await app.bot.pin_chat_message(chat_id=TELEGRAM_CHANNEL_ID, message_id=dashboard_message_id,
                                       disable_notification=True)
    except Exception:
        logger.warning("Не удалось закрепить дашборд (нет прав?)", exc_info=True)

    return msg


async def publish_new_lives_if_any(app: Application, state: dict):
    """
    (опционально) публикует отдельные сообщения, если появились новые «live».
    Вкл/выкл логикой last_seen_live_ids.
    """
    global last_seen_live_ids
    current_ids = {it["id"]["videoId"] for it in state["live"]}
    new_ids = current_ids - last_seen_live_ids
    if new_ids:
        logger.info("Обнаружены новые live: %s", ", ".join(new_ids))
    else:
        logger.debug("Новых live не обнаружено")
    for it in state["live"]:
        vid = it["id"]["videoId"]
        if vid in new_ids:
            sn = it["snippet"]
            title = sn["title"]
            ch_title = sn.get("channelTitle", "Channel")
            url = f"{YOUTUBE_VID_URL}{vid}"
            text = f"🔴 **LIVE**: [{title}]({url})\n_{ch_title}_"
            await app.bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=text,
                parse_mode=constants.ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
    last_seen_live_ids = current_ids


async def update_cycle(app: Application):
    """Основной цикл обновления."""
    logger.info("Старт цикла обновления")
    msg = await ensure_dashboard(app)
    while True:
        try:
            logger.debug("Начинаем новый проход цикла обновления")
            state = await collect_whitelist_state()
            text = build_dashboard_text(state)
            logger.debug("Получился текст длиной %d символов", len(text))
            await app.bot.edit_message_text(
                chat_id=TELEGRAM_CHANNEL_ID,
                message_id=msg.message_id,
                text=text,
                parse_mode=constants.ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            await publish_new_lives_if_any(app, state)
            logger.info("Цикл обновления успешно завершён")
        except Exception as e:
            logger.exception("Сбой при обновлении дашборда")
            try:
                await app.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=f"⚠️ ошибка обновления: {e}")
            except Exception:
                logger.exception("Не удалось отправить сообщение об ошибке в канал")
        await asyncio.sleep(POLL_SECONDS)


async def start_cmd(update, context):
    await update.message.reply_text(
        "Ок! Дашборд будет поддерживаться автоматически. Закрепите этот пост, если он не закрепился сам.")


def attach_update_cycle(
    app: Application,
    create_task: Callable[[Awaitable[object]], asyncio.Task[object]] = asyncio.create_task,
) -> None:
    async def on_start(application: Application):
        logger.info("Application post-init: запускаем цикл обновления")
        create_task(update_cycle(application))

    app.post_init.append(on_start)


async def main():
    logger.info("Инициализация приложения")
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID and YT_API_KEY and WHITELIST):
        logger.error(
            "Не заданы необходимые переменные окружения. TELEGRAM_BOT_TOKEN=%s TELEGRAM_CHANNEL_ID=%s YT_API_KEY=%s WHITELIST=%s",
            bool(TELEGRAM_BOT_TOKEN),
            bool(TELEGRAM_CHANNEL_ID),
            bool(YT_API_KEY),
            WHITELIST,
        )
        raise SystemExit(
            "Не заданы переменные окружения: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, YT_API_KEY, WHITELIST")

    logger.info(
        "Запускаем приложение: whitelist=%s poll_seconds=%s show_upcoming=%s",
        ",".join(WHITELIST),
        POLL_SECONDS,
        SHOW_UPCOMING,
    )
    app: Application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))

    # запускаем фоновую задачу после старта бота
    attach_update_cycle(app)
    await app.initialize()
    await app.start()
    try:
        # await app.updater.start_polling(allowed_updates=constants.Update.ALL_TYPES)
        logger.info("Запускаем polling Telegram")
        await app.updater.start_polling()
        # бот используется лишь для отправки/редактирования; polling нужен, чтобы /start работал (необяз.)
        await asyncio.Event().wait()
    finally:
        logger.info("Останавливаем приложение")
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
