"""ORM-модели testing_service. Импортируются здесь, чтобы Alembic видел metadata."""

from src.models.department_test_settings import DepartmentTestSettings
from src.models.entity_permission import EntityPermission
from src.models.global_variable import GlobalVariable
from src.models.queue_item import QueueItem
from src.models.test_command_arg import TestCommandArg
from src.models.test_definition import TestDefinition
from src.models.test_log import TestLog
from src.models.test_log_blob import TestLogBlob
from src.models.test_log_segment import TestLogSegment
from src.models.test_stand import TestStand

__all__ = [
    "DepartmentTestSettings",
    "EntityPermission",
    "GlobalVariable",
    "QueueItem",
    "TestCommandArg",
    "TestDefinition",
    "TestLog",
    "TestLogBlob",
    "TestLogSegment",
    "TestStand",
]
