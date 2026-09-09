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
