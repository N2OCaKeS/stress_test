"""ORM models package — import all models so Alembic can discover them."""

from src.models.ban import Ban
from src.models.bot_account import BotAccount
from src.models.bot_group_membership import BotGroupMembership
from src.models.bot_service_role import BotServiceRole
from src.models.bot_token import BotToken
from src.models.department import Department
from src.models.department_docker_registry import DepartmentDockerRegistry
from src.models.department_service_access import DepartmentServiceAccess
from src.models.group_service_access import GroupServiceAccess
from src.models.group_service_role import GroupServiceRole
from src.models.lockout_policy import LockoutPolicy
from src.models.nav_link import NavLink
from src.models.oauth_authorization_code import OAuthAuthorizationCode
from src.models.oauth_client import OAuthClient
from src.models.oauth_refresh_token import OAuthRefreshToken
from src.models.password_policy_settings import PasswordPolicySettings
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
    "BotGroupMembership",
    "BotServiceRole",
    "BotToken",
    "Department",
    "DepartmentDockerRegistry",
    "DepartmentServiceAccess",
    "GroupServiceAccess",
    "GroupServiceRole",
    "LockoutPolicy",
    "NavLink",
    "OAuthAuthorizationCode",
    "OAuthClient",
    "OAuthRefreshToken",
    "PasswordPolicySettings",
    "PersonalAccessToken",
    "PlatformService",
    "ServiceRoleDefinition",
    "Session",
    "User",
    "UserGroup",
    "UserGroupMembership",
    "UserServiceRole",
]
