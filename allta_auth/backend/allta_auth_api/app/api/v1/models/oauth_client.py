from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func

from app.db.session import Base


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(128), unique=True, nullable=False, index=True)
    client_secret_hash = Column(String(255), nullable=False)
    display_name = Column(String(128), nullable=False)
    description = Column(String(255), nullable=True)
    redirect_uri_prefixes = Column(Text, nullable=False, default="")
    required_permission = Column(String(64), nullable=True)
    default_scope = Column(String(255), nullable=False, default="profile")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def redirect_prefixes(self) -> list[str]:
        raw = self.redirect_uri_prefixes or ""
        result: list[str] = []
        for item in raw.replace("\n", ",").split(","):
            normalized = item.strip().strip('"').strip("'")
            if normalized:
                result.append(normalized)
        return result
