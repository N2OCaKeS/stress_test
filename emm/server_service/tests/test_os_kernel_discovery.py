"""Обнаружение ядер и атомарное обновление каталога ОС."""
import gzip
import uuid
import httpx
import pytest
from src.services import os_kernel_resolver as resolver
from src.core.exceptions import ServiceUnavailableError
from tests._helpers import auth_hdr

BASE = "/api/server/v1/os-versions"


def test_packages_parser_and_sources_list():
    text = "\n".join([
        "Package: linux-image-6.1.9-1-generic", "Package: linux-image-6.1.10-1-lowlatency",
        "Package: linux-image-6.1.9-1-generic", "Package: linux-image-generic",
        "Package: linux-headers-6.1.9-1-generic", "Package: linux-image-6.1.9-1-generic-dbgsym",
    ])
    assert resolver.parse_kernels(text) == ["6.1.9-1-generic", "6.1.10-1-lowlatency"]
    assert resolver.package_indexes(["deb [arch=amd64] https://repo.example/os 1.7_x86-64 main contrib"]) == [
        "https://repo.example/os/dists/1.7_x86-64/main/binary-amd64/Packages",
        "https://repo.example/os/dists/1.7_x86-64/contrib/binary-amd64/Packages",
    ]


@pytest.mark.parametrize("compressed", [True, False])
async def test_fetch_compressed_or_plain_index(compressed):
    text = "Package: linux-image-6.1.9-1-generic\n"
    def handler(request):
        if request.url.path.endswith(".gz"):
            return httpx.Response(200, content=gzip.compress(text.encode())) if compressed else httpx.Response(404)
        return httpx.Response(200, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await resolver.fetch_index(client, "https://repo.example/Packages") == text


async def test_discovery_saves_kernels_and_preserves_them_on_network_failure(client, admin_role_token_a, monkeypatch):
    response = await client.post(BASE, headers=auth_hdr(admin_role_token_a), json={"name": f"kernel-{uuid.uuid4().hex}", "repositories": ["https://repo.example/os"]})
    assert response.status_code == 201, response.text
    id_ = response.json()["id"]
    async def discover(repositories):
        assert repositories == ["https://repo.example/os"]
        return ["6.1.9-1-generic", "6.1.10-1-lowlatency"]
    monkeypatch.setattr(resolver, "resolve_kernels", discover)
    response = await client.post(f"{BASE}/{id_}/resolve-kernels", headers=auth_hdr(admin_role_token_a))
    assert response.status_code == 200, response.text
    expected = response.json()["kernels"]
    assert len(expected) == 2
    async def fail(repositories):
        raise ServiceUnavailableError(error_code="OS_KERNEL_INDEX_UNAVAILABLE", message="offline")
    monkeypatch.setattr(resolver, "resolve_kernels", fail)
    response = await client.post(f"{BASE}/{id_}/resolve-kernels", headers=auth_hdr(admin_role_token_a))
    assert response.status_code == 503
    response = await client.get(f"{BASE}/{id_}", headers=auth_hdr(admin_role_token_a))
    assert response.json()["kernels"] == expected
    assert (await client.post(f"{BASE}/{id_}/resolve-kernels")).status_code == 401
