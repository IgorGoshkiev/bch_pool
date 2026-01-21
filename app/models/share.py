from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float
from datetime import datetime, UTC
from app.models import Base


class Share(Base):
    __tablename__ = "shares"

    id = Column(Integer, primary_key=True, index=True)
    miner_address = Column(String(128), nullable=False, index=True)
    job_id = Column(String(64), nullable=False)
    difficulty = Column(Float, nullable=False, default=1.0)
    is_valid = Column(Boolean, default=True)
    # поля для отладки
    extra_nonce2 = Column(String(16), nullable=True)
    ntime = Column(String(16), nullable=True)
    nonce = Column(String(16), nullable=True)
    submitted_at = Column(DateTime, default=lambda: datetime.now(UTC), index=True)

    def __repr__(self):
        return f"<Share {self.id}:{self.miner_address[:8]}:{self.job_id}>"