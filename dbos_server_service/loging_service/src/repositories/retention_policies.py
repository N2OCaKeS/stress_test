"""Репозиторий `RetentionPolicy` — global или per-(severity, service) политики хранения.

PUT без `severity_filter`/`service_filter` живёт в legacy-режиме: один row
с NULL/NULL применяется ко всем событиям. PUT с filter'ами создаёт
Cartesian product (одна строка на пару) — `apply_active` обрабатывает их
как отдельные DELETE-проходы по соответствующим фильтрам.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update as sa_update
from sqlalchemy.orm import Session

from src.core.constants import RESERVED_SERVICE_NAMES
from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.schemas.retention import RetentionPolicyCreate

# Lowercase каноническая форма — `EventCreate.service` валидатор уже
# приводит к канонике (NFKC + invisibles + confusables + lower) на ingest,
# поэтому в БД `service` лежит готовый ASCII-snake_case. Сравнение здесь
# raw-equality по этой нормализованной колонке, без `func.lower(...)` —
# иначе теряется `ix_audit_events_service` index seek и retention-sweep
# вырождается в seq-scan по миллионам строк. Литерал `"loging_service"`
# защищён от ротации (см. `apply_active`).
#
# Источник правды — `RESERVED_SERVICE_NAMES` в `core/constants.py`.
# `_PROTECTED_SERVICE` оставлен как backward-compat alias: тесты
# (`test_retention_apply.py`) импортируют его именно отсюда, и снэпшот-тест
# фиксирует литерал "loging_service". Когда reserved-set расширится до 2+
# имён — здесь надо будет переключиться на `in RESERVED_SERVICE_NAMES`
# и обновить `apply_active`/тесты соответственно.
_PROTECTED_SERVICE = "loging_service"
assert _PROTECTED_SERVICE in RESERVED_SERVICE_NAMES  # drift-guard


def get_active(db: Session) -> RetentionPolicy | None:
    """Возвращает единственную активную retention-политику (самую свежую)."""
    return db.execute(
        select(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .order_by(RetentionPolicy.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def create(db: Session, payload: RetentionPolicyCreate) -> RetentionPolicy:
    """Test-only: создаёт ОДНУ retention-политику и возвращает row напрямую.

    Production-код (endpoint'ы и `_retention_loop`) ходит через
    `create_policy`, который возвращает список row'ов (Cartesian по
    severity_filter × service_filter). Этот хелпер оставлен ради тестов,
    которые исторически писали `repo.create(db, RetentionPolicyCreate(...))`
    и ждали single-row return. Trade-off — миграция тестов на
    `create_policy(...)[0]` против поддержки тонкого wrap'ера; пока живёт.
    """
    rows = create_policy(db, payload)
    return rows[0]


def create_policy(
    db: Session, payload: RetentionPolicyCreate, *, commit: bool = True
) -> list[RetentionPolicy]:
    """Создаёт N×M retention-политик из Cartesian product (severity × service).

    Если оба filter'а пусты — пишет один row с NULL/NULL (legacy-поведение,
    применяется ко всем событиям). Иначе генерирует одну строку на каждую
    пару (severity, service), где None обрабатывается как «все severity» или
    «все сервисы» соответственно.

    Возвращает список созданных строк в порядке вставки.

    `commit=False` — для атомарного admin-CRUD: caller (endpoint)
    делает единственный `db.commit()` после deactivate + create + audit.
    """
    now = datetime.now(timezone.utc)
    severities = payload.severity_filter or [None]
    services = payload.service_filter or [None]
    created: list[RetentionPolicy] = []
    for sev in severities:
        for svc in services:
            policy = RetentionPolicy(
                severity=sev,
                service=svc,
                retain_days=payload.retain_days,
                description=payload.description,
                is_active=payload.is_active,
                created_at=now,
                updated_at=now,
            )
            db.add(policy)
            created.append(policy)
    if commit:
        db.commit()
        for p in created:
            db.refresh(p)
    else:
        db.flush()
    return created


def deactivate_all_active(db: Session, *, commit: bool = True) -> int:
    """Помечает is_active=False у всех текущих активных политик.

    Используется при PUT с filter'ами: новая filtered-политика заменяет
    предыдущий набор (single global или предыдущий Cartesian). Возвращает
    количество затронутых строк.

    `commit=False` — атомарный admin-CRUD: caller делает один commit
    после deactivate + create + audit.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        sa_update(RetentionPolicy)
        .where(RetentionPolicy.is_active == True)  # noqa: E712
        .values(is_active=False, updated_at=now)
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return result.rowcount


def list_active(db: Session) -> list[RetentionPolicy]:
    """Все активные политики, отсортированные от свежих к старым.

    Filter-режим создаёт несколько строк за один PUT — `get_active`
    возвращает только первую (legacy contract). Здесь — полный набор для
    применения в `apply_active`.
    """
    return list(
        db.execute(
            select(RetentionPolicy)
            .where(RetentionPolicy.is_active == True)  # noqa: E712
            .order_by(RetentionPolicy.created_at.desc())
        ).scalars().all()
    )


# Размер чанка для retention-DELETE. Один большой DELETE на миллионы строк
# держал бы row-locks на всю выборку, раздувал WAL и тормозил конкурентный
# ingest-INSERT. Чанкуем по этому размеру и коммитим каждый чанк — autovacuum
# успевает чистить dead tuples между коммитами, а транзакция остаётся короткой.
#
# Дефолт сознательно дублирует `Settings.retention_chunk_size`: импорт settings
# на module-level развернул бы цикл (`config` → `repositories` → `models`),
# поэтому keep-in-sync пара. `apply_active` сам уважает env-override через
# параметр `chunk_size`, который `_retention_loop` подтягивает из settings.
_SWEEP_CHUNK_SIZE = 10_000


def apply_active(
    db: Session,
    *,
    chunk_size: int = _SWEEP_CHUNK_SIZE,
    now: datetime | None = None,
) -> int:
    """Удаляет события старше `retain_days`, КРОМЕ событий `loging_service`.

    Активные политики комбинируются в одно OR-выражение: событие подлежит
    удалению, если попадает под предикат **хотя бы одной** политики. Это
    даёт честный счёт уникально удалённых строк — N-pass подход суммировал
    rowcount каждой политики и недосчитывал, когда события подпадали под
    несколько (вторая политика видела rowcount=0 на уже снесённых первой).

    Предикат одной политики:
        timestamp < now - retain_days
        AND (severity IS NULL OR event.severity = severity)
        AND (service  IS NULL OR event.service = service)

    NULL в `severity`/`service` колонке = «все severity / все сервисы»
    (legacy global-политика). Сравнение `service` идёт raw-equality
    по уже нормализованному значению (ingest-валидатор канонизирует),
    чтобы не терять `ix_audit_events_service` index seek.

    `service != _PROTECTED_SERVICE` фильтр снаружи OR'а — gating-условие,
    которое исключает loging-self-audit из удаления любой политикой. PG
    обычно сначала отрабатывает `match_any` (он селективнее по timestamp),
    а потом дочищает `!=`-anti-filter. На текущей нагрузке (~1M событий)
    план держится через `ix_audit_events_timestamp`; на 50M-таблице, если
    `_PROTECTED_SERVICE` начнёт лидировать по cardinality (массовая
    публикация loging-self-events), стоит добавить partial composite
    `(timestamp, severity) WHERE service != 'loging_service'`. Сигнал —
    рост latency retention sweep'а в `_build_retention_sweep_details`
    audit-row'ах.

    DELETE идёт чанками по `chunk_size` строк с коммитом на каждый чанк —
    на append-only журнале в миллионы строк один безлимитный DELETE держал бы
    блокировки и раздувал WAL, тормозя ingest.

    Кросс-репликовая защита (один sweep за раз) обеспечивается session-level
    `pg_try_advisory_lock` в `main._retention_loop` — он переживает
    per-chunk коммиты, потому что advisory-lock привязан к сессии, а не к
    транзакции. Здесь дополнительного лока нет сознательно: прямой вызов
    `apply_active` из тестов (или ручной admin-прогон через REPL) не должен
    блокироваться демоном и наоборот. Конкурентность безопасна по построению:
    `id_select.limit(chunk_size)` без `FOR UPDATE SKIP LOCKED` приведёт к
    тому, что в худшем случае второй sweep увидит уже удалённые id и его
    `DELETE ... WHERE id IN (...)` отдаст `rowcount=0` (повтор не «удалит
    дважды», транзакции не разъезжаются). Цена — лишний RTT и шум в логах
    на тестовом стенде; в production daemon под advisory-lock один.

    Важно: advisory-lock сидит ТОЛЬКО в `main._retention_loop`. При ручном
    запуске `apply_active` из REPL / admin-скрипта / разовой миграции
    параллельно с работающим daemon'ом lock не сработает — будут два
    конкурентных sweep'а одновременно. Это безопасно по описанной выше
    логике (повторный `DELETE ... WHERE id IN (...)` отдаёт 0), но в логах
    появится двойной шум и счётчик `total` ручного прогона может оказаться
    меньше ожидаемого: часть row'ов снёс daemon. Для атомарного admin-
    прогона daemon надо остановить.

    Snapshot политик фиксируется на старте sweep'а ОДИН раз (`list_active`
    до цикла). Это сознательный contract: если админ через PUT
    /retention поменяет `retain_days` пока идёт chunked-DELETE, sweep
    дорабатывает по старому набору правил. Альтернатива (перечитывать
    `policies` каждый чанк) даст «частично применённый» режим, в котором
    половина журнала уже снесена по старому retain_days, а вторая — по
    новому, что хуже для аудита и SOC-отчётности. Свежие политики
    подхватятся следующим suite-tick'ом (через 24 часа); до этого следующий
    admin-PUT не догонит уже бегущий sweep.

    Возвращает количество уникально удалённых событий.
    """
    policies = list_active(db)
    if not policies:
        return 0

    # `now` снимается ОДИН раз на старте sweep'а; на 50M-таблице chunked-DELETE
    # идёт часами, и без зафиксированного cutoff'а каждый chunk удалял бы
    # «всё, что старше cutoff на момент чанка», постепенно сдвигая границу
    # внутрь in-flight ingest'а. Зафиксированный `now` гарантирует, что
    # события, упавшие в БД позже старта sweep'а, точно не будут затронуты —
    # by-design, не баг.
    # Caller (`main._retention_loop`) может пробросить тот же `now`, что
    # пишется в audit-details `cutoff_at` — тогда audit и sweep гарантированно
    # говорят про одно и то же окно.
    if now is None:
        now = datetime.now(timezone.utc)
    policy_predicates = []
    for policy in policies:
        cutoff = now - timedelta(days=policy.retain_days)
        clauses = [AuditEvent.timestamp < cutoff]
        if policy.severity is not None:
            clauses.append(AuditEvent.severity == policy.severity)
        if policy.service is not None:
            clauses.append(AuditEvent.service == policy.service)
        policy_predicates.append(and_(*clauses))

    # Один OR на все политики — событие удаляется, если попадает под любую.
    # Планировщик на multimillion-журнале — lottery: без composite
    # `(service, timestamp, id)` запрос идёт либо `ix_audit_events_timestamp`
    # + filter on service, либо bitmap-OR по нескольким single-column
    # индексам. На 50M-таблице обе стратегии становятся seq-scan-friendly.
    # Если retention sweep начнёт упираться в latency — собрать EXPLAIN
    # (ANALYZE, BUFFERS) и добавить partial composite (`severity IS NULL`
    # ветка лидирует по cardinality). Текущая нагрузка (~1M событий)
    # держит план через timestamp-index.
    match_any = or_(*policy_predicates)
    id_select = (
        select(AuditEvent.id)
        .where(
            match_any,
            # `service` уже нормализован на ingest (NFKC + invisibles +
            # confusables + lower), поэтому raw-equality достаточно и
            # даёт index seek по `ix_audit_events_service`.
            AuditEvent.service != _PROTECTED_SERVICE,
        )
        # Детерминированный порядок чанков. Без явного ORDER BY PostgreSQL
        # может вернуть разные id в каждом LIMIT-блоке: между чанками
        # коммитим, autovacuum чистит dead tuples, план может перейти на
        # другой индекс или bitmap heap scan. Это не приводит к двойному
        # удалению (DELETE ... WHERE id IN (...) на уже снесённых id даёт
        # rowcount=0), но в редких сценариях chunk-loop мог не сойтись,
        # пока не закончились matching row'ы — порядком id.asc() это
        # гарантируется монотонно.
        .order_by(AuditEvent.id.asc())
        .limit(chunk_size)
    )

    total = 0
    while True:
        stmt = delete(AuditEvent).where(
            AuditEvent.id.in_(id_select.scalar_subquery())
        )
        result = db.execute(stmt)
        db.commit()
        deleted = result.rowcount
        total += deleted
        if deleted < chunk_size:
            break
    return total
