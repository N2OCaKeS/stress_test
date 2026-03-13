#!/usr/bin/env python3
"""Simple requests-based client for Allta APIs.

Features:
- list tokens from config API (/config/tokens)
- get snapshot password(s) from server API (/passwords)
- get credential for a specific service from config API (/config/credentials/{service})

Auth token is read from environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_CONFIG_API_BASE = "http://allta.devos.astralinux.ru:21500/api/config/v1"
DEFAULT_SERVER_API_BASE = "http://allta.devos.astralinux.ru:21501/api/server/v1"
TOKEN_ENV_CANDIDATES = ("ALLTA_AUTH_TOKEN", "ALLTA_API_TOKEN", "ALLTA_TOKEN")


def _load_auth_token() -> str:
    for name in TOKEN_ENV_CANDIDATES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    names = ", ".join(TOKEN_ENV_CANDIDATES)
    raise RuntimeError(f"Auth token is missing. Set one of: {names}")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: int = 30,
) -> Any:
    response = requests.request(method=method, url=url, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        detail = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
        except ValueError:
            payload = None
        msg = str(detail).strip() if isinstance(detail, str) and detail.strip() else response.text.strip()
        raise RuntimeError(f"HTTP {response.status_code} for {url}: {msg}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON response for {url}: {response.text}") from exc


def list_tokens(config_api_base: str, headers: dict[str, str]) -> dict[str, str]:
    url = f"{config_api_base.rstrip('/')}/config/tokens"
    data = _request_json("GET", url, headers=headers)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected tokens payload: expected JSON object")
    return {str(k): str(v) for k, v in data.items()}


def list_snapshot_passwords(server_api_base: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{server_api_base.rstrip('/')}/passwords/"
    data = _request_json("GET", url, headers=headers)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected snapshot passwords payload: expected JSON list")
    return [x for x in data if isinstance(x, dict)]


def get_snapshot_password(
    server_api_base: str,
    headers: dict[str, str],
    os_version_name: str,
) -> dict[str, Any]:
    name = os_version_name.strip()
    if not name:
        raise RuntimeError("os_version_name is empty")
    url = f"{server_api_base.rstrip('/')}/passwords/{quote(name, safe='')}"
    data = _request_json("GET", url, headers=headers)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected snapshot password payload: expected JSON object")
    return data


def get_service_credential(
    config_api_base: str,
    headers: dict[str, str],
    service_name: str,
) -> dict[str, Any]:
    name = service_name.strip()
    if not name:
        raise RuntimeError("service_name is empty")
    url = f"{config_api_base.rstrip('/')}/config/credentials/{quote(name, safe='')}"
    data = _request_json("GET", url, headers=headers)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected service credential payload: expected JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple requests client for Allta APIs")
    parser.add_argument(
        "--config-api-base",
        default=os.getenv("ALLTA_CONFIG_API_BASE", DEFAULT_CONFIG_API_BASE),
        help="Config API base URL",
    )
    parser.add_argument(
        "--server-api-base",
        default=os.getenv("ALLTA_SERVER_API_BASE", DEFAULT_SERVER_API_BASE),
        help="Server API base URL",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("tokens", help="List tokens from /config/tokens")

    snapshot = subparsers.add_parser("snapshot", help="Get snapshot password(s)")
    snapshot.add_argument("--os-version", default=None, help="OS version name. If omitted, list all.")
    snapshot.add_argument(
        "--only-password",
        action="store_true",
        help="Print only password value (works with --os-version)",
    )

    service = subparsers.add_parser("service", help="Get credentials for a specific service")
    service.add_argument("--name", required=True, help="Service name")
    service.add_argument(
        "--only-password",
        action="store_true",
        help="Print only password value",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        token = _load_auth_token()
        headers = _headers(token)

        if args.command == "tokens":
            payload = list_tokens(args.config_api_base, headers)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "snapshot":
            if args.os_version:
                payload = get_snapshot_password(args.server_api_base, headers, args.os_version)
                if args.only_password:
                    print(str(payload.get("password", "")))
                else:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0

            payload = list_snapshot_passwords(args.server_api_base, headers)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "service":
            payload = get_service_credential(args.config_api_base, headers, args.name)
            if args.only_password:
                print(str(payload.get("password", "")))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        parser.print_help()
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
