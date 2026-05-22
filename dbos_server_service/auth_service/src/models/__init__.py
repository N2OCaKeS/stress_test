"""ORM models package — import all models so Alembic can discover them."""

from src.models.ban import Ban
from src.models.bot_account import BotAccount
from src.models.bot_service_role import BotServiceRole
from src.models.bot_token import BotToken
from src.models.department import Department
from src.models.department_docker_registry import DepartmentDockerRegistry
from src.models.department_service_access import DepartmentServiceAccess
from src.models.group_service_access import GroupServiceAccess
from src.models.group_service_role import GroupServiceRole
from src.models.oauth_authorization_code import OAuthAuthorizationCode
from src.models.oauth_client import OAuthClient
from src.models.personal_access_token import PersonalAccessToken
from src.models.platform_service import PlatformService
from src.models.service_role_definition import ServiceRoleDefinition
from src.models.session import Session
from src.models.user import User
from src.models.user_group import UserGroup
from src.models.user_group_membership import UserGroupMembership
from src.models.user_service_role import UserServiceRole

__all__ = [
    "Ban",
    "BotAccount",
    "BotServiceRole",
    "BotToken",
    "Department",
    "DepartmentDockerRegistry",
    "DepartmentServiceAccess",
    "GroupServiceAccess",
    "GroupServiceRole",
    "OAuthAuthorizationCode",
    "OAuthClient",
    "PersonalAccessToken",
    "PlatformService",
    "ServiceRoleDefinition",
    "Session",
    "User",
    "UserGroup",
    "UserGroupMembership",
    "UserServiceRole",
]
