"""Ядра generic/lowlatency из индексов пакетов репозиториев ОС."""
import asyncio
import re
import shlex
import zlib
from urllib.parse import urlparse

import httpx
from src.core.exceptions import BadRequestError, ServiceUnavailableError

MAX_INDEX_BYTES = 64 * 1024 * 1024
_KERNEL = re.compile(r"^Package:\s+linux-image-(\d+\.\d+\.\d+-\d+-(?:generic|lowlatency))\s*$", re.MULTILINE | re.IGNORECASE)


def package_indexes(repositories):
    indexes = []
    for line in repositories:
        words = shlex.split(line)
        if not words or words[0] == "deb-src":
            continue
        if words[0] == "deb":
            words = words[1:]
            if words and words[0].startswith("["):
                while words and not words.pop(0).endswith("]"):
                    pass
            if len(words) < 2:
                raise BadRequestError(error_code="OS_REPOSITORY_INVALID", message="Некорректная строка репозитория ОС")
            base, suite, *components = words
            if suite.endswith("/"):
                indexes.append(f"{base.rstrip('/')}/{suite.strip('/')}/Packages")
            else:
                indexes.extend(f"{base.rstrip('/')}/dists/{suite}/{component}/binary-amd64/Packages" for component in components)
        else:
            base = words[0].rstrip("/")
            indexes.append(base if base.endswith("Packages") else f"{base}/Packages")
    if any(urlparse(url).scheme not in ("http", "https") for url in indexes) or not indexes:
        raise BadRequestError(error_code="OS_REPOSITORIES_REQUIRED", message="Для поиска ядер нужны HTTP-репозитории выбранной ОС")
    return list(dict.fromkeys(indexes))


def parse_kernels(text):
    return sorted(set(_KERNEL.findall(text)), key=lambda value: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)])


async def fetch_index(client, url):
    for suffix in (".gz", ""):
        async with client.stream("GET", url + suffix) as response:
            if response.status_code == 404:
                continue
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_INDEX_BYTES:
                    raise ValueError("Package index too large")
            data = bytes(chunks)
            if suffix:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = decoder.decompress(data, MAX_INDEX_BYTES + 1)
                if len(data) > MAX_INDEX_BYTES or not decoder.eof:
                    raise ValueError("Invalid or oversized compressed index")
            return data.decode("utf-8", errors="replace")
    raise ValueError("Packages index not found")


async def resolve_kernels(repositories):
    kernels = set()
    try:
        async with asyncio.timeout(90), httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            semaphore = asyncio.Semaphore(3)
            async def read(url):
                async with semaphore:
                    return parse_kernels(await fetch_index(client, url))
            for found in await asyncio.gather(*(read(url) for url in package_indexes(repositories))):
                kernels.update(found)
    except (httpx.HTTPError, ValueError, zlib.error, TimeoutError) as exc:
        raise ServiceUnavailableError(error_code="OS_KERNEL_INDEX_UNAVAILABLE", message="Не удалось прочитать индексы пакетов ОС; сохранённые ядра не изменены") from exc
    if not kernels:
        raise BadRequestError(error_code="OS_KERNELS_NOT_FOUND", message="В репозиториях ОС не найдены ядра generic/lowlatency")
    return parse_kernels("\n".join(f"Package: linux-image-{kernel}" for kernel in kernels))
