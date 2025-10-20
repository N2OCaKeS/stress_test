from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from allta import SystemCommands
from allta_cli.utils.config import git_clone_command, GIT_DEST_DIR
from allta_cli.utils.config_api import tokens as fetch_tokens, ConfigApiError, TokenKeyNotFound
from allta_cli.utils.auth import AuthError, TokenExpiredError, NotAuthenticatedError

SEP = "─" * 60

def _mask_token(s: str, left: int = 4, right: int = 3) -> str:
    if not s:
        return ""
    return "*" * len(s) if len(s) <= left + right else f"{s[:left]}…{s[-right:]}"

def _as_path(p) -> Path:
    if isinstance(p, Path):
        return p.expanduser()
    return Path(str(p)).expanduser()

def get_tokens() -> str:
    try:
        git_token = fetch_tokens("git_token")
    except TokenKeyNotFound as e:
        raise RuntimeError("В tokens.json отсутствует ключ 'git_token'.") from e
    except (NotAuthenticatedError, TokenExpiredError) as e:
        raise RuntimeError("Локальная сессия недействительна. Выполните вход: allta login ...") from e
    except (AuthError, ConfigApiError) as e:
        raise RuntimeError(f"Не удалось получить git-токен: {e}") from e
    if not isinstance(git_token, str) or not git_token.strip():
        raise RuntimeError("git_token пустой или имеет неверный формат.")
    return git_token.strip()

def _ensure_root_dir(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o770)
    except Exception:
        pass  # не критично

def _prepare_dest(root: Path) -> Path:
    dest = root / "stress_test"
    if dest.exists():
        # удаляем полностью перед клоном
        shutil.rmtree(dest, ignore_errors=False)
    return dest

def _build_command(token: str, dest: Path) -> str:
    # если git_clone_command имеет сигнатуру (token, dest) — используем
    try:
        cmd = git_clone_command(token, str(dest))
    except TypeError:
        # значит принимает только (token) — допишем dest в конец
        cmd = git_clone_command(token)
        if str(dest) not in cmd:
            cmd = f"{cmd.strip()} {shlex.quote(str(dest))}"
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("git_clone_command() вернула пустую строку.")
    return cmd

# NEW: выбор базового каталога (root/sudo -> /home/u/git)
def _choose_root_base() -> Path:
    is_root = False
    try:
        is_root = (os.geteuid() == 0)
    except AttributeError:
        # Windows или нестандартная платформа — fallback по переменным окружения sudo
        pass
    if is_root or os.environ.get("SUDO_USER") or os.environ.get("SUDO_UID"):
        return Path("/home/u/git")
    return _as_path(GIT_DEST_DIR)

def git_clone() -> int:
    print(SEP)
    print("GIT CLONE (start)")
    print(SEP)

    # 0) пути
    try:
        root = _choose_root_base()
        _ensure_root_dir(root)
        dest = _prepare_dest(root)
    except Exception as e:
        print(f"Ошибка подготовки каталога: {e}")
        print(SEP); print("GIT CLONE (end)"); print(SEP)
        return 1

    # 1) токен
    try:
        token = get_tokens()
    except Exception as e:
        print(f"Ошибка получения git-токена: {e}")
        print(SEP); print("GIT CLONE (end)"); print(SEP)
        return 1

    # 2) команда
    try:
        command = _build_command(token, dest)
    except Exception as e:
        print(f"Ошибка формирования команды клонирования: {e}")
        print(SEP); print("GIT CLONE (end)"); print(SEP)
        return 1

    masked_cmd = command.replace(token, _mask_token(token))
    print(f"Команда: {masked_cmd}")

    try:
        rc = SystemCommands.cmd_with_returncode(command=command)
    except Exception as e:
        print(f"Ошибка запуска команды: {e}")
        print(SEP); print("GIT CLONE (end)"); print(SEP)
        return 1

    if rc == 0:
        try:
            rc2 = SystemCommands.cmd_with_returncode(
                command=f'git -C {shlex.quote(str(dest))} config http.extraHeader "Authorization: {token}"'
            )
        except Exception as e:
            print(f"Ошибка запуска команды: {e}")
            print(SEP); print("GIT config (end)"); print(SEP)
            return 1

    if rc == 0:
        try:
            os.chmod(dest, 0o770)
        except Exception:
            pass
        print("✓ Клонирование успешно.")
        print(f"Папка: {dest}")
        print(SEP); print("GIT CLONE (end)"); print(SEP)
        return 0

    # 4) ошибка — без повторов, сразу сообщаем
    print(f"✗ Не удалось клонировать репозиторий. Код возврата: {rc}")
    print("Подсказки:")
    print("  • Проверьте корректность git_token (allta tokens git_token).")
    print("  • Убедитесь, что у токена есть права на репозиторий.")
    print("  • Проверьте сеть/доступ к git-серверу.")
    print(f"Каталог назначения: {dest}")
    print(SEP); print("GIT CLONE (end)"); print(SEP)
    return rc or 1
