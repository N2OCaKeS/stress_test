"""ORM-модели testing_service. Импортируются здесь, чтобы Alembic видел metadata."""

from src.models.audit_outbox import AuditOutbox
from src.models.changelog_cache import ChangelogCache
from src.models.department_activity_report import DepartmentActivityReport
from src.models.department_integration_settings import DepartmentIntegrationSettings
from src.models.department_report_member import DepartmentReportMember
from src.models.department_test_settings import DepartmentTestSettings
from src.models.entity_permission import EntityPermission
from src.models.launch_profile import LaunchProfile, LaunchProfileVersion
from src.models.provisioning_profile import ProvisioningProfile
from src.models.scenario import Scenario, ScenarioAction, ScenarioRun, ScenarioRunStand, ScenarioStand
from src.models.legacy_compat import CompatAllowedNetwork, LegacyCompatSettings
from src.models.global_variable import GlobalVariable
from src.models.queue_item import QueueItem
from src.models.queue_orchestration_event import QueueOrchestrationEvent
from src.models.run_summary_comment import RunSummaryComment
from src.models.statistics_category import StatisticsCategory
from src.models.statistics_recalc import StatisticsRecalcState
from src.models.statistics_settings import StatisticsSettings
from src.models.stp_add_test_operation import StpAddTestOperation
from src.models.stp_cell import StpCell
from src.models.stp_composition import StpComposition
from src.models.stp_matrix_publication import StpMatrixPublication
from src.models.stp_pull_operation import StpPullOperation
from src.models.stp_test_case import StpTestCase
from src.models.stp_test_run import StpTestRun
from src.models.test_command_arg import TestCommandArg
from src.models.test_definition import TestDefinition
from src.models.test_log import TestLog
from src.models.test_log_blob import TestLogBlob
from src.models.test_log_segment import TestLogSegment
from src.models.test_run import TestRun
from src.models.test_run_entry import TestRunEntry
from src.models.test_stand import TestStand
from src.models.test_step import TestStep
from src.models.zephyr_folder import ZephyrFolder
from src.models.zephyr_status_mapping import ZephyrStatusMapping

__all__ = [
    "CompatAllowedNetwork",
    "LegacyCompatSettings",
    "LaunchProfile",
    "LaunchProfileVersion",
    "ProvisioningProfile",
    "AuditOutbox",
    "ChangelogCache",
    "DepartmentActivityReport",
    "DepartmentIntegrationSettings",
    "DepartmentReportMember",
    "DepartmentTestSettings",
    "EntityPermission",
    "GlobalVariable",
    "QueueItem",
    "QueueOrchestrationEvent",
    "RunSummaryComment",
    "Scenario",
    "ScenarioAction",
    "ScenarioRun",
    "ScenarioRunStand",
    "ScenarioStand",
    "StatisticsCategory",
    "StatisticsRecalcState",
    "StatisticsSettings",
    "StpAddTestOperation",
    "StpCell",
    "StpComposition",
    "StpMatrixPublication",
    "StpPullOperation",
    "StpTestCase",
    "StpTestRun",
    "TestCommandArg",
    "TestDefinition",
    "TestLog",
    "TestLogBlob",
    "TestLogSegment",
    "TestRun",
    "TestRunEntry",
    "TestStand",
    "TestStep",
    "ZephyrFolder",
    "ZephyrStatusMapping",
]
