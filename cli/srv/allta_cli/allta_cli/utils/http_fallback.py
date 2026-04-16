from __future__ import annotations

from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from allta_cli.utils import ui


def _replace_scheme(url: str, scheme: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme:
        return url
    updated = SplitResult(
        scheme=scheme,
        netloc=parts.netloc,
        path=parts.path,
        query=parts.query,
        fragment=parts.fragment,
    )
    return urlunsplit(updated)


def prefer_https_url(url: str) -> str:
    if urlsplit(url).scheme == "http":
        return _replace_scheme(url, "https")
    return url


def http_fallback_url(url: str) -> str | None:
    if urlsplit(url).scheme != "https":
        return None
    return _replace_scheme(url, "http")


def request_with_http_fallback(
    method: str,
    url: str,
    *,
    log: bool = False,
    **kwargs: Any,
) -> requests.Response:
    primary_url = prefer_https_url(url)
    fallback_url = http_fallback_url(primary_url)

    if log:
        ui.http(f"{method.upper()} {primary_url}")

    try:
        return requests.request(method=method, url=primary_url, **kwargs)
    except requests.RequestException as https_error:
        if not fallback_url:
            raise

        ui.warn(f"HTTPS недоступен для {primary_url}, пробую HTTP.")
        if log:
            ui.http(f"{method.upper()} {fallback_url}")

        try:
            return requests.request(method=method, url=fallback_url, **kwargs)
        except requests.RequestException as http_error:
            raise requests.RequestException(
                f"HTTPS failed for {primary_url}: {https_error}; "
                f"HTTP failed for {fallback_url}: {http_error}"
            ) from http_error
