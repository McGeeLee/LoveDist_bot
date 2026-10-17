# Migration verification

Upstream baseline: `763c62ee90e13aa7861b1e24060ac0a05213fbd9`.

## Changes

- Python 3.12+ baseline and `.python-version`, uv 0.12.23 lock, hashed production requirements export.
- python-telegram-bot 22.8, SQLAlchemy 2.1.5, aiosqlite 0.22.1, python-dotenv 1.2.4 (stable PyPI versions resolved during this migration).
- Fix invalid `FloodWait` import to `RetryAfter`; accept the required `Application` lifecycle argument; dispose DB engine on shutdown.
- Retry a rate-limited broadcast recipient once, accounting for second rate-limit and Telegram failure; support both seconds and timedelta retry delays.
- Recheck the existing privacy flag when calculating a distance, not just when opening a share link. Treat SQLite naive location timestamps as UTC; missing timestamps expire safely.
- Add missing environment example and ignore local credentials/database files.

## Verified locally

- `uv sync --locked` on Python 3.12.14.
- Seven offline tests passed on Python 3.12.14, Python 3.13.5 and Python 3.14.8.
- Seven tests also passed with `PTB_TIMEDELTA=1`; default mode emits upstream deprecation warnings for integer retry delays.
- `uv run ruff check .` passes (fatal-error rules, not a complete style/type audit).
- `git diff --check` passes.
- Tests use a fake token and mocked Telegram client construction; SQLite test database is temporary.

## Not run / rollout

- CI matrix includes 3.12, 3.13, 3.14; remote CI was not run.
- No type checker configured or run.
- No real tokens used, no live Telegram polling/broadcasts, no production database opened.
- No push, PR, or deployment. Back up the existing DB and validate bot startup in an explicitly authorized staging rollout before production.
