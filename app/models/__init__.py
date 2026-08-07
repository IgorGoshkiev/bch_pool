"""
Инициализация моделей - избегаем циклических импортов
"""
from app.models.database import Base
from app.models.miner import Miner
from app.models.share import Share
from app.models.block import Block
from app.models.aggregated_stats import AggregatedStats

__all__ = ['Base', 'Miner', 'Share', 'Block', "AggregatedStats"]