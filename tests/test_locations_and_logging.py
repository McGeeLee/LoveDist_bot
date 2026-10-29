from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telegram import Message, Update

import bot


@pytest.fixture
async def database(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'locations.db'}")
    monkeypatch.setattr(bot, "engine", engine)
    monkeypatch.setattr(bot, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    bot._rate_limit_store.clear()
    await bot.init_db()
    async with bot.get_session() as session:
        session.add(bot.User(telegram_id=99, username="visitor", share_code="visitor-code",
                             latitude=0, longitude=0, is_public=True))
    try:
        yield bot.LocationBot.__new__(bot.LocationBot)
    finally:
        bot._rate_limit_store.clear()
        await engine.dispose()


def location_update(kind, *, latitude=1.0, longitude=2.0, user=True, live=True):
    message = {
        "message_id": 100, "date": 1793232000,
        "chat": {"id": 99, "type": "private"},
        "location": {"latitude": latitude, "longitude": longitude},
    }
    if user:
        message["from"] = {"id": 99, "is_bot": False, "first_name": "Visitor"}
    if live:
        message["location"]["live_period"] = 900
    if kind == "edited_message":
        message["edit_date"] = 1793232010
    return Update.de_json({"update_id": 1, kind: message}, None)


@pytest.mark.parametrize("kind", ["message", "edited_message"])
@pytest.mark.parametrize("live", [False, True])
async def test_location_updates_persist_and_reply_to_effective_message(database, monkeypatch, kind, live):
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", reply)
    update = location_update(kind, live=live)
    if kind == "edited_message":
        assert update.message is None
    await database.handle_location(update, SimpleNamespace(user_data={}))

    async with bot.get_session() as session:
        own = await session.scalar(select(bot.User).where(bot.User.telegram_id == 99))
        assert (own.latitude, own.longitude) == (1.0, 2.0)
        assert not bot.is_location_expired(own.last_updated)
    reply.assert_awaited_once()
    assert "位置已更新" in reply.await_args.args[0]


@pytest.mark.parametrize("kind", ["message", "edited_message"])
async def test_live_location_rate_limit_replies_without_overwriting_data(database, monkeypatch, kind):
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", reply)
    context = SimpleNamespace(user_data={})
    await database.handle_location(location_update(kind), context)
    await database.handle_location(location_update("edited_message", latitude=3, longitude=4), context)

    assert reply.await_count == 2
    assert "速度太快" in reply.await_args.args[0]
    async with bot.get_session() as session:
        own = await session.scalar(select(bot.User).where(bot.User.telegram_id == 99))
        assert (own.latitude, own.longitude) == (1.0, 2.0)


async def test_live_edit_after_rate_limit_refreshes_existing_location(database, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", reply)
    context = SimpleNamespace(user_data={})
    await database.handle_location(location_update("message"), context)
    monkeypatch.setitem(bot._rate_limit_store, (99, "location_process"),
                        datetime.now(timezone.utc) - timedelta(seconds=6))
    reply.reset_mock()

    await database.handle_location(
        location_update("edited_message", latitude=3, longitude=4), context,
    )
    async with bot.get_session() as session:
        own = await session.scalar(select(bot.User).where(bot.User.telegram_id == 99))
        assert (own.latitude, own.longitude) == (3, 4)
        assert not bot.is_location_expired(own.last_updated)
    reply.assert_awaited_once()
    assert "位置已更新" in reply.await_args.args[0]


@pytest.mark.parametrize("target_state", ["public", "private", "expired", "missing"])
async def test_edited_location_rechecks_target_before_recording_distance(
    database, monkeypatch, target_state,
):
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", reply)
    if target_state != "missing":
        async with bot.get_session() as session:
            session.add(bot.User(
                telegram_id=10, username="target", share_code="target-code",
                latitude=0, longitude=1, is_public=target_state != "private",
                last_updated=datetime.now(timezone.utc) - timedelta(
                    days=2 if target_state == "expired" else 0),
            ))
    context = SimpleNamespace(user_data={"target_info": {"telegram_id": 10}})
    await database.handle_location(location_update("edited_message", latitude=0, longitude=2), context)

    async with bot.get_session() as session:
        own = await session.scalar(select(bot.User).where(bot.User.telegram_id == 99))
        assert (own.latitude, own.longitude) == (0, 2)
        visits = (await session.scalars(select(bot.Visit))).all()
        if target_state == "public":
            assert len(visits) == 1
            assert visits[0].visitor_id == 99
            assert visits[0].target_id == 10
            assert visits[0].distance_km == pytest.approx(111.195, abs=.001)
            assert "直线距离" in reply.await_args.args[0]
        else:
            assert visits == []
            assert ("已过期" if target_state == "expired" else "无法计算") in reply.await_args.args[0]
    assert "target_info" not in context.user_data


@pytest.mark.parametrize("kind", ["message", "edited_message"])
async def test_location_without_sender_is_ignored(monkeypatch, kind):
    get_session = AsyncMock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(bot, "get_session", get_session)
    update = location_update(kind, user=False)
    await bot.LocationBot.__new__(bot.LocationBot).handle_location(update, SimpleNamespace(user_data={}))
    get_session.assert_not_called()


async def test_update_without_message_is_ignored(monkeypatch):
    get_session = AsyncMock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(bot, "get_session", get_session)
    await bot.LocationBot.__new__(bot.LocationBot).handle_location(
        Update(update_id=1), SimpleNamespace(user_data={}),
    )
    get_session.assert_not_called()


def test_http_request_logs_do_not_expose_bot_token(caplog):
    # Synthetic token and in-memory transport: no Telegram or network request.
    token = "123456:offline-test-token"
    url = f"https://api.telegram.org/bot{token}/getMe"
    with caplog.at_level(logging.DEBUG):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            assert client.post(url).status_code == 200
        bot.logger.info("Application diagnostic remains visible")
        logging.getLogger("httpcore.connection").debug("HTTP trace %s", url)
        logging.getLogger("httpx").warning("Transport warning remains visible")
    assert token not in caplog.text
    assert "HTTP Request:" not in caplog.text
    assert "Application diagnostic remains visible" in caplog.text
    assert "Transport warning remains visible" in caplog.text
