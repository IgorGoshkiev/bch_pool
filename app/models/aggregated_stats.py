from sqlalchemy import Column, Integer, String, Float, Date, DateTime, BigInteger, Index
from datetime import datetime, UTC
from app.models import Base


class AggregatedStats(Base):
    """Агрегированная статистика майнера (за день)"""
    __tablename__ = "aggregated_stats"

    id = Column(Integer, primary_key=True, index=True)
    miner_address = Column(String(128), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    total_shares = Column(BigInteger, default=0)
    accepted_shares = Column(BigInteger, default=0)
    rejected_shares = Column(BigInteger, default=0)
    total_difficulty = Column(Float, default=0.0)
    max_difficulty = Column(Float, default=0.0)
    avg_difficulty = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    # Составной индекс для быстрых запросов
    __table_args__ = (
        Index('idx_aggregated_stats_miner_date', 'miner_address', 'date'),
    )

    def __repr__(self):
        return f"<AggregatedStats {self.miner_address[:8]} {self.date}>"