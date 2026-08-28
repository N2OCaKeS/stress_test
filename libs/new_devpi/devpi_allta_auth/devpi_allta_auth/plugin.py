import base64
import json
import os
import ssl
import urllib.error
import urllib.request

from devpi_server.config import hookimpl
from devpi_server.log import threadlog


UPLOAD_GROUP = "devpi_upload"


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def _api_base_url():
    return os.getenv("DEVPI_AUTH_API_URL", "https://allta.devos.astralinux.ru:21500").rstrip("/")


def _authorize_url():
    path = os.getenv(
        "DEVPI_AUTH_API_AUTHORIZE_PATH",
        "/api/auth/v1/integrations/devpi/authorize",
    )
    return f"{_api_base_url()}{path}?action=read"


def _ssl_context():
    if _env_bool("DEVPI_AUTH_API_VERIFY_TLS", False):
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _auth_header(username, password):
    password = password or ""
    if password.lower().startswith("bearer "):
        return password
    raw = f"{username}:{password}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"Basic {encoded}"


def _request_identity(username, password):
    request = urllib.request.Request(_authorize_url(), method="GET")
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", _auth_header(username, password))

    try:
        with urllib.request.urlopen(request, timeout=15, context=_ssl_context()) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return None
        threadlog.error("Allta Auth API returned HTTP %s for user %r", exc.code, username)
        return None
    except urllib.error.URLError as exc:
        threadlog.error("Allta Auth API is unavailable for user %r: %s", username, exc)
        return None

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        threadlog.error("Allta Auth API returned invalid JSON for user %r", username)
        return None

    if not isinstance(data, dict):
        return None
    if data.get("login") != username:
        threadlog.warning(
            "Allta Auth API login mismatch: requested %r, got %r",
            username,
            data.get("login"),
        )
        return None
    return data


def _groups_from_identity(identity):
    groups = _split_csv(os.getenv("DEVPI_AUTH_DEFAULT_GROUPS", ""))
    capabilities = identity.get("capabilities") or {}
    if capabilities.get("devpi_write"):
        groups.append(UPLOAD_GROUP)
    role = identity.get("role")
    if role:
        groups.append(f"role_{role}")
    for permission in identity.get("permissions") or []:
        normalized = str(permission).replace(".", "_").replace("-", "_")
        if normalized:
            groups.append(f"perm_{normalized}")
    return sorted(set(groups))


def _stage_bases():
    return tuple(_split_csv(os.getenv("DEVPI_AUTH_USER_INDEX_BASES", "root/test")))


def _stage_acl_upload(username):
    value = os.getenv("DEVPI_AUTH_USER_INDEX_ACL_UPLOAD", "{username}")
    return tuple(item.format(username=username) for item in _split_csv(value))


def _ensure_user_and_index(request, username):
    model = request.registry["xom"].model
    user = model.get_user(username)
    if user is None:
        user = model.create_user(username, password=None)

    if not _env_bool("DEVPI_AUTH_CREATE_USER_INDEX", True):
        return

    index = os.getenv("DEVPI_AUTH_USER_INDEX", os.getenv("DEVPI_DEV_INDEX", "dev"))
    if user.getstage(index) is not None:
        return

    user.create_stage(
        index,
        bases=_stage_bases(),
        volatile=_env_bool("DEVPI_AUTH_USER_INDEX_VOLATILE", True),
        acl_upload=_stage_acl_upload(username),
        mirror_whitelist=(),
    )
    threadlog.info("created Allta Auth devpi index %s/%s", username, index)


@hookimpl(trylast=True)
def devpiserver_auth_request(request, userdict, username, password):
    if username == "root":
        return None
    identity = _request_identity(username, password)
    if identity is None:
        return None
    _ensure_user_and_index(request, username)
    return {"status": "ok", "groups": _groups_from_identity(identity)}
