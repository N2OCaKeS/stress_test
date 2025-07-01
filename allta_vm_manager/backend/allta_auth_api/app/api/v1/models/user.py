from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.orm import relationship

from app.db.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)

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
