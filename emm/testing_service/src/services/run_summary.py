"""End-of-run комментарий в Confluence-блоге (§2.7, §9.2 плана миграции).

`post_run_summary()` вызывается из `services/queue.py` там же, где
`test_run_status.recompute()` переводит `test_runs.status` в терминальное
состояние (`succeeded`/`failed`/`partially_failed`) — best-effort, симметрично
`stp_status.sync_cell_from_queue_item`: сбой здесь не должен как-либо влиять
на уже завершённый прогон, ни один вызов не поднимает исключение наружу.

Перенос легаси `SendCommentToConfluence` (`allta_app/libs/libconfluence.py`,
вызывается из `allta_back.py` один раз в самом конце прогона): идемпотентный
комментарий на уже существующем blog-посте, найденном по title-шаблону
версии RC, со ссылкой на уже существующую STP-страницу статистики. Ни
blog-пост, ни STP-страница здесь не создаются — оба остаются внешними
предусловиями, как и в легаси.

Отличия от легаси (осознанные, см. план миграции):

* учётка Confluence — per-department (`department_integration_settings` +
  `secret_client.reveal_credential`), не единый хардкод; предпочитает
  `confluence_credential_id`, при его отсутствии — `credential_id` (C4,
  совместимость с прежней общей учёткой Jira+Confluence);
* вместо «один раз добавить и не трогать» — обновляем существующий
  комментарий при повторном прогоне того же RC (`confluence_comment_id`
  хранится для `update_comment`, `body_snapshot` — basis для diff, чтобы не
  дёргать Confluence API, если текст не изменился);
* «пост/страница не найдены» — видимый `status` в `run_summary_comments`
  (`skipped_no_blog`/`skipped_no_stp_page`), а не потерянный молча no-op.

Легаси ищет STP-страницу под конкретной родительской страницей (разные root
id для веток 1.7.x/1.8.x) — здесь такого понятия "родительская страница
ветки" нет, поэтому STP-страница ищется просто по точному заголовку в том
же пространстве `AL`, без привязки к parent (упрощение, см. отчёт волны).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import RunSummaryCommentStatus
from src.core.exceptions import AppException, NotFoundError
from src.dependencies.auth import Identity
from src.models import RunSummaryComment
from src.repositories import department_integration_settings as dis_repo
from src.repositories import run_summary_comment as repo
from src.repositories import test_run as test_run_repo
from src.services import audit_service, confluence_client, permissions, secret_client, server_client
from src.utils.ids import run_summary_comment_id as new_id

logger = logging.getLogger(__name__)

_CONFLUENCE_SPACE = "AL"
_COMMENT_TITLE = "Нагрузочное тестирование"


def render_titles(version: str, rc_label: str, *, is_urgent_update: bool = False) -> tuple[str, str]:
    """`(stp_page_title, blog_post_title)` — легаси-шаблон (§9.2).

    Обычный релиз (4 сегмента `X.Y.Z.W`): версия релиза — первые три
    сегмента, заголовок блога — "{RC} оперативного обновления Astra Linux SE
    {X.Y.Z}". Срочное/hotfix-обновление (`is_urgent_update`, версия из 6
    сегментов, 4-й сегмент буквально `UU`/`uu` — маркер-разделитель, не часть
    номера): версия собирается из первых трёх сегментов плюс пятого
    (`X.Y.Z.W`, сам маркер `UU` в неё не входит), заголовок блога — "{RC}
    срочного обновления Astra Linux SE {X.Y.Z.W}". STP-страница ищется по
    заголовку `"STRESS_report ⬝ {версия}"` с той же версией, что и в
    заголовке блога.

    `{RC}` — легаси `rc_number` (`"RC3"`) — ручная метка на самой OS-версии
    (`os_versions.rc_number` в `server_service`), ОТДЕЛЬНАЯ от `version`
    (`"1.8.5.46"`) — легаси заголовок `"RC3 оперативного обновления Astra
    Linux SE 1.8.5"` несёт оба значения сразу, путать их нельзя.

    Формат `version`, не подпадающий ни под один из этих двух случаев (в
    легаси не встречался), трактуется как обычный релиз с версией из
    доступных сегментов — консервативный дефолт, не падение.

    `version`/`is_urgent_update` — резолвятся вызывающим кодом через
    `server_client.resolve_os_version_info` (не путать `os_version_id` с
    версией — id каталога никогда не должен попадать в заголовок).
    """
    parts = version.split(".")
    if is_urgent_update and len(parts) == 6 and parts[3].upper() == "UU":
        release = ".".join([parts[0], parts[1], parts[2], parts[4]])
        blog_title = f"{rc_label} срочного обновления Astra Linux SE {release}"
    else:
        release = ".".join(parts[:3]) if len(parts) >= 3 else version
        blog_title = f"{rc_label} оперативного обновления Astra Linux SE {release}"
    stp_title = f"STRESS_report ⬝ {release}"
    return stp_title, blog_title


def _render_body(stp_page_url: str, blog_title: str) -> str:
    """Легаси-текст ссылки (`libconfluence.py::_get_load_page_statistics_url`):
    `Добавлены результаты для "{blog_title}"` — цитирует заголовок ЭТОГО ЖЕ
    блог-поста, а не голый URL."""
    return (
        f"<p><strong>{_COMMENT_TITLE}</strong></p>"
        f'<p><a href="{stp_page_url}">Добавлены результаты для "{_escape_html(blog_title)}"</a></p>'
        f"<p><em>this comment was automatically created</em></p>"
    )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def _resolve_confluence_bearer(db: AsyncSession, department_id: str) -> tuple[str, str] | None:
    """`(confluence_base_url, bearer_token)` для отдела, либо `None` — не настроено/недоступно.

    Тот же приём, что `stp.py::_resolve_jira_bearer` — `None` вызывающий код
    трактует как `status=failed`, не как исключение.
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.confluence_base_url:
        return None
    cred_id = settings.confluence_credential_id or settings.credential_id
    if not cred_id:
        return None
    try:
        _login, secret = await secret_client.reveal_credential(cred_id)
    except AppException as exc:
        logger.warning(
            "run_summary: reveal_credential failed for dept=%s cred=%s: %s",
            department_id, cred_id, exc.message,
        )
        return None
    if not secret:
        return None
    return settings.confluence_base_url, secret


async def _save(
    db: AsyncSession,
    existing: RunSummaryComment | None,
    test_run_id: str,
    *,
    status: str,
    stp_page_id: str | None = None,
    confluence_blog_id: str | None = None,
    confluence_comment_id: str | None = None,
    body_snapshot: str | None = None,
) -> RunSummaryComment:
    changes: dict = {
        "status": status,
        "stp_page_id": stp_page_id,
        "confluence_blog_id": confluence_blog_id,
        "confluence_comment_id": confluence_comment_id,
        "body_snapshot": body_snapshot,
    }
    if status == RunSummaryCommentStatus.POSTED:
        changes["posted_at"] = datetime.now(timezone.utc)

    if existing is None:
        obj = await repo.create(db, {"id": new_id(), "test_run_id": test_run_id, **changes})
    else:
        obj = await repo.update(db, existing, changes)

    audit_service.emit(
        "run_summary_comment.posted",
        target_id=obj.id, target_type="run_summary_comment",
        status="success" if status == RunSummaryCommentStatus.POSTED else "failure",
        allowed=True,
        details={"test_run_id": test_run_id, "status": status},
    )
    return obj


async def _do_post_run_summary(
    db: AsyncSession, run, existing: RunSummaryComment | None,
) -> RunSummaryComment:
    ctx = await _resolve_confluence_bearer(db, run.department_id)
    if ctx is None:
        return await _save(db, existing, run.id, status=RunSummaryCommentStatus.FAILED)
    base_url, bearer_token = ctx

    os_version_info = await server_client.resolve_os_version_info(run.os_version_id)
    if not os_version_info.rc_number:
        return await _save(db, existing, run.id, status=RunSummaryCommentStatus.SKIPPED_NO_RC_NUMBER)
    stp_title, blog_title = render_titles(
        os_version_info.name, os_version_info.rc_number,
        is_urgent_update=os_version_info.is_urgent_update,
    )

    stp_page_id = await confluence_client.find_page_id(
        base_url=base_url, bearer_token=bearer_token, space=_CONFLUENCE_SPACE, title=stp_title,
    )
    if stp_page_id is None:
        return await _save(db, existing, run.id, status=RunSummaryCommentStatus.SKIPPED_NO_STP_PAGE)

    blog_id = await confluence_client.find_blogpost_id(
        base_url=base_url, bearer_token=bearer_token, space=_CONFLUENCE_SPACE, title=blog_title,
    )
    if blog_id is None:
        return await _save(
            db, existing, run.id,
            status=RunSummaryCommentStatus.SKIPPED_NO_BLOG, stp_page_id=stp_page_id,
        )

    stp_page_url = f"{base_url}/pages/viewpage.action?pageId={stp_page_id}"
    body_html = _render_body(stp_page_url, blog_title)

    if existing is not None and existing.confluence_comment_id and existing.body_snapshot == body_html:
        # Ничего не изменилось с прошлого прогона того же RC — Confluence не дёргаем.
        return await _save(
            db, existing, run.id,
            status=RunSummaryCommentStatus.POSTED, stp_page_id=stp_page_id,
            confluence_blog_id=blog_id, confluence_comment_id=existing.confluence_comment_id,
            body_snapshot=body_html,
        )

    if existing is not None and existing.confluence_comment_id:
        comments = await confluence_client.get_comments(
            base_url=base_url, bearer_token=bearer_token, content_id=blog_id,
        )
        current = next(
            (c for c in comments if c.get("id") == existing.confluence_comment_id), None,
        )
        if current is not None:
            version_number = (current.get("version") or {}).get("number", 1)
            await confluence_client.update_comment(
                base_url=base_url, bearer_token=bearer_token,
                comment_id=existing.confluence_comment_id, body_html=body_html,
                version=version_number,
            )
            comment_id = existing.confluence_comment_id
        else:
            # Комментарий пропал на стороне Confluence (удалён вручную) — заводим заново.
            comment_id = await confluence_client.add_comment(
                base_url=base_url, bearer_token=bearer_token, content_id=blog_id, body_html=body_html,
            )
    else:
        comment_id = await confluence_client.add_comment(
            base_url=base_url, bearer_token=bearer_token, content_id=blog_id, body_html=body_html,
        )

    return await _save(
        db, existing, run.id,
        status=RunSummaryCommentStatus.POSTED, stp_page_id=stp_page_id,
        confluence_blog_id=blog_id, confluence_comment_id=comment_id, body_snapshot=body_html,
    )


async def post_run_summary(db: AsyncSession, test_run_id: str) -> RunSummaryComment | None:
    """Попытаться опубликовать/обновить end-of-run комментарий кампании.

    Best-effort целиком: любой сбой (интеграция не настроена, reveal не
    прошёл, Confluence недоступен, неожиданный ответ) оседает в
    `run_summary_comments.status=failed` и логе, наружу не поднимается —
    вызывается после того, как прогон уже завершён терминально, роняться
    здесь нечему.
    """
    run = await test_run_repo.get_by_id(db, test_run_id)
    if run is None:
        return None

    existing = await repo.get_by_test_run_id(db, test_run_id)
    try:
        result = await _do_post_run_summary(db, run, existing)
    except Exception as exc:  # noqa: BLE001 — best-effort, caller не должен упасть
        logger.warning("run_summary.post_run_summary failed for test_run %s: %s", test_run_id, exc)
        result = await _save(db, existing, test_run_id, status=RunSummaryCommentStatus.FAILED)

    await db.commit()
    return result


async def get_run_summary(db: AsyncSession, identity: Identity, test_run_id: str) -> dict:
    """`GET /test-runs/{id}/summary-comment` — статус для отображения в UI прогона.

    Строки может не быть (кампания ещё не завершилась терминально, либо
    завершилась до того, как эта фича появилась) — тогда поля, кроме
    `test_run_id`, пустые, это не 404.
    """
    run = await test_run_repo.get_by_id(db, test_run_id)
    if run is None:
        raise NotFoundError(error_code="TEST_RUN_NOT_FOUND", message="Test run not found")
    permissions.require_own_department(identity, run.department_id)

    row = await repo.get_by_test_run_id(db, test_run_id)
    if row is None:
        return {
            "id": None,
            "test_run_id": test_run_id,
            "status": None,
            "confluence_blog_id": None,
            "confluence_comment_id": None,
            "stp_page_id": None,
            "posted_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "test_run_id": row.test_run_id,
        "status": row.status,
        "confluence_blog_id": row.confluence_blog_id,
        "confluence_comment_id": row.confluence_comment_id,
        "stp_page_id": row.stp_page_id,
        "posted_at": row.posted_at,
        "updated_at": row.updated_at,
    }
