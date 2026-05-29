"""Brute-force lockout helper для login-pipeline'а.

Изолирует счётчик `failed_login_attempts` и lockout-окно (`locked_until`)
от остальной аутентификации. Caller'ы (`auth_service.verify_password_with_lockout`,
`docker_registry_service`) не должны знать про конкретные репо-вызовы и про
формулу «5 attempts → 15 минут» — параметры передаются явно (DI), без
скрытого `get_settings()` внутри helper'а: так helper тривиально юнит-тестируется
и не тянет глобальный конфиг.

Поведение зеркалит то, что раньше жило inline в `verify_password_with_lockout`:

* `release_if_expired(user)` — JIT-сброс истёкшего lockout'а до verify'я.
* `assert_not_locked(user)` — кидает `AuthorizationError(ACCOUNT_TEMPORARILY_LOCKED, 429)`
  если lockout активен; рассчитывает `retry_after_seconds` относительно `utcnow`.
* `register_failure(user, max_attempts, lockout_minutes)` — атомарный
  инкремент счётчика + установка `locked_until`, если перевалили лимит.
* `register_success(user)` — обнуление счётчика после успешного verify'я.

Commit'ы остаются на caller'е: helper меняет только репо + ORM-инстанс,
транзакционная рамка решается выше (см. docstring `verify_password_with_lockout`
про «commit ДО raise»).
"""

from datetime import timezone

from src.core.exceptions import AuthorizationError
from src.repositories.users import UserRepository
from src.utils.time import expires_at, is_expired, utcnow


async def release_if_expired(user_repo: UserRepository, user) -> bool:
    """Сбросить счётчик, если `locked_until` уже истёк.

    Возвращает True, если был сброс (caller, скорее всего, захочет
    немедленно закоммитить, чтобы concurrent-вызовы не гонкались на
    stale `locked_until`).
    """
    if user.locked_until and is_expired(user.locked_until):
        await user_repo.reset_failed_attempts(user)
        return True
    return False


def assert_not_locked(user) -> None:
    """Кинуть 429, если активный lockout ещё не истёк.

    Делается ДО verify_password — чтобы не платить Argon2id за залоченный
    аккаунт (brute-force amplifier) и держать timing симметричным.
    """
    if not user.locked_until:
        return
    if is_expired(user.locked_until):
        return
    locked_dt = (
        user.locked_until
        if user.locked_until.tzinfo
        else user.locked_until.replace(tzinfo=timezone.utc)
    )
    retry_secs = int((locked_dt - utcnow()).total_seconds())
    raise AuthorizationError(
        error_code="ACCOUNT_TEMPORARILY_LOCKED",
        message="Account is temporarily locked",
        details={"retry_after_seconds": retry_secs},
        http_status=429,
    )


async def register_failure(
    user_repo: UserRepository,
    user,
    max_attempts: int,
    lockout_minutes: int,
) -> None:
    """Учесть очередную неудачную попытку: инкремент + опциональный lockout.

    Атомарный `UPDATE ... RETURNING` — concurrent failures не гонкуются
    на stale in-memory счётчике. Если новое значение >= `max_attempts`,
    выставляем `locked_until = now + lockout_minutes`.

    Commit caller-side, иначе внешний `get_db()`-rollback стирает счётчик.
    """
    await user_repo.increment_failed_attempts(user)
    if user.failed_login_attempts >= max_attempts:
        await user_repo.set_locked_until(user, expires_at(minutes=lockout_minutes))


async def register_success(user_repo: UserRepository, user) -> None:
    """Сбросить счётчик + `locked_until` после успешного verify'я."""
    await user_repo.reset_failed_attempts(user)


# ── Generic helpers для OAuth/Bot lockout ──────────────────────────────────────
# Те же инварианты, что и для user-lockout (commit-до-raise остаётся на caller'е),
# но репо/принципал передаются как duck-typed объекты с увеличивать/сетить/
# ресетить-методами. Так избегаем общего базового класса и тесно связанной
# иерархии — нужны только три конкретных метода.

async def release_principal_if_expired(
    repo, principal, *, reset_attr: str = "reset_failed_attempts"
) -> bool:
    """Generic-аналог `release_if_expired` для OAuth/Bot principal'а."""
    if principal.locked_until and is_expired(principal.locked_until):
        await getattr(repo, reset_attr)(principal)
        return True
    return False


def assert_principal_not_locked(principal) -> None:
    """Generic-аналог `assert_not_locked` для произвольного principal'а
    с полем `locked_until`."""
    if not principal.locked_until:
        return
    if is_expired(principal.locked_until):
        return
    locked_dt = (
        principal.locked_until
        if principal.locked_until.tzinfo
        else principal.locked_until.replace(tzinfo=timezone.utc)
    )
    retry_secs = int((locked_dt - utcnow()).total_seconds())
    raise AuthorizationError(
        error_code="ACCOUNT_TEMPORARILY_LOCKED",
        message="Account is temporarily locked",
        details={"retry_after_seconds": retry_secs},
        http_status=429,
    )


async def register_principal_failure(
    repo,
    principal,
    *,
    counter_attr: str,
    increment_method: str,
    set_locked_method: str = "set_locked_until",
    max_attempts: int,
    lockout_minutes: int,
) -> None:
    """Атомарный инкремент произвольного счётчика + опциональный lockout.

    `counter_attr` — имя поля на ORM-инстансе (`failed_secret_attempts` /
    `failed_token_attempts`). `increment_method` — имя метода репо.
    """
    await getattr(repo, increment_method)(principal)
    if getattr(principal, counter_attr) >= max_attempts:
        await getattr(repo, set_locked_method)(
            principal, expires_at(minutes=lockout_minutes)
        )
