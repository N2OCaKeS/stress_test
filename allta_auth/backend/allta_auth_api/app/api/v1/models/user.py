from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.api.v1.models.access_control import ROLE_ADMIN, ROLE_USER
from app.db.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)

    tokens = relationship(
        "ActiveToken",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    api_tokens = relationship(
        "APIToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    role = relationship("Role", back_populates="users")
    groups = relationship(
        "Group",
        secondary="user_groups",
        back_populates="users",
    )

    def role_name(self) -> str:
        if self.role and self.role.name:
            return self.role.name
        return ROLE_USER

    def is_admin_effective(self) -> bool:
        return self.role_name() == ROLE_ADMIN

    def permission_codes(self) -> set[str]:
        if self.is_admin_effective():
            return {"*"}

        codes: set[str] = set()
        if self.role:
            for perm in self.role.permissions:
                codes.add(perm.code)
        for group in self.groups:
            for perm in group.permissions:
                codes.add(perm.code)
        return codes

    def has_permission(self, permission_code: str) -> bool:
        codes = self.permission_codes()
        return "*" in codes or permission_code in codes
