# Dependency review — 2026-10-29

The supported Python range remains 3.12 and newer. Development defaults to
Python 3.15, and CI validates 3.12, 3.13, 3.14 and 3.15. Stable versions were
checked against the maintainers' release pages and PyPI; prereleases are excluded.
The complete repository tree at the reviewed main commit contained no
`AGENTS.md` or `.agents/skills` instructions.

## Versions and compatibility

| Component | Previous lock/configuration | Selected stable version |
| --- | --- | --- |
| python-telegram-bot | 22.8 | 22.8 |
| SQLAlchemy | 2.1.5 | 2.1.6 |
| aiosqlite | 0.22.1 | 0.22.1 |
| python-dotenv | 1.2.4 | 1.2.4 |
| pytest | 9.1.1 | 9.2.0 |
| pytest-asyncio | 1.4.0 | 1.4.1 |
| Ruff | 0.17.1 | 0.17.3 |
| certifi | 2026.7.22 | 2026.10.23 |
| uv | 0.12.23 | 0.13.6 |
| actions/checkout | v4 | v7.0.1, pinned commit |
| astral-sh/setup-uv | v6 | v10.3.3, pinned commit |

- [python-telegram-bot 22.8](https://pypi.org/project/python-telegram-bot/22.8/)
  is still the latest stable release. Its HTTPX requirement is
  `>=0.27,<0.29`; retain compatible HTTPX 0.28.1 rather than overriding the
  maintainer's range. Bot API behavior is checked against the
  [22.8 release notes](https://github.com/python-telegram-bot/python-telegram-bot/releases/tag/v22.8).
- [SQLAlchemy 2.1.6](https://docs.sqlalchemy.org/en/21/changelog/changelog_21.html#change-2.1.6)
  fixes cancellation during asynchronous connection close, among other fixes.
  The 2.1.7 section has no release date and is not selected. Keep
  `sqlalchemy[asyncio]` as required by the
  [asyncio installation guidance](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html).
- [aiosqlite 0.22.1](https://pypi.org/project/aiosqlite/0.22.1/) and
  [python-dotenv 1.2.4](https://pypi.org/project/python-dotenv/1.2.4/) remain current.
- [pytest 9.2](https://docs.pytest.org/en/stable/changelog.html#pytest-9-2-0-2026-10-27)
  makes optional `parametrize` arguments keyword-only. Existing tests pass only
  the two required positional arguments; no test rewrite is needed for that change.
  [pytest-asyncio 1.4.1](https://pytest-asyncio.readthedocs.io/en/stable/reference/changelog.html)
  explicitly adds Python 3.15 support. The suite uses `asyncio_mode = "auto"`
  and explicitly pins `asyncio_default_fixture_loop_scope = "function"` to
  preserve per-test fixture isolation across upcoming default changes. It does
  not use the deprecated `event_loop_policy` fixture.
- [Ruff 0.17.3](https://github.com/astral-sh/ruff/releases/tag/0.17.3)
  includes bug fixes. The existing fatal-error rule selection is preserved.
- [Python 3.15.2](https://www.python.org/downloads/release/python-3152/)
  is a stable security release. CI's minor-version selectors install the current
  available patch release for each supported interpreter.
- [uv 0.13.6](https://github.com/astral-sh/uv/releases/tag/0.13.6),
  [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), and
  [setup-uv v10.3.3](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.3)
  are current stable tooling. Action tags were resolved through GitHub's Git refs
  API and pinned to their full commit SHA. Workflow permissions remain
  `contents: read`, and checkout does not persist credentials.

## Security review

- The old certifi 2026.7.22 lock predates the emSign trust-root removal in
  [GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2).
  Its patched version is 2026.10.18. The resolver constraint requires at least
  [2026.10.23](https://github.com/certifi/python-certifi/releases/tag/2026.10.23),
  the current stable release, and the generated requirements retain hashes.
- [python-dotenv GHSA-mf9w-mj56-hr94](https://github.com/theskumar/python-dotenv/security/advisories/GHSA-mf9w-mj56-hr94)
  describes symlink following during `set_key` / `unset_key` file rewrites.
  The advisory lists 1.2.2 as patched; the retained 1.2.4 is newer.
- [h11 GHSA-vqfr-h8mv-ghfj](https://github.com/python-hyper/h11/security/advisories/GHSA-vqfr-h8mv-ghfj)
  affects 0.15.0 and earlier. The existing compatible 0.16.0 contains the fix;
  the resolver must not downgrade it.
- The official advisory listings for
  [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot/security/advisories),
  [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy/security/advisories),
  [aiosqlite](https://github.com/omnilib/aiosqlite/security/advisories), and
  [HTTPX](https://github.com/encode/httpx/security/advisories) had no published
  advisories at review time. This is not a claim that the packages are vulnerability-free.

CI uses [pip-audit 2.10.1](https://github.com/pypa/pip-audit) on an export of all
runtime and development groups and extras from the committed lock. The scan runs
on Linux/Python 3.15 and honors the requirements' environment markers.
`--no-deps --disable-pip` prevents a second dependency resolution; `--strict`
fails if dependency collection fails. Auditor-tool dependencies, the operating
system and interpreter binaries are outside this project-dependency scan.

## Bot API and logging guidance

[Update.effective_message](https://docs.python-telegram-bot.org/en/v22.8/telegram.update.html#telegram.Update.effective_message)
supports regular and edited messages and may be absent. The bot's handler
regression tests exercise these update forms.
[PTB's logging example](https://docs.python-telegram-bot.org/en/v22.8/examples.errorhandlerbot.html)
raises HTTPX logging to WARNING because HTTP request logs include URLs.
[HTTPX's logging documentation](https://www.python-httpx.org/logging/) also
shows request URLs and transport logs. The HTTP client loggers now retain WARNING and ERROR diagnostics while
suppressing request traces. Application INFO logging remains enabled; production
logging must not explicitly emit token-bearing URLs.

## Lock generation and verification

The selected local Mac execution environment was unavailable during this update.
A temporary GitHub Actions job with `contents: read` therefore resolves the
dependencies with uv itself, using Python 3.15:

```sh
uv lock --upgrade
uv export --locked --no-dev --no-emit-project --no-header --format requirements-txt --output-file requirements.txt
```

Only the resulting `uv.lock` and `requirements.txt` text is returned through a
JSON log record for the maintainer to commit. No lock entries or hashes are
handwritten. The final workflow replaces this temporary generation step and
requires `uv sync --locked`. The export uses `--no-header` so that consistency
does not depend on the command's output path or header text; package hashes remain
enabled.

The [resolver run](https://github.com/McGeeLee/LoveDist_bot/actions/runs/40833518301)
on Python 3.15.2 passed Ruff and all 22 tests both normally and with
`PTB_TIMEDELTA=1`; pip-audit found no known vulnerabilities in the resolved
runtime/development dependencies. The normal compatibility run emits PTB's
expected legacy integer-`RetryAfter` deprecation warnings; the opt-in timedelta
run passes without those warnings.

The final workflow verifies frozen installation, Ruff, the offline test suite in
both RetryAfter modes and an identical requirements export on Python 3.12–3.15.
The Python 3.15 job also performs the dependency audit. Its exact-head status is
recorded in [PR #2](https://github.com/McGeeLee/LoveDist_bot/pull/2). No local
LoveDist_bot test or audit pass is claimed here.

CI disables dotenv file loading and supplies a fixed dummy Bot Token. Tests use
mock Telegram requests and temporary SQLite databases; CI does not poll Telegram
or access the deployed database.
