from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, func
from app.models import Base


class Miner(Base):
    __tablename__ = "miners"

    id = Column(Integer, primary_key=True, index=True)
    bch_address = Column(String(128), unique=True, nullable=False, index=True)
    worker_name = Column(String(64), default="default")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    total_shares = Column(Integer, default=0)
    total_blocks = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    hashrate = Column(Float, default=0.0)

    def __repr__(self):
        return f"<Miner {self.bch_address}:{self.worker_name}>"