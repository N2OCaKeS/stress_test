from pathlib import Path
import os
import shlex

API_BASE_URL = str("https://allta.devos.astralinux.ru").rstrip("/")
VM_API_BASE = f"{API_BASE_URL}:21501/api/vm/v1".rstrip("/")
SERVER_API_BASE = f"{API_BASE_URL}:21501/api/server/v1".rstrip("/")

SESSION_FILE =  Path(Path.home() / ".config" / "allta" / "session.json")
TOKEN_TTL_HOURS_DEFAULT = 10 * 60 * 60

CONFIG_API_BASE = "https://allta.devos.astralinux.ru:21500/api/config/v1".rstrip("/")

GIT_REPO_URL = "https://git.astralinux.ru/scm/qa/stress_test.git"
GIT_DEST_DIR = Path(os.path.expanduser("~/git"))

PYTHON_PATH = Path(os.path.expanduser("/home/u/python"))
PYTHON_GET_COMMAND = "wget -P /home/u/python ftp://10.177.103.10/python/*"

def git_clone_command(token: str, dest: Path | str | None = None) -> str:
    """
    Сформировать команду клонирования в указанный dest.
    Если dest не передан — используем GIT_DEST_DIR / 'stress_test'.
    """
    # конечная папка для клона
    final_dest = Path(dest).expanduser() if dest else (GIT_DEST_DIR.expanduser() / "stress_test")

    cmd = (
        f"git -c http.extraHeader='Authorization: {token}' clone {GIT_REPO_URL} {shlex.quote(str(final_dest))}"
    )
    return cmd
