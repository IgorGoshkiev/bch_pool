"""
Утилиты для работы с базой данных
"""
from typing import TypeVar, Optional, List, Sequence, Type
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import Select
from datetime import datetime, timedelta, UTC

from app.models.share import Share
from app.models.block import Block

T = TypeVar('T')


class TimeRangeParams:
    """Параметры временного диапазона"""

    def __init__(self, time_range: str = "24h"):
        self.time_range = time_range
        self.time_filter = self._parse_time_range(time_range)

    @staticmethod
    def _parse_time_range(time_range: str) -> Optional[datetime]:
        """Преобразование строки временного диапазона в datetime"""
        now = datetime.now(UTC)
        time_filters = {
            "1h": now - timedelta(hours=1),
            "24h": now - timedelta(days=1),
            "7d": now - timedelta(days=7),
            "30d": now - timedelta(days=30),
            "all": None
        }
        return time_filters.get(time_range, time_filters["24h"])

    def get_human_readable(self) -> str:
        """Получить человекочитаемое описание временного диапазона"""
        human_readable_map = {
            "1h": "последний час",
            "24h": "последние 24 часа",
            "7d": "последние 7 дней",
            "30d": "последние 30 дней",
            "all": "вся история"
        }
        return human_readable_map.get(self.time_range, "последние 24 часа")

    def get_seconds(self) -> Optional[int]:
        """Получить количество секунд в диапазоне"""
        time_seconds_map = {
            "1h": 3600,
            "24h": 86400,
            "7d": 604800,
            "30d": 2592000,
            "all": None
        }
        return time_seconds_map.get(self.time_range)


def _apply_filters_and_ordering(
        query: Select,
        model: Type[T],
        filters: Optional[dict] = None,
        order_by_field: Optional[str] = None,
        order_desc: bool = True
) -> Select:
    """
    Универсальная функция для применения фильтров и сортировки к запросу

    Args:
        query: Объект запроса SQLAlchemy
        model: Модель SQLAlchemy
        filters: Словарь фильтров (поле -> значение)
        order_by_field: Поле для сортировки
        order_desc: Сортировка по убыванию

    Returns:
        Select: Модифицированный запрос
    """
    # Применяем дополнительные фильтры
    if filters:
        for field, value in filters.items():
            if hasattr(model, field):
                query = query.where(getattr(model, field) == value)

    # Применяем сортировку
    if order_by_field and hasattr(model, order_by_field):
        order_attr = getattr(model, order_by_field)
        if order_desc:
            query = query.order_by(order_attr.desc())
        else:
            query = query.order_by(order_attr)

    return query


async def get_miner_data(
        db: AsyncSession,
        model: Type[T],
        bch_address: str,
        filters: Optional[dict] = None,
        order_by_field: Optional[str] = None,
        order_desc: bool = True,
        skip: int = 0,
        limit: int = 50
) -> List[T]:
    """
    Универсальная функция для получения данных майнера

    Args:
        db: Сессия базы данных
        model: Модель SQLAlchemy (Share или Block)
        bch_address: Адрес майнера
        filters: Словарь фильтров (например: {"is_valid": True, "confirmed": False})
        order_by_field: Поле для сортировки
        order_desc: Сортировка по убыванию
        skip: Сколько записей пропустить
        limit: Максимальное количество записей

    Returns:
        List[T]: Список записей
    """
    query = select(model).where(model.miner_address == bch_address)

    # Применяем фильтры и сортировку
    query = _apply_filters_and_ordering(query, model, filters, order_by_field, order_desc)

    # Применяем пагинацию
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    items: Sequence[T] = result.scalars().all()
    return list(items)


async def get_miner_data_with_time_filter(
        db: AsyncSession,
        model: Type[T],
        bch_address: str,
        time_field: str,
        time_filter: Optional[datetime],
        filters: Optional[dict] = None,
        order_by_field: Optional[str] = None,
        order_desc: bool = True
) -> List[T]:
    """
    Универсальная функция для получения данных майнера с фильтром по времени

    Args:
        db: Сессия базы данных
        model: Модель SQLAlchemy (Share или Block)
        bch_address: Адрес майнера
        time_field: Имя поля с временем
        time_filter: Фильтр времени (datetime или None)
        filters: Словарь дополнительных фильтров
        order_by_field: Поле для сортировки
        order_desc: Сортировка по убыванию

    Returns:
        List[T]: Список записей
    """
    query = select(model).where(model.miner_address == bch_address)

    # Применяем фильтр по времени
    if time_filter and hasattr(model, time_field):
        query = query.where(getattr(model, time_field) >= time_filter)

    # Применяем фильтры и сортировку
    query = _apply_filters_and_ordering(query, model, filters, order_by_field, order_desc)

    result = await db.execute(query)
    items: Sequence[T] = result.scalars().all()
    return list(items)


async def get_miner_stats_universal(
        db: AsyncSession,
        bch_address: str,
        time_filter: Optional[datetime] = None
) -> dict:
    """
    Универсальная функция для получения статистики майнера

    Args:
        db: Сессия базы данных
        bch_address: Адрес майнера
        time_filter: Фильтр времени

    Returns:
        dict: Словарь со списками shares и blocks
    """
    shares = await get_miner_data_with_time_filter(
        db=db,
        model=Share,
        bch_address=bch_address,
        time_field="submitted_at",
        time_filter=time_filter,
        order_by_field="submitted_at",
        order_desc=True
    )

    blocks = await get_miner_data_with_time_filter(
        db=db,
        model=Block,
        bch_address=bch_address,
        time_field="found_at",
        time_filter=time_filter,
        order_by_field="found_at",
        order_desc=True
    )

    return {
        "shares": shares,
        "blocks": blocks
    }


async def get_shares(
        db: AsyncSession,
        bch_address: str,
        skip: int = 0,
        limit: int = 50,
        valid_only: bool = False
) -> List[Share]:
    """Получить шары майнера с пагинацией"""
    return await get_miner_data(
        db=db,
        model=Share,
        bch_address=bch_address,
        filters={"is_valid": True} if valid_only else None,
        order_by_field="submitted_at",
        order_desc=True,
        skip=skip,
        limit=limit
    )


async def get_blocks(
        db: AsyncSession,
        bch_address: str,
        skip: int = 0,
        limit: int = 20,
        confirmed_only: bool = False
) -> List[Block]:
    """Получить блоки майнера с пагинацией"""
    return await get_miner_data(
        db=db,
        model=Block,
        bch_address=bch_address,
        filters={"confirmed": True} if confirmed_only else None,
        order_by_field="found_at",
        order_desc=True,
        skip=skip,
        limit=limit
    )