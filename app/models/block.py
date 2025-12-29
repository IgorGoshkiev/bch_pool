from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime
from app.models.database import Base


class Block(Base):
    __tablename__ = "blocks"

    id = Column(Integer, primary_key=True, index=True)
    height = Column(Integer, nullable=False, index=True)
    hash = Column(String(64), unique=True, nullable=False, index=True)
    miner_address = Column(String(128), nullable=False, index=True)
    confirmed = Column(Boolean, default=False)
    found_at = Column(DateTime, default=datetime.utcnow, index=True)