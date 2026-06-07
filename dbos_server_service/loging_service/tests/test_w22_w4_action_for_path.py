"""W22-W4 P3: `_action_for_path` — регрессия на substring-collision.

`_action_for_path` ещё в F-W17-W3 был переведён с substring (`in`) на
segment walk (`path.split('/')` + явный match на `parts[idx_after_v1]`).
Тесты в `test_w18_w3_loging_p3.py::TestActionForPathSegmentWalk` и
`test_cov_loging_w6.py` уже проверяли happy + один collision-case
(`/whatever` → fallback). Здесь — расширенный набор пар, на которых
substring-логика разъехалась бы: `/services` vs `/services_admin`,
`/services_test`, плюс вложенные пути.
"""

import pytest

from src.main import _action_for_path


class TestActionForPathSegmentCollisions:
    @pytest.mark.parametrize(
        "method,path,expected",
        [
            # Точные resource'ы — известные.
            ("GET", "/api/logging/v1/services", "logging.services_read"),
            ("GET", "/api/logging/v1/rules", "logging.rules_read"),
            ("GET", "/api/logging/v1/events", "logging.events_queried"),
            ("GET", "/api/logging/v1/retention", "logging.retention_read"),
            # Substring-collision на `/services_*` — segment walk должен
            # уйти в fallback, а не подцепиться на `services_*` как
            # на `services`.
            ("GET", "/api/logging/v1/services_admin", "logging.admin_access"),
            ("GET", "/api/logging/v1/services_test", "logging.admin_access"),
            ("GET", "/api/logging/v1/services_x_y", "logging.admin_access"),
            # Substring-collision на `/rules_*`.
            ("GET", "/api/logging/v1/rules_legacy", "logging.admin_access"),
            ("POST", "/api/logging/v1/rules_legacy", "logging.admin_access"),
            # Substring-collision на `/retention_*`.
            ("GET", "/api/logging/v1/retention_archive", "logging.admin_access"),
            ("PUT", "/api/logging/v1/retention_archive", "logging.admin_access"),
            # `/services/{svc}/events` — каталог action'ов сервиса,
            # отдельный action `logging.service_events_browsed`, чтобы
            # SOC-фильтр по `logging.events_queried` ловил только
            # чтения audit-журнала.
            ("GET", "/api/logging/v1/services/auth_service/events", "logging.service_events_browsed"),
            ("GET", "/api/logging/v1/services/server_service/events", "logging.service_events_browsed"),
            # А вот `/services/{svc}/sub` без `events`-суффикса — это
            # всё ещё services-resource.
            ("GET", "/api/logging/v1/services/auth_service", "logging.services_read"),
            # Хвостовой слэш не должен путать split.
            ("GET", "/api/logging/v1/services/", "logging.services_read"),
            ("GET", "/api/logging/v1/rules/", "logging.rules_read"),
            # POST/PATCH/DELETE на rules — write-ветка.
            ("POST", "/api/logging/v1/rules", "logging.rules_write"),
            ("PATCH", "/api/logging/v1/rules/rl_123", "logging.rules_write"),
            ("DELETE", "/api/logging/v1/rules/rl_123", "logging.rules_write"),
            # Path без `/v1/` — fallback.
            ("GET", "/api/logging/services", "logging.admin_access"),
            ("GET", "/something/else", "logging.admin_access"),
        ],
    )
    def test_no_collision(self, method, path, expected):
        assert _action_for_path(method, path) == expected

    def test_empty_path_falls_back(self):
        assert _action_for_path("GET", "/") == "logging.admin_access"
        assert _action_for_path("GET", "") == "logging.admin_access"

    def test_v1_with_no_resource_falls_back(self):
        """`/api/logging/v1` без resource'а — нет совпадения, fallback."""
        assert _action_for_path("GET", "/api/logging/v1") == "logging.admin_access"
        assert _action_for_path("GET", "/api/logging/v1/") == "logging.admin_access"
