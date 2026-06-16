# pip-dependency inventory report — 2026-06-15

Generated from 5 scan files covering **21 branches**.

All 5 scan files parsed cleanly (valid JSON arrays).

## Headline counts

- Distinct packages in requirements-all.txt: **117**
- Total lines / distinct download specs in requirements-all.txt: **170**
- Distinct pinned specs (non-bare lines): **132**
- Review candidates (requirements-review.txt): **1**
- Dropped (stdlib): none

## Packages with MULTIPLE conflicting exact (==) pins

True version conflicts — distinct exact versions that must each be stored.

- **cryptography**: 44.0.2, 44.0.3

## Packages with >1 distinct spec line (mixed pins/ranges)

Each distinct spec = a separate `pip download`; these need multiple wheels offline.

- **alembic**: ==1.15.2, >=1.13, >=1.16.0,<2.0.0, >=1.16.2,<2.0.0
- **asyncpg**: >=0.29, >=0.30.0,<0.31.0
- **asyncssh**: ==2.23.0, >=2.14
- **celery**: >=5.4, >=5.5.3,<6.0.0
- **click**: >=8.1, (bare)
- **cryptography**: ==44.0.2, ==44.0.3, >=43.0, >=45.0.4,<46.0.0, >=45.0.6,<46.0.0
- **devpi-client**: ==7.2.0, (bare)
- **fastapi**: ==0.115.12, >=0.115, >=0.115.12,<0.116.0, >=0.115.13,<0.116.0, >=0.115.14,<0.116.0
- **gunicorn**: >=22.0, >=23.0.0,<24.0.0
- **httpx**: ==0.28.1, >=0.27, >=0.28.1,<0.29.0
- **paramiko**: ==3.5.1, >=3.0, >=3.5.1,<4.0.0, >=4.0.0,<5.0.0, (bare)
- **poetry-core**: >=2.0.0,<3.0.0, (bare)
- **psycopg**: ==3.2.6, (bare)
- **psycopg2-binary**: >=2.9, >=2.9.10,<3.0.0, (bare)
- **pydantic**: ==2.11.3, >=2.7
- **pydantic-settings**: ==2.8.1, >=2.10.1,<3.0.0, >=2.4, >=2.9.1,<3.0.0
- **pytest**: ==9.0.3, >=8.0, >=8.3.5
- **pytest-asyncio**: >=0.23, >=1.0
- **python-multipart**: ==0.0.20, >=0.0.20,<0.0.21, >=0.0.9
- **pytz**: ==2025.2, >=2024.1, (bare)
- **redis**: ==5.2.1, >=4.5.2,<6.0.0, >=5.0, >=6.4.0,<7.0.0
- **requests**: ==2.32.3, >=2.32, (bare)
- **scikit-learn**: ==1.7.2, (bare)
- **setuptools**: ==78.1.0, (bare)
- **slowapi**: ==0.1.9, >=0.1.9
- **sqlalchemy**: ==2.0.40, >=2.0, >=2.0.41,<3.0.0, >=2.0.43,<3.0.0
- **uvicorn**: ==0.34.0, >=0.32, >=0.34.2,<0.35.0, >=0.34.3,<0.35.0, >=0.35.0,<0.36.0

## Name mapping applied (IMPORT_ONLY rows)

| import module | -> PyPI dist | status | note |
|---|---|---|---|
| `aiofiles` | aiofiles | same-name |  |
| `aiogram` | aiogram | same-name |  |
| `aiohttp_socks` | aiohttp-socks | confident |  |
| `alembic` | alembic | same-name |  |
| `ansible` | ansible | same-name |  |
| `argon2` | argon2-cffi | confident |  |
| `asyncpg` | asyncpg | same-name |  |
| `asyncssh` | asyncssh | same-name |  |
| `atlassian` | atlassian-python-api? | REVIEW | import 'atlassian' -> almost certainly atlassian-python-api (declared elsewhere); confirm not first-party |
| `bs4` | beautifulsoup4 | confident |  |
| `celery` | celery | same-name |  |
| `click` | click | same-name |  |
| `cryptography` | cryptography | same-name |  |
| `dotenv` | python-dotenv | confident |  |
| `fabric` | ? | REVIEW | unrecognized |
| `fastapi` | fastapi | same-name |  |
| `flask` | flask | same-name |  |
| `flask_cors` | Flask-Cors | confident |  |
| `httpx` | httpx | same-name |  |
| `hypothesis` | hypothesis | same-name |  |
| `invoke` | ? | REVIEW | unrecognized |
| `itcase_sphinx_theme` | itcase-sphinx-theme | confident |  |
| `jose` | python-jose | confident |  |
| `jwt` | PyJWT | confident |  |
| `kombu` | kombu | same-name |  |
| `kubernetes` | kubernetes | same-name |  |
| `matplotlib` | matplotlib | same-name |  |
| `numpy` | numpy | same-name |  |
| `pandas` | pandas | same-name |  |
| `paramiko` | paramiko | same-name |  |
| `passlib` | passlib | same-name |  |
| `pretty_html_table` | pretty-html-table | confident |  |
| `psutil` | psutil | same-name |  |
| `psycopg` | ? | REVIEW | unrecognized |
| `psycopg2` | psycopg2-binary | REVIEW | import psycopg2 -> could be psycopg2 OR psycopg2-binary; flagging both |
| `psycopg_pool` | psycopg-pool | confident |  |
| `pydantic` | pydantic | same-name |  |
| `pydantic_core` | pydantic-core | confident |  |
| `pydantic_settings` | pydantic-settings | confident |  |
| `pysnooper` | pysnooper | same-name |  |
| `pytest` | pytest | same-name |  |
| `pytest_asyncio` | pytest-asyncio | confident |  |
| `pytz` | pytz | same-name |  |
| `redfish` | redfish | same-name |  |
| `redis` | redis | same-name |  |
| `requests` | requests | same-name |  |
| `respx` | ? | REVIEW | unrecognized |
| `scipy` | scipy | same-name |  |
| `selenium` | selenium | same-name |  |
| `setuptools` | setuptools | same-name |  |
| `sklearn` | scikit-learn | confident |  |
| `slowapi` | slowapi | same-name |  |
| `sqlalchemy` | sqlalchemy | same-name |  |
| `sqlalchemy_utils` | SQLAlchemy-Utils | confident |  |
| `starlette` | starlette | same-name |  |
| `structlog` | structlog | same-name |  |
| `taskiq` | taskiq | same-name |  |
| `taskiq_redis` | taskiq-redis | confident |  |
| `uvicorn` | uvicorn | same-name |  |
| `websockets` | websockets | same-name |  |

## Per-branch package counts (master-list packages)

| branch | packages |
|---|---|
| allta_app | 7 |
| allta_vmmanager | 15 |
| apache2 | 15 |
| astra_openvpn | 5 |
| backup/pre-ai-cleanup | 24 |
| cli | 9 |
| dbos_server_service | 26 |
| dev_allta_app | 7 |
| dev_allta_app_demo | 38 |
| dev_allta_auth | 16 |
| dev_allta_vmmanager | 17 |
| dev_astra_openvpn | 5 |
| dev_cli | 7 |
| dev_kernel | 4 |
| dev_libs | 47 |
| dev_network | 4 |
| dev_osbench | 14 |
| dev_postgresql | 20 |
| libs | 9 |
| network | 0 |
| postgresql | 6 |

## Anomalies & notes

- `backup/pre-ai-cleanup` branch duplicates `dbos_server_service` pins but with `secret_service` (renamed/removed) — likely stale; kept since versions match current except where noted.
- `pytest==9.0.3` pinned in dbos services — 9.x did not exist at scan time (likely a typo for 8.x); kept verbatim per owner decision but flagged for download verification.
- `cryptography` spans many versions across branches (44.0.2 / 44.0.3 pinned + several >=45 ranges) — heavy offline footprint.
- `fastapi`/`uvicorn`/`pydantic-settings` have both hard pins (dbos) and ranges (vmmanager/auth) — multiple wheels needed.
- `asyncio` appears as a DECLARED requirements line in dev_postgresql/postgresql `req.txt`. It is kept in the master list (it is a declared dependency), but note: PyPI `asyncio` is a stdlib backport shim for Python<3.4 and is almost certainly a stale/no-op requirement that should be removed from those req.txt files.
- `psycopg2` (import) flagged to review: repo declares `psycopg2-binary` in dev_allta_app_demo/postgresql, so the binary variant is downloaded; bare `psycopg2` source build left for owner.
- `psycopg_pool` (import in postgresql) maps to PyPI `psycopg-pool`; the matching declared row `psycopg[binary]` is a different distribution (psycopg v3) - both kept.
- Poetry caret/tilde constraints (`^1.0`, `^6.0`, `^2.32`, `^8.3.5`, `^0.1.9`) were lowered to `>=` pins so they are concretely downloadable (upper bound dropped).
- `pip-install` source rows in apache2/astra_openvpn used spaced specs like `== 2.2.3` (normalized to `==2.2.3`).
