"""Unit-тесты `auth_service._merge_permissions`.

Чистая функция — нет БД. Покрытие:
* объединение `dept_services + group_services` без дублей;
* объединение `direct_roles + group_roles` пер-сервис с дедупом ролей;
* пустые входы (нет services / нет ролей);
* **cross-dept privilege retention guard**: роли в
  `direct_roles`/`group_roles` без соответствующего сервиса в
  `dept_services ∪ group_services` фильтруются (INTERSECT). Раньше
  баг — стирали при `_build_identity` ниже по стеку (если вообще
  стирали), теперь — на уровне merge-функции, чтобы её callers получали
  безопасный результат сразу;
* большие коллекции (производительность не тестируем, только корректность).
"""

from src.services.auth_service import _merge_permissions


class TestServicesMerge:
    def test_union_without_duplicates(self):
        services, _ = _merge_permissions(
            dept_services=["a", "b"],
            direct_roles={},
            group_services=["b", "c"],
            group_roles={},
        )
        assert sorted(services) == ["a", "b", "c"]

    def test_empty_inputs_return_empty(self):
        services, roles = _merge_permissions([], {}, [], {})
        assert services == []
        assert roles == {}

    def test_only_dept_services(self):
        services, roles = _merge_permissions(["s1"], {}, [], {})
        assert services == ["s1"]
        assert roles == {}

    def test_only_group_services(self):
        services, roles = _merge_permissions([], {}, ["s2"], {})
        assert services == ["s2"]


class TestRolesMerge:
    def test_direct_roles_kept(self):
        _, roles = _merge_permissions(
            dept_services=["s"],
            direct_roles={"s": ["reader"]},
            group_services=[],
            group_roles={},
        )
        assert roles == {"s": ["reader"]}

    def test_direct_and_group_roles_merged_deduplicated(self):
        _, roles = _merge_permissions(
            dept_services=["s"],
            direct_roles={"s": ["reader", "operator"]},
            group_services=["s"],
            group_roles={"s": ["operator", "guest"]},
        )
        assert sorted(roles["s"]) == ["guest", "operator", "reader"]

    def test_disjoint_services(self):
        _, roles = _merge_permissions(
            dept_services=["a"],
            direct_roles={"a": ["reader"]},
            group_services=["b"],
            group_roles={"b": ["operator"]},
        )
        assert roles == {"a": ["reader"], "b": ["operator"]}

    def test_group_roles_dropped_without_service_grant(self):
        """Cross-dept retention guard: role for service `b` без `b` в
        `dept_services ∪ group_services` отсеивается. Раньше эта
        утечка позволяла переведённому пользователю сохранять admin
        в сервисе, к которому его новый отдел потерял access."""
        services, roles = _merge_permissions(
            dept_services=["a"],
            direct_roles={},
            group_services=[],
            group_roles={"b": ["reader"]},
        )
        assert services == ["a"]
        # `b` отфильтрован: ни dept, ни группа его не разрешают.
        assert roles == {}

    def test_direct_roles_dropped_when_service_revoked(self):
        """Симметрия: direct_roles тоже фильтруются. Это и есть основной
        cross-dept transfer scenario — `user_service_roles` row для
        `config_service` пережил transfer, но dept_services его не
        содержит → роль не пробрасывается в effective."""
        services, roles = _merge_permissions(
            dept_services=["a"],
            direct_roles={"config_service": ["admin"], "a": ["reader"]},
            group_services=[],
            group_roles={},
        )
        assert services == ["a"]
        assert roles == {"a": ["reader"]}
        assert "config_service" not in roles

    def test_role_kept_when_only_group_grants_service(self):
        """Сервис не у отдела, но у группы — роль сохраняется. Это легитимный
        cross-dept-grant через групповое членство (group_service_access
        даёт доступ независимо от отдела)."""
        _, roles = _merge_permissions(
            dept_services=[],
            direct_roles={"shared_svc": ["operator"]},
            group_services=["shared_svc"],
            group_roles={},
        )
        assert roles == {"shared_svc": ["operator"]}

    def test_duplicate_roles_within_one_source(self):
        _, roles = _merge_permissions(
            dept_services=["s"],
            direct_roles={"s": ["reader", "reader", "operator", "reader"]},
            group_services=[],
            group_roles={},
        )
        assert sorted(roles["s"]) == ["operator", "reader"]


class TestImmutability:
    def test_does_not_mutate_inputs(self):
        direct = {"s": ["reader"]}
        group = {"s": ["operator"]}
        services_dept = ["s"]
        services_grp = ["s"]
        _merge_permissions(services_dept, direct, services_grp, group)
        # Исходные не должны быть тронуты — иначе повторный вызов сломается.
        assert direct == {"s": ["reader"]}
        assert group == {"s": ["operator"]}
        assert services_dept == ["s"]
        assert services_grp == ["s"]
