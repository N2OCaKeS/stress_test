from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path

ALLTA_API_HOST = os.getenv("ALLTA_API_HOST", "allta.devos.astralinux.ru")
ALLTA_API_PORT = os.getenv("ALLTA_API_PORT", "21500")
ALLTA_SHARED_CERT_SOURCE = Path(
    os.getenv("ALLTA_SHARED_CERT_SOURCE", "/var/allta_services/certs/allta-api.crt")
)
ALLTA_SYSTEM_CA_TARGET = Path(
    os.getenv("ALLTA_SYSTEM_CA_TARGET", "/usr/local/share/ca-certificates/allta-api.crt")
)
ALLTA_USER_CERT_DIR = Path(
    os.getenv("ALLTA_USER_CERT_DIR", str(Path.home() / ".config" / "allta" / "certs"))
)
ALLTA_USER_CERT_FILE = ALLTA_USER_CERT_DIR / "allta-api.crt"
ALLTA_USER_CA_BUNDLE = ALLTA_USER_CERT_DIR / "ca-bundle.pem"
SYSTEM_CA_BUNDLE = Path(
    ssl.get_default_verify_paths().cafile or "/etc/ssl/certs/ca-certificates.crt"
)


def _resolve_update_ca_cmd() -> str | None:
    candidate = shutil.which("update-ca-certificates")
    if candidate:
        return candidate

    for path in ("/usr/sbin/update-ca-certificates", "/usr/bin/update-ca-certificates"):
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path

    return None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def _is_valid_cert(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if not shutil.which("openssl"):
        return False
    return _run(["openssl", "x509", "-in", str(path), "-noout"]).returncode == 0


def _copy_if_changed(src: Path, dst: Path) -> bool:
    if dst.is_file():
        try:
            if src.read_bytes() == dst.read_bytes():
                return False
        except OSError:
            pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _fetch_cert_from_api() -> Path | None:
    if not shutil.which("openssl"):
        return None

    tmp = tempfile.NamedTemporaryFile(prefix="allta-api-cert-", suffix=".crt", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        cmd = [
            "openssl",
            "s_client",
            "-servername",
            ALLTA_API_HOST,
            "-connect",
            f"{ALLTA_API_HOST}:{ALLTA_API_PORT}",
        ]
        if shutil.which("timeout"):
            cmd = ["timeout", "8", *cmd]

        proc = subprocess.run(
            cmd,
            input="",
            text=True,
            capture_output=True,
            check=False,
        )
        out = proc.stdout or ""
        begin = "-----BEGIN CERTIFICATE-----"
        end = "-----END CERTIFICATE-----"
        start_idx = out.find(begin)
        end_idx = out.find(end, start_idx if start_idx >= 0 else 0)
        if start_idx >= 0 and end_idx >= 0:
            cert_text = out[start_idx : end_idx + len(end)] + "\n"
            tmp_path.write_text(cert_text, encoding="utf-8")

        if _is_valid_cert(tmp_path):
            return tmp_path
        return None
    except OSError:
        return None


def _build_user_ca_bundle(cert_path: Path) -> None:
    ALLTA_USER_CERT_DIR.mkdir(parents=True, exist_ok=True)
    _copy_if_changed(cert_path, ALLTA_USER_CERT_FILE)

    bundle_parts: list[bytes] = []
    if SYSTEM_CA_BUNDLE.is_file():
        try:
            bundle_parts.append(SYSTEM_CA_BUNDLE.read_bytes())
        except OSError:
            pass
    try:
        cert_bytes = ALLTA_USER_CERT_FILE.read_bytes()
    except OSError:
        cert_bytes = b""
    if cert_bytes:
        if bundle_parts and not bundle_parts[-1].endswith(b"\n"):
            bundle_parts[-1] = bundle_parts[-1] + b"\n"
        bundle_parts.append(cert_bytes)

    if not bundle_parts:
        return

    new_bundle = b"".join(bundle_parts)
    if ALLTA_USER_CA_BUNDLE.is_file():
        try:
            if ALLTA_USER_CA_BUNDLE.read_bytes() == new_bundle:
                os.environ["REQUESTS_CA_BUNDLE"] = str(ALLTA_USER_CA_BUNDLE)
                os.environ["SSL_CERT_FILE"] = str(ALLTA_USER_CA_BUNDLE)
                return
        except OSError:
            pass

    ALLTA_USER_CA_BUNDLE.write_bytes(new_bundle)
    os.environ["REQUESTS_CA_BUNDLE"] = str(ALLTA_USER_CA_BUNDLE)
    os.environ["SSL_CERT_FILE"] = str(ALLTA_USER_CA_BUNDLE)


def _try_update_system_trust(cert_path: Path) -> None:
    update_ca_cmd = _resolve_update_ca_cmd()
    if not update_ca_cmd:
        return

    if os.geteuid() == 0:
        _copy_if_changed(cert_path, ALLTA_SYSTEM_CA_TARGET)
        _run([update_ca_cmd])
        return

    if not shutil.which("sudo"):
        return

    _run(
        [
            "sudo",
            "-n",
            "install",
            "-m",
            "0644",
            str(cert_path),
            str(ALLTA_SYSTEM_CA_TARGET),
        ]
    )
    _run(["sudo", "-n", update_ca_cmd])


def ensure_api_tls_trust() -> None:
    source_cert: Path | None = None
    tmp_cert: Path | None = None

    try:
        tmp_cert = _fetch_cert_from_api()
        if tmp_cert is not None:
            source_cert = tmp_cert
        elif _is_valid_cert(ALLTA_SHARED_CERT_SOURCE):
            source_cert = ALLTA_SHARED_CERT_SOURCE
        elif _is_valid_cert(ALLTA_USER_CERT_FILE):
            source_cert = ALLTA_USER_CERT_FILE

        if source_cert is None:
            return

        _build_user_ca_bundle(source_cert)
        _try_update_system_trust(source_cert)
    finally:
        if tmp_cert is not None:
            try:
                tmp_cert.unlink(missing_ok=True)
            except OSError:
                pass
