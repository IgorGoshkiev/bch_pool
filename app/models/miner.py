from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.models.database import Base


class Miner(Base):
    __tablename__ = "miners"

    id = Column(Integer, primary_key=True, index=True)
    bch_address = Column(String(128), unique=True, nullable=False, index=True)
    worker_name = Column(String(64), default="default")
    registered_at = Column(DateTime, default=datetime.utcnow)