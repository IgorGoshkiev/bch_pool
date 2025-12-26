from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.models.database import Base


class Share(Base):
    __tablename__ = "shares"

    id = Column(Integer, primary_key=True, index=True)
    miner_address = Column(String(128), nullable=False, index=True)
    job_id = Column(String(64), nullable=False)
    difficulty = Column(Integer, nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow, index=True)