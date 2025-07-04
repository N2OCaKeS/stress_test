
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import INET
from app.db.base import Base

class IPRange(Base):
    __tablename__ = "ip_ranges"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, comment="Название диапазона")
    ip_start = Column(INET, nullable=False, comment="Начало диапазона")
    ip_end = Column(INET, nullable=False, comment="Конец диапазона")