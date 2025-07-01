from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import INET
from app.db.base import Base

class ServiceIPRange(Base):
    __tablename__ = "service_ip_ranges"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String(50), nullable=False, index=True)
    ip_from = Column(INET, nullable=False)
    ip_to   = Column(INET, nullable=False)
