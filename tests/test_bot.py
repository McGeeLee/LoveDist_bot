from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pytest
from telegram.error import Forbidden, RetryAfter
import bot


def test_import_without_token_and_missing_token_validation(monkeypatch):
    monkeypatch.setattr(bot, 'BOT_TOKEN', None)
    with pytest.raises(ValueError, match='BOT_TOKEN'):
        bot.LocationBot()


async def test_application_lifecycle(monkeypatch):
    # Structurally valid dummy token; never initialize or poll Telegram.
    monkeypatch.setattr(bot, 'BOT_TOKEN', '123456:offline-test-token')
    from telegram.request import HTTPXRequest
    monkeypatch.setattr(HTTPXRequest, '_build_client', MagicMock(return_value=MagicMock(is_closed=False)))
    init = AsyncMock()
    dispose = AsyncMock()
    monkeypatch.setattr(bot, 'init_db', init)
    monkeypatch.setattr(bot, 'engine', SimpleNamespace(dispose=dispose))
    instance = bot.LocationBot()
    await instance.app.post_init(instance.app)
    await instance.app.post_shutdown(instance.app)
    init.assert_awaited_once()
    dispose.assert_awaited_once()


def test_distance_and_sqlite_utc():
    assert bot.LocationBot.calculate_distance(0, 0, 0, 0) == 0
    assert bot.LocationBot.calculate_distance(0, 0, 0, 1) == pytest.approx(111.195, abs=.001)
    assert not bot.is_location_expired(datetime.now(timezone.utc).replace(tzinfo=None))
    assert bot.is_location_expired(datetime.now(timezone.utc) - timedelta(days=2))
    assert bot.is_location_expired(None)


@pytest.mark.parametrize('delay', [1, timedelta(seconds=1)])
async def test_broadcast_retries_same_recipient_and_counts(monkeypatch, delay):
    monkeypatch.setattr(bot, 'ADMIN_IDS', [42])
    result = MagicMock()
    result.all.return_value = [(10,), (20,), (30,)]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    @asynccontextmanager
    async def get_session():
        yield session
    monkeypatch.setattr(bot, 'get_session', get_session)
    sleep = AsyncMock()
    monkeypatch.setattr(bot.asyncio, 'sleep', sleep)
    status = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=SimpleNamespace(reply_text=AsyncMock(return_value=status)))
    send = AsyncMock(side_effect=[RetryAfter(delay), None, Forbidden('blocked'), RetryAfter(1), RetryAfter(1)])
    context = SimpleNamespace(args=['hello'], bot=SimpleNamespace(send_message=send))
    await bot.LocationBot.__new__(bot.LocationBot).broadcast(update, context)
    assert [call.kwargs['chat_id'] for call in send.await_args_list] == [10, 10, 20, 30, 30]
    assert '成功: 1' in status.edit_text.call_args.args[0]
    assert '失败: 2' in status.edit_text.call_args.args[0]
    sleep.assert_any_await(1)


async def test_privacy_rechecked_before_distance(monkeypatch):
    bot._rate_limit_store.clear()
    own = SimpleNamespace(is_public=True)
    target = SimpleNamespace(is_public=False, latitude=0)
    results = []
    for value in [own, target]:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session = SimpleNamespace(execute=AsyncMock(side_effect=results))
    @asynccontextmanager
    async def get_session():
        yield session
    monkeypatch.setattr(bot, 'get_session', get_session)
    reply = AsyncMock()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=99), message=SimpleNamespace(location=SimpleNamespace(latitude=0, longitude=1), reply_text=reply))
    context = SimpleNamespace(user_data={'target_info': {'telegram_id': 10}})
    await bot.LocationBot.__new__(bot.LocationBot).handle_location(update, context)
    assert '无法计算' in reply.call_args.args[0]


async def test_database_initialization_offline(monkeypatch, tmp_path):
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "test.db"}')
    monkeypatch.setattr(bot, 'engine', engine)
    try:
        await bot.init_db()
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert set(tables) == {'users', 'visits'}
    finally:
        await engine.dispose()
