"""ORM-модели testing_service. Импортируются здесь, чтобы Alembic видел metadata."""

from src.models.entity_permission import EntityPermission
from src.models.global_variable import GlobalVariable
from src.models.test_command_arg import TestCommandArg
from src.models.test_definition import TestDefinition
from src.models.test_stand import TestStand

__all__ = [
    "EntityPermission",
    "GlobalVariable",
    "TestCommandArg",
    "TestDefinition",
    "TestStand",
]
