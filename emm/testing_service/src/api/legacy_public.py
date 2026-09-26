"""Публичный compat `/rest/api/*` по легаси-путям.

Скрипты на стендах (`*_conf.py`, `allta._zefir`, `prepare.sh`,
`allta._VirtInstall`) ходят на легаси-хост ALLTA без авторизации:
`http://allta.devos.astralinux.ru/rest/api/get-repo-path` и т.п. Ветки не
меняются (D17), поэтому после переноса DNS на платформу эти пути отвечает
testing_service — тот же путь, тот же формат (`allta_front.py:886-952`):

| Путь | Легаси | Здесь |
|---|---|---|
| `get-jira-url`, `get-confluence-url` | голая строка `jira.astralinux.ru` | URL отдела стенда без схемы |
| `get-repo-path` | файл `releases.json` | тот же JSON, `attachment` |
| `get-repo-path-as-json` | текст `releases.json` | тот же JSON |
| `get-astra-config`, `get-box-config` | файл `*.json` | тот же JSON, `attachment` |
| `get-stand` | файл `stand.html` | HTML, `attachment` |
| `get-testname-columns`, `known-bugs`, `annotations` | `jsonify(...)` | JSON |
| `POST available-kernels-from-<rc>` | JSON-список ядер | JSON-список ядер |

Не переносятся: `get-times` (исключён ещё в §12), `get-ping*-pic`
(картинки телеграм-бота), маршруты очереди/стендов/логов — это не
справочники, у них есть авторизованные аналоги.

Авторизации нет — только несекретные справочники. Доступ ограничен
подсетями источника (`compat_allowed_networks`, редактируются в UI);
чужой адрес — 403 `LEGACY_COMPAT_SOURCE_FORBIDDEN`. IP источника — с учётом
доверенных прокси (`services/client_ip.py`). Лимит запросов — `core/limiter`
с ключом по тому же IP (`LEGACY_COMPAT_RATE_LIMIT`), глобальный лимит к этим
маршрутам не применяется.

Неизвестный путь под `/rest/api/` из разрешённой подсети — 404 в легаси-форме
`{"error": "Not found", "message": ...}` (`allta_front.py:117-124`).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.limiter import limiter
from src.dependencies.db import get_db
from src.services import audit_context, client_ip
from src.services import legacy_compat as svc
from src.services.legacy_compat import CompatSource

PREFIX = "/rest/api"

router = APIRouter(prefix=PREFIX, tags=["legacy-public"])

# Flask отдаёт `str` из view как text/html (`allta_front.py:911-918`).
_TEXT = "text/html; charset=utf-8"


def _limit_key(request: Request) -> str:
    return client_ip.source_ip(request) or "unknown"


_RATE_LIMIT = get_settings().legacy_compat_rate_limit


async def allowed_source(request: Request, db: AsyncSession = Depends(get_db)) -> CompatSource:
    """Пропустить только разрешённые подсети; IP источника — и в аудит-контекст."""
    ip = client_ip.source_ip(request)
    audit_context.update_context(ip_address=ip)
    return await svc.check_source(db, ip)


def _json(data, *, filename: str | None = None, indent: int | None = None) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else None
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=indent),
        media_type="application/json",
        headers=headers,
    )


@router.get("/get-jira-url", summary="[legacy] URL Jira отдела стенда (текст без схемы)")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_jira_url(
    request: Request,
    source: CompatSource = Depends(allowed_source),
    db: AsyncSession = Depends(get_db),
) -> Response:
    url = await svc.integration_url_for_source(db, source, "jira_base_url", "get-jira-url")
    return Response(content=url, media_type=_TEXT)


@router.get("/get-confluence-url", summary="[legacy] URL Confluence отдела стенда (текст без схемы)")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_confluence_url(
    request: Request,
    source: CompatSource = Depends(allowed_source),
    db: AsyncSession = Depends(get_db),
) -> Response:
    url = await svc.integration_url_for_source(db, source, "confluence_base_url", "get-confluence-url")
    return Response(content=url, media_type=_TEXT)


@router.get("/get-repo-path", summary="[legacy] releases.json: строки sources.list по версиям (файл)")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_repo_path(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(await svc.get_repo_paths(), filename="releases.json", indent=4)


@router.get("/get-repo-path-as-json", summary="[legacy] releases.json: строки sources.list по версиям")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_repo_path_as_json(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(await svc.get_repo_paths(), indent=4)


@router.get("/get-astra-config", summary="[legacy] astra-config.json (файл)")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_astra_config(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(await svc.get_astra_config(), filename="astra-config.json", indent=4)


@router.get("/get-box-config", summary="[legacy] box-config.json (файл), адрес FTP — из FTP_URL")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_box_config(
    request: Request,
    _: CompatSource = Depends(allowed_source),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _json(await svc.box_config(db), filename="box-config.json", indent=4)


@router.get("/get-testname-columns", summary="[legacy] короткие колонки СТП по имени теста")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_testname_columns(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(svc.get_testname_columns())


@router.get("/get-stand", summary="[legacy] легенда стендов stand.html (файл)")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_stand(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return Response(
        content=svc.get_stand_description_html(),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="stand.html"'},
    )


@router.get("/known-bugs", summary="[legacy] известные баги по категории теста")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_known_bugs(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(svc.get_known_bugs())


@router.get("/annotations", summary="[legacy] аннотации к артефактам по категории")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def get_annotations(request: Request, _: CompatSource = Depends(allowed_source)) -> Response:
    return _json(svc.get_annotations())


@router.post("/available-kernels-from-{rc}", summary="[legacy] ядра, доступные для версии")
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def available_kernels_from_rc(
    rc: str, request: Request, _: CompatSource = Depends(allowed_source),
) -> Response:
    return _json(await svc.available_kernels_from_rc(rc))


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
@limiter.limit(_RATE_LIMIT, key_func=_limit_key)
async def not_found(path: str, request: Request, _: CompatSource = Depends(allowed_source)) -> JSONResponse:
    """Легаси-форма 404 для остальных `/rest/api/*` (`allta_front.py:117-124`)."""
    return JSONResponse(
        status_code=404,
        content={"error": "Not found", "message": f"Endpoint {request.url.path} not found"},
    )
