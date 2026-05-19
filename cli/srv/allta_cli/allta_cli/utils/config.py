from pathlib import Path
import os
import shlex

API_BASE_URL = str("https://allta.devos.astralinux.ru").rstrip("/")
AUTH_API_BASE = f"{API_BASE_URL}:21500/api/auth".rstrip("/")
VM_API_BASE = f"{API_BASE_URL}:21501/api/vm/v1".rstrip("/")
SERVER_API_BASE = f"{API_BASE_URL}:21501/api/server/v1".rstrip("/")

SESSION_FILE =  Path(Path.home() / ".config" / "allta" / "session.json")
TOKEN_TTL_HOURS_DEFAULT = 10 * 60 * 60

CONFIG_API_BASE = "https://allta.devos.astralinux.ru:21500/api/config/v1".rstrip("/")

JIRA_BASE_URL = os.environ.get("ALLTA_JIRA_BASE_URL") or os.environ.get("JIRA_BASE_URL") or "https://jira.astralinux.ru"
JIRA_BASE_URL = JIRA_BASE_URL.rstrip("/")
JIRA_PROJECT_KEY = os.environ.get("ALLTA_JIRA_PROJECT_KEY") or os.environ.get("JIRA_PROJECT_KEY") or "DEVQA"
JIRA_ISSUE_TYPE_ID = os.environ.get("ALLTA_JIRA_ISSUE_TYPE_ID") or os.environ.get("JIRA_ISSUE_TYPE_ID") or "10400"
JIRA_ISSUE_TYPE_NAME = os.environ.get("ALLTA_JIRA_ISSUE_TYPE_NAME") or os.environ.get("JIRA_ISSUE_TYPE_NAME") or "Задача"
JIRA_EPIC_LINK_FIELD = os.environ.get("ALLTA_JIRA_EPIC_LINK_FIELD") or os.environ.get("JIRA_EPIC_LINK_FIELD") or "customfield_10300"
JIRA_STORY_POINTS_FIELD = os.environ.get("ALLTA_JIRA_STORY_POINTS_FIELD") or os.environ.get("JIRA_STORY_POINTS_FIELD") or "customfield_21823"
JIRA_AUTH_MODE = (os.environ.get("ALLTA_JIRA_AUTH_MODE") or os.environ.get("JIRA_AUTH_MODE") or "bearer").strip().lower()
JIRA_BOARD_ID = os.environ.get("ALLTA_JIRA_BOARD_ID") or os.environ.get("JIRA_BOARD_ID") or "340"
JIRA_PRIORITY_ID = os.environ.get("ALLTA_JIRA_PRIORITY_ID") or os.environ.get("JIRA_PRIORITY_ID") or "2"

_JIRA_SERVICE_USERS_RAW = os.environ.get("ALLTA_JIRA_SERVICE_USERS") or "allta,lib"
JIRA_SERVICE_USERS = frozenset(s.strip() for s in _JIRA_SERVICE_USERS_RAW.split(",") if s.strip())

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
