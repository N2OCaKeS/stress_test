"""Генерация prefixed ID. По одной фабрике на каждый prefix."""

import uuid


def _new_id(prefix: str) -> str:
    """`prefix + uuid4.hex` — единственная точка генерации, единый формат."""
    return f"{prefix}{uuid.uuid4().hex}"


def global_variable_id() -> str:
    """`gvar_<uuid>` — для global_variables."""
    return _new_id("gvar_")


def entity_permission_id() -> str:
    """`prm_<uuid>` — для entity_permissions."""
    return _new_id("prm_")


def test_definition_id() -> str:
    """`tdef_<uuid>` — для test_definitions."""
    return _new_id("tdef_")


def test_command_arg_id() -> str:
    """`targ_<uuid>` — для test_command_args."""
    return _new_id("targ_")


def test_stand_id() -> str:
    """`stand_<uuid>` — для test_stands."""
    return _new_id("stand_")


def department_test_settings_id() -> str:
    """`dts_<uuid>` — для department_test_settings."""
    return _new_id("dts_")


def queue_item_id() -> str:
    """`qi_<uuid>` — для queue_items. Используется буквально как `correlation_id`
    в вызове `prepare-for-test`, без дополнительной обёртки."""
    return _new_id("qi_")


def test_log_id() -> str:
    """`tlog_<uuid>` — для test_logs."""
    return _new_id("tlog_")


def test_log_segment_id() -> str:
    """`tseg_<uuid>` — для test_log_segments."""
    return _new_id("tseg_")


def test_run_id() -> str:
    """`run_<uuid>` — для test_runs."""
    return _new_id("run_")


def stp_test_case_id() -> str:
    """`stpc_<uuid>` — для stp_test_cases."""
    return _new_id("stpc_")


def stp_test_run_id() -> str:
    """`stpr_<uuid>` — для stp_test_runs (Zephyr test-run/execution)."""
    return _new_id("stpr_")


def stp_cell_id() -> str:
    """`cell_<uuid>` — для stp_cells."""
    return _new_id("cell_")


def department_integration_settings_id() -> str:
    """`dis_<uuid>` — для department_integration_settings."""
    return _new_id("dis_")
