"""Чистые билдеры командных строк для OS-учёток и онбординга управления.

Единый источник shell-строк, которые worker гоняет по SSH при заведении,
обновлении и сносе OS-учёток, а также при онбординге управляющего
пользователя. Один и тот же набор операций нужен в двух флейворах транспорта,
и формы команд у них разные:

* ``server`` — прямая управляющая сессия (:class:`clients.ssh.SshClient`).
  Пароль и ключ уходят на stdin процесса, идемпотентность держится отдельными
  ``getent``/``id``-пробами, ключ пишется через getent-home guard под sudo.
* ``vm`` — вложенный ``ssh`` в гостя из hub-сессии
  (:mod:`tasks._accounts_common`). Пароль и ключ инлайнятся прямо в одну
  строку (``echo login:pwd | chpasswd``, base64-обёрнутый ключ), а
  идемпотентность — через ``id ... ||``.

Формы разъезжаются из-за транспорта, поэтому здесь по функции на операцию с
явным аргументом ``flavor`` (``SERVER``/``VM``): и серверный, и гостевой путь
строят команды отсюда, а не копипастят шелл-строки у себя.

Функции ничего не исполняют и не валидируют — только собирают строку (и, где
нужно, stdin-payload). Значения (login, группы, пути, ключ) обязан провалидировать
вызывающий транспорт по своим allow-list'ам до подстановки: server отбивает login
через ``_LOGIN_RE``/``_validate_login``, vm — через ``validate_name`` и парольный
allow-list. Билдеры на это опираются.
"""

from __future__ import annotations

SERVER = "server"
VM = "vm"


def _join_groups(groups) -> str:
    """Склеить unix-группы в CSV для ``-G``/``-aG``."""
    return ",".join(groups)


# ── useradd ──────────────────────────────────────────────────────────────────


def build_useradd(
    login: str,
    *,
    flavor: str,
    shell: str | None = None,
    home_dir: str | None = None,
    groups=None,
) -> str:
    """Собрать команду заведения пользователя.

    ``server`` — ``useradd -m [-s SHELL] [-d HOME] [-G G,..] LOGIN`` (обёртку
    ``sudo -S`` доклеивает транспорт, идемпотентность — отдельная ``getent``-проба).
    ``vm`` — идемпотентный однострочник ``bash -c 'id LOGIN >/dev/null 2>&1 ||
    useradd -m[ -G G,..] LOGIN'``; shell/home на гостевом пути не задаются.
    """
    if flavor == SERVER:
        opts = ["-m"]
        if shell is not None:
            opts += ["-s", shell]
        if home_dir is not None:
            opts += ["-d", home_dir]
        if groups:
            opts += ["-G", _join_groups(groups)]
        return f"useradd {' '.join(opts)} {login}"
    if flavor == VM:
        gopt = f" -G {_join_groups(groups)}" if groups else ""
        return f"bash -c 'id {login} >/dev/null 2>&1 || useradd -m{gopt} {login}'"
    raise ValueError(f"unknown flavor {flavor!r}")


# ── usermod ──────────────────────────────────────────────────────────────────


def build_usermod(
    login: str,
    *,
    flavor: str,
    shell: str | None = None,
    groups=None,
) -> str | None:
    """Собрать команду синхронизации атрибутов пользователя.

    ``server`` — перезапись групп через ``-G`` (без ``-a``: выпавшие группы
    снимаются) плюс опциональный ``-s SHELL``. ``vm`` — аддитивное
    ``usermod -aG G,.. LOGIN`` (гостевой пул только доклеивает членство).

    Возвращает ``None``, когда менять нечего (server без shell и групп; vm без
    групп) — вызывающая сторона тогда шаг пропускает.
    """
    if flavor == SERVER:
        opts: list[str] = []
        if shell is not None:
            opts += ["-s", shell]
        if groups:
            opts += ["-G", _join_groups(groups)]
        if not opts:
            return None
        return f"usermod {' '.join(opts)} {login}"
    if flavor == VM:
        if not groups:
            return None
        return f"usermod -aG {_join_groups(groups)} {login}"
    raise ValueError(f"unknown flavor {flavor!r}")


# ── userdel ──────────────────────────────────────────────────────────────────


def build_userdel(
    login: str, *, flavor: str, remove_home: bool = False,
) -> str:
    """Собрать команду удаления пользователя.

    ``server`` — ``userdel [--remove ]LOGIN`` (наличие юзера проверяет
    транспорт отдельной пробой). ``vm`` — идемпотентный ``bash -c 'id LOGIN
    >/dev/null 2>&1 && userdel [-r ]LOGIN || true'``.
    """
    if flavor == SERVER:
        flag = "--remove " if remove_home else ""
        return f"userdel {flag}{login}"
    if flavor == VM:
        flag = "-r " if remove_home else ""
        return f"bash -c 'id {login} >/dev/null 2>&1 && userdel {flag}{login} || true'"
    raise ValueError(f"unknown flavor {flavor!r}")


# ── set_password (chpasswd) ──────────────────────────────────────────────────


def build_set_password(
    login: str, password: str, *, flavor: str,
) -> tuple[str, str | None]:
    """Собрать смену пароля через ``chpasswd``: ``(command, stdin_payload)``.

    ``server`` — команда ``chpasswd``, payload ``login:pwd\\n`` на stdin (пароль
    в argv не попадает). ``vm`` — инлайн ``bash -c 'echo login:pwd | chpasswd'``,
    stdin не нужен (``None``).
    """
    if flavor == SERVER:
        return "chpasswd", f"{login}:{password}\n"
    if flavor == VM:
        return f"bash -c 'echo {login}:{password} | chpasswd'", None
    raise ValueError(f"unknown flavor {flavor!r}")


# ── authorized_keys ──────────────────────────────────────────────────────────


def build_authorized_keys(
    target_user: str,
    *,
    flavor: str,
    write_mode: str | None = None,
    marker: str | None = None,
    b64_key: str | None = None,
) -> str:
    """Собрать запись публичного ключа в ``~/.ssh/authorized_keys``.

    ``server`` — bash под sudo: резолвит home через ``getent passwd``, отбивает
    системные home'ы case-guard'ом, пишет ключ (он приходит на stdin как
    ``$key``), правит права/владельца. ``write_mode``:

      * ``truncate`` — файл перезаписывается одним ключом;
      * ``managed`` — прежние строки с ``marker`` вычищаются, потом идемпотентный
        append (``marker`` обязателен);
      * ``plain`` — идемпотентный append голого ключа.

    ``vm`` — инлайн-однострочник: ``umask 077``, ``mkdir``, декод base64-ключа
    (``b64_key``) в файл, ``chown``. Ключ несёт пробелы, поэтому заходит
    base64-обёрнутым, а не на stdin.
    """
    if flavor == VM:
        return (
            f"bash -c 'umask 077 && mkdir -p ~{target_user}/.ssh && "
            f"echo {b64_key} | base64 -d >> ~{target_user}/.ssh/authorized_keys && "
            f"chown -R {target_user}: ~{target_user}/.ssh'"
        )
    if flavor != SERVER:
        raise ValueError(f"unknown flavor {flavor!r}")

    if write_mode == "truncate":
        write_cmd = 'printf "%s\\n" "$key" > "$home/.ssh/authorized_keys"'
    elif write_mode == "managed":
        write_cmd = (
            'touch "$home/.ssh/authorized_keys"; '
            'tmp_ak=$(mktemp); '
            f'grep -vF " {marker}" "$home/.ssh/authorized_keys" > "$tmp_ak" || true; '
            'mv "$tmp_ak" "$home/.ssh/authorized_keys"; '
            'grep -qxF "$key" "$home/.ssh/authorized_keys" || '
            'printf "%s\\n" "$key" >> "$home/.ssh/authorized_keys"'
        )
    else:
        write_cmd = (
            'touch "$home/.ssh/authorized_keys"; '
            'grep -qxF "$key" "$home/.ssh/authorized_keys" || '
            'printf "%s\\n" "$key" >> "$home/.ssh/authorized_keys"'
        )
    return (
        f"bash -c 'set -e; "
        f"home=$(getent passwd {target_user} | cut -d: -f6); "
        'case "$home" in '
        '""|"/"|"/dev"|"/var/empty"|"/nonexistent"|"/run/sshd"|"/usr/sbin/nologin"|"/sbin/nologin"|"/bin/false") '
        f'echo "user {target_user} not found or has invalid home" >&2; '
        'exit 1;; '
        'esac; '
        'mkdir -p "$home/.ssh"; '
        "key=$(cat); "
        f"{write_cmd}; "
        f'chown -R {target_user}: "$home/.ssh"; '
        'chmod 700 "$home/.ssh"; '
        'chmod 600 "$home/.ssh/authorized_keys"\''
    )


# ── management-bootstrap: sudoers / detect / harden (server-only) ─────────────


def build_sudoers_install(sudoers_path: str) -> str:
    """Bash записи NOPASSWD-drop-in: mktemp → visudo -cf → атомарный mv, chmod 440.

    Правило приходит на stdin ``tee`` (не в argv). Битый sudoers не оставляем —
    ``visudo -cf`` валидирует временный файл до перемещения.
    """
    return (
        "bash -c 'set -e; "
        'tmp=$(mktemp); cat > "$tmp"; '
        'chmod 440 "$tmp"; '
        'visudo -cf "$tmp"; '
        f'mv "$tmp" {sudoers_path}; '
        f'chmod 440 {sudoers_path}\''
    )


def build_sudoers_line(management_user: str) -> str:
    """NOPASSWD-строка sudoers для управляющего пользователя (уходит на stdin)."""
    return f"{management_user} ALL=(ALL) NOPASSWD: ALL"


def build_account_sudoers_line(login: str) -> str:
    """NOPASSWD-строка sudoers для ОДНОГО OS-аккаунта (per-user, не per-group).

    Та же форма правила, что и `build_sudoers_line` для управляющего
    пользователя, но применяется к конкретному `server_account.login`, а не к
    группе `sudo` целиком — остальные члены группы (если такие есть) свой
    пароль на sudo продолжают вводить как обычно.
    """
    return f"{login} ALL=(ALL) NOPASSWD: ALL"


def build_detect_management_mode_probe() -> str:
    """Probe-команда детекта редакции ОС: печатает ``ASTRA=`` и ``LEVEL=``.

    Без sudo и побочных эффектов (только чтение). Разбор — в
    :func:`clients.ssh.parse_management_mode`.
    """
    return (
        "bash -c '"
        "astra=\"\"; "
        'for f in /etc/astra_version /etc/astra/build_version /etc/astra-release; do '
        'if [ -f "$f" ]; then astra=1; fi; done; '
        'if [ -z "$astra" ] && grep -qi "^ID=astra" /etc/os-release 2>/dev/null; then astra=1; fi; '
        'echo "ASTRA=$astra"; '
        "level=\"\"; "
        'if command -v astra-modeswitch >/dev/null 2>&1; then '
        'level=$(astra-modeswitch get 2>/dev/null); fi; '
        'if [ -z "$level" ] && [ -f /etc/parsec/mswitch.conf ]; then '
        'level=$(grep -iE "^[[:space:]]*mode[[:space:]]*=" /etc/parsec/mswitch.conf 2>/dev/null '
        "| head -1 | cut -d= -f2); fi; "
        'echo "LEVEL=$level"'
        "'"
    )


# Hardening drop-in: pubkey-only, без пароля и root-login. Уходит на stdin `tee`.
SSHD_HARDEN_SNIPPET = (
    "# Managed by DBOS prepare. Do not edit by hand.\n"
    "PubkeyAuthentication yes\n"
    "PasswordAuthentication no\n"
    "PermitRootLogin no\n"
)


def build_sshd_harden(dropin_path: str) -> str:
    """Bash укладки hardening drop-in в ``sshd_config.d`` + валидация ``sshd -t``.

    Раскомментирует ``Include`` если надо, кладёт snippet во временный путь,
    атомарно переносит, валидирует собранный конфиг доступным ``sshd``-бинарём и
    откатывает drop-in при провале. Reload делается отдельным шагом.
    """
    return (
        "bash -c 'set -e; "
        'mkdir -p /etc/ssh/sshd_config.d; '
        'if grep -qE "^[[:space:]]*#[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config.d/\\*\\.conf" /etc/ssh/sshd_config; then '
        'sed -i -E "s|^[[:space:]]*#[[:space:]]*(Include[[:space:]]+/etc/ssh/sshd_config.d/\\*\\.conf)|\\1|" /etc/ssh/sshd_config; '
        'fi; '
        f'tmp="{dropin_path}.tmp"; '
        'cat > "$tmp"; chmod 644 "$tmp"; '
        f'mv "$tmp" {dropin_path}; chmod 644 {dropin_path}; '
        'sshd_bin=""; '
        'for c in sshd sshd.pam /usr/sbin/sshd /usr/sbin/sshd.pam; do '
        'if command -v "$c" >/dev/null 2>&1; then sshd_bin="$c"; break; fi; done; '
        'if [ -n "$sshd_bin" ]; then '
        f'if ! "$sshd_bin" -t 2>/tmp/dbos_sshd_test_err; then rm -f {dropin_path}; '
        'cat /tmp/dbos_sshd_test_err >&2; exit 90; fi; '
        'fi\''
    )


def build_sshd_reload_commands() -> tuple[str, ...]:
    """Кортеж reload-команд sshd по убыванию распространённости init-системы."""
    return (
        "systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null",
        "service sshd reload 2>/dev/null || service ssh reload 2>/dev/null",
        "rc-service sshd reload 2>/dev/null",
        "bash -c 'pid=$(cat /run/sshd.pid 2>/dev/null || pidof sshd sshd.pam 2>/dev/null | tr \" \" \"\\n\" | head -1); "
        'if [ -n "$pid" ]; then kill -HUP "$pid"; else exit 1; fi\'',
    )


__all__ = [
    "SERVER",
    "VM",
    "build_useradd",
    "build_usermod",
    "build_userdel",
    "build_set_password",
    "build_authorized_keys",
    "build_sudoers_install",
    "build_sudoers_line",
    "build_detect_management_mode_probe",
    "build_sshd_harden",
    "build_sshd_reload_commands",
    "SSHD_HARDEN_SNIPPET",
]
