from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import datetime, timedelta, UTC

from app.utils.logging_config import StructuredLogger
from app.utils.helpers import humanize_time_ago
from app.utils.protocol_helpers import DEFAULT_PAGINATION_LIMIT, MAX_PAGINATION_LIMIT
from app.schemas.models import (
    ApiResponse,
    MinerResponse,
    MinerCreate,
)

from app.models.database import get_db
from app.models.miner import Miner
from app.models.share import Share
from app.models.block import Block

logger = StructuredLogger(__name__)

router = APIRouter(prefix="/miners", tags=["miners"])


class ListMinersParams:
    """Параметры для списка майнеров"""
    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Сколько записей пропустить"),
        limit: int = Query(DEFAULT_PAGINATION_LIMIT, ge=1, le=MAX_PAGINATION_LIMIT, description="Максимальное количество записей"),
        active_only: bool = Query(False, description="Только активные майнеры")
    ):
        self.skip = skip
        self.limit = limit
        self.active_only = active_only

@router.get(
    "/",
    summary="Список всех майнеров",
    response_description="Список зарегистрированных майнеров",
    response_model=ApiResponse
)
async def list_miners(
    params: ListMinersParams = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Получение списка майнеров с пагинацией.
    """
    query = select(Miner)

    if params.active_only:
        query = query.where(Miner.is_active.is_(true()))

    query = query.offset(params.skip).limit(params.limit)

    result = await db.execute(query)
    miners = result.scalars().all()

    # Преобразуем SQLAlchemy модели в Pydantic схемы
    miner_responses = [
        MinerResponse(
            id=m.id,
            bch_address=m.bch_address,
            worker_name=m.worker_name,
            is_active=m.is_active,
            total_shares=m.total_shares,
            total_blocks=m.total_blocks,
            hashrate=m.hashrate,
            registered_at=m.created_at
        )
        for m in miners
    ]

    return ApiResponse(
        status="success",
        message=f"Найдено {len(miner_responses)} майнеров",
        data={
            "miners": [miner.model_dump() for miner in miner_responses],
            "pagination": {
                "skip": params.skip,
                "limit": params.limit,
                "total": len(miner_responses)
            }
        }
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация нового майнера",
    response_description="Данные зарегистрированного майнера",
    response_model=ApiResponse
)
async def register_miner(
    miner_data: MinerCreate,  # ИСПОЛЬЗУЕМ PYDANTIC СХЕМУ
    db: AsyncSession = Depends(get_db)
):
    """
    Регистрация майнера в соло-пуле.

    - **bch_address**: Адрес Bitcoin Cash для выплат (обязательно)
    - **worker_name**: Имя воркера (опционально, по умолчанию "default")
    """
    # Данные уже валидированы через MinerCreate
    bch_address = miner_data.bch_address
    worker_name = miner_data.worker_name

    # Проверяем, существует ли уже майнер
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Майнер с адресом {bch_address} уже зарегистрирован"
        )

    # Создаём нового майнера
    new_miner = Miner(
        bch_address=bch_address,
        worker_name=worker_name
    )

    try:
        db.add(new_miner)
        await db.commit()
        await db.refresh(new_miner)

        # Создаем ответ через Pydantic
        miner_response = MinerResponse(
            id=new_miner.id,
            bch_address=new_miner.bch_address,
            worker_name=new_miner.worker_name,
            is_active=new_miner.is_active,
            total_shares=new_miner.total_shares,
            total_blocks=new_miner.total_blocks,
            hashrate=new_miner.hashrate,
            registered_at=new_miner.created_at
        )

        return ApiResponse(
            status="registered",
            message="Майнер успешно зарегистрирован",
            data={"miner": miner_response.model_dump()}
        )

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ошибка при сохранении майнера"
        )

@router.get(
    "/{bch_address}",
    summary="Информация о майнере",
    response_description="Детальная информация о майнере"
)  # Будет /api/v1/miners/{bch_address}
async def get_miner(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """
    Получение информации о конкретном майнере по BCH адресу.
    """
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()

    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )

    return {
        "miner": {
            "id": miner.id,
            "bch_address": miner.bch_address,
            "worker_name": miner.worker_name,
            "is_active": miner.is_active,
            "total_shares": miner.total_shares,
            "total_blocks": miner.total_blocks,
            "hashrate": miner.hashrate,
            "registered_at": miner.created_at.isoformat() if hasattr(miner, 'created_at') else None
        }
    }


@router.delete(
    "/{bch_address}",
    status_code=status.HTTP_200_OK,
    summary="Удаление майнера",
    response_description="Результат удаления майнера"
)
async def delete_miner(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """
    Удаление майнера из системы.

    - **bch_address**: Адрес Bitcoin Cash майнера
    - **hard_delete**: Полное удаление (false - деактивация, true - удаление из БД)
    """
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()

    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )

    try:
        # Мягкое удаление (деактивация) вместо физического удаления
        miner.is_active = False
        await db.commit()

        return {
            "status": "deactivated",
            "message": f"Майнер {bch_address} успешно деактивирован",
            "bch_address": bch_address,
            "action": "soft_delete",
            "note": "Майнер деактивирован, но данные сохранены в БД"
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при удалении майнера: {str(e)}"
        )


@router.put(
    "/{bch_address}/update",
    summary="Обновление данных майнера",
    response_description="Обновлённые данные майнера"
)
async def update_miner(
        bch_address: str,
        worker_name: Optional[str] = Query(None, description="Новое имя воркера"),
        is_active: Optional[bool] = Query(None, description="Статус активности"),
        db: AsyncSession = Depends(get_db)
):
    """
    Обновление данных майнера.

    - **bch_address**: Адрес Bitcoin Cash майнера
    - **worker_name**: Новое имя воркера (опционально)
    - **is_active**: Новый статус активности (опционально)
    """
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()

    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )

    # Обновляем только переданные поля
    update_data = {}

    if worker_name is not None:
        if len(worker_name) < 1 or len(worker_name) > 64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Имя воркера должно быть от 1 до 64 символов"
            )
        miner.worker_name = worker_name
        update_data["worker_name"] = worker_name

    if is_active is not None:
        miner.is_active = is_active
        update_data["is_active"] = is_active

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не указаны данные для обновления"
        )

    try:
        await db.commit()
        await db.refresh(miner)

        return {
            "status": "updated",
            "message": f"Данные майнера {bch_address} обновлены",
            "bch_address": bch_address,
            "updated_fields": update_data,
            "miner": {
                "id": miner.id,
                "bch_address": miner.bch_address,
                "worker_name": miner.worker_name,
                "is_active": miner.is_active,
                "total_shares": miner.total_shares,
                "total_blocks": miner.total_blocks,
                "hashrate": miner.hashrate
            }
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении данных: {str(e)}"
        )


@router.get(
    "/{bch_address}/stats",
    summary="Подробная статистика майнера",
    response_description="Детальная статистика по майнеру",
    response_model=ApiResponse
)
async def get_miner_stats(
        bch_address: str,
        time_range: Optional[str] = Query("24h", description="Временной диапазон: 1h, 24h, 7d, 30d, all"),
        db: AsyncSession = Depends(get_db)
):
    """
    Получение детальной статистики по майнеру.

    - **bch_address**: Адрес Bitcoin Cash майнера
    - **time_range**: Временной диапазон для статистики:
        - 1h: Последний час
        - 24h: Последние 24 часа
        - 7d: Последние 7 дней
        - 30d: Последние 30 дней
        - all: Вся история
    """

    try:
        # Сначала находим майнера
        result = await db.execute(
            select(Miner).where(Miner.bch_address == bch_address)
        )

        miner = result.scalar_one_or_none()

        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Майнер с адресом {bch_address} не найден"
            )

        # Допустимые значения
        valid_time_ranges = {"1h", "24h", "7d", "30d", "all"}

        # Если time_range не задан или некорректен, используем по умолчанию
        if not time_range or time_range not in  valid_time_ranges:
            time_range = "24h"

        # Определяем временной диапазон
        now = datetime.now(UTC)
        time_filters = {
            "1h": now - timedelta(hours=1),
            "24h": now - timedelta(days=1),
            "7d": now - timedelta(days=7),
            "30d": now - timedelta(days=30),
            "all": None
        }

        time_filter = time_filters.get(time_range, time_filters["24h"])

        # Словарь для человекочитаемого формата
        human_readable_map = {
            "1h": "последний час",
            "24h": "последние 24 часа",
            "7d": "последние 7 дней",
            "30d": "последние 30 дней",
            "all": "вся история"
        }

        human_readable = human_readable_map.get(time_range, "последние 24 часа")

        # ========================== Статистика по шарам
        shares_query = select(Share).where(Share.miner_address == bch_address)
        if time_filter:
            shares_query = shares_query.where(Share.submitted_at >= time_filter)

        result = await db.execute(shares_query)
        shares = result.scalars().all()

        # Валидные и невалидные шары
        valid_shares = [s for s in shares if s.is_valid]
        invalid_shares = [s for s in shares if not s.is_valid]

        # Средняя сложность
        avg_difficulty = 0
        if valid_shares:
            avg_difficulty = sum(s.difficulty for s in valid_shares) / len(valid_shares)

        #==========================================Статистика по блокам
        blocks_query = select(Block).where(Block.miner_address == bch_address)
        if time_filter:
            blocks_query = blocks_query.where(Block.found_at >= time_filter)

        result = await db.execute(blocks_query)
        blocks = result.scalars().all()

        # Подтверждённые блоки
        confirmed_blocks = [b for b in blocks if b.confirmed]

        #================================ Рассчитываем хэшрейт (упрощённо)
        hashrate_calc = 0.0
        if valid_shares and time_range != "all":
            # Примерная формула: сумма сложности / время в секундах
            total_difficulty = sum(s.difficulty for s in valid_shares)
            # Используем time_range_str вместо time_range
            time_seconds_map = {
                "1h": 3600,
                "24h": 86400,
                "7d": 604800,
                "30d": 2592000
            }

            #  time_range_str точно есть в словаре
            time_seconds = time_seconds_map.get(time_range, 86400)

            if valid_shares and time_seconds is not None and time_seconds > 0:
                # Примерная формула: сумма сложности / время в секундах
                # Каждый шар с difficulty 1.0 соответствует 2^32 хэшей
                hashes_per_share = 2 ** 32  # 4,294,967,296
                total_hashes = sum(s.difficulty * hashes_per_share for s in valid_shares)
                hashrate_calc = total_hashes / time_seconds

                # Форматируем ответ через ApiResponse
        return ApiResponse(
            status="success",
            message=f"Статистика майнера {bch_address} получена",
            data={
                "miner": {
                    "bch_address": miner.bch_address,
                    "worker_name": miner.worker_name,
                    "is_active": miner.is_active,
                    "registered_at": miner.created_at.isoformat() if hasattr(miner, 'created_at') else None
                },
                "time_range": {
                    "selected": time_range,
                    "human_readable": human_readable
                },
                "statistics": {
                    "shares": {
                        "total": len(shares),
                        "valid": len(valid_shares),
                        "invalid": len(invalid_shares),
                        "validity_rate": len(valid_shares) / len(shares) if shares else 0,
                        "avg_difficulty": avg_difficulty
                    },
                    "blocks": {
                        "total": len(blocks),
                        "confirmed": len(confirmed_blocks),
                        "unconfirmed": len(blocks) - len(confirmed_blocks),
                        "confirmation_rate": len(confirmed_blocks) / len(blocks) if blocks else 0
                    },
                    "performance": {
                        "total_shares": miner.total_shares,
                        "total_blocks": miner.total_blocks,
                        "current_hashrate": miner.hashrate,  # Из БД
                        "calculated_hashrate": hashrate_calc,  # Рассчитанный для периода
                        "unit": "H/s"
                    }
                },
                "recent_activity": {
                    "last_share": shares[-1].submitted_at.isoformat() if shares else None,
                    "last_block": blocks[-1].found_at.isoformat() if blocks else None
                }
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения статистики майнера {bch_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка при получении статистики: {str(e)}"
        )


@router.get(
    "/{bch_address}/shares",
    summary="Шары майнера",
    response_description="Список шаров (shares) майнера"
)
async def get_miner_shares(
        bch_address: str,
        skip: int = 0,
        limit: int = 50,
        valid_only: bool = False,
        db: AsyncSession = Depends(get_db)
):
    """
    Получение списка шаров майнера.

    - **bch_address**: Адрес Bitcoin Cash майнера
    - **skip**: Пропустить первых N записей
    - **limit**: Максимальное количество записей
    - **valid_only**: Только валидные шары
    """
    # Проверяем существование майнера
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()

    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )

    # Получаем шары
    query = select(Share).where(Share.miner_address == bch_address)

    if valid_only:
        query = query.where(Share.is_valid == True)

    query = query.order_by(Share.submitted_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    shares = result.scalars().all()

    return {
        "miner": bch_address,
        "worker_name": miner.worker_name,
        "shares_count": len(shares),
        "skip": skip,
        "limit": limit,
        "valid_only": valid_only,
        "shares": [
            {
                "id": s.id,
                "job_id": s.job_id,
                "difficulty": s.difficulty,
                "is_valid": s.is_valid,
                "submitted_at": s.submitted_at.isoformat(),
                "time_ago": humanize_time_ago(s.submitted_at) if hasattr(s, 'submitted_at') else None
            }
            for s in shares
        ]
    }


@router.get(
    "/{bch_address}/blocks",
    summary="Блоки майнера",
    response_description="Список найденных блоков майнера"
)
async def get_miner_blocks(
        bch_address: str,
        skip: int = 0,
        limit: int = 20,
        confirmed_only: bool = False,
        db: AsyncSession = Depends(get_db)
):
    """
    Получение списка блоков, найденных майнером.

    - **bch_address**: Адрес Bitcoin Cash майнера
    - **skip**: Пропустить первых N записей
    - **limit**: Максимальное количество записей
    - **confirmed_only**: Только подтверждённые блоки
    """
    # Проверяем существование майнера
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()

    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )

    # Получаем блоки
    query = select(Block).where(Block.miner_address == bch_address)

    if confirmed_only:
        query = query.where(Block.confirmed == True)

    query = query.order_by(Block.found_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    blocks = result.scalars().all()

    return {
        "miner": bch_address,
        "worker_name": miner.worker_name,
        "blocks_count": len(blocks),
        "skip": skip,
        "limit": limit,
        "confirmed_only": confirmed_only,
        "blocks": [
            {
                "id": b.id,
                "height": b.height,
                "hash": b.hash,
                "confirmed": b.confirmed,
                "found_at": b.found_at.isoformat(),
                "time_ago": humanize_time_ago(b.found_at) if hasattr(b, 'found_at') else None
            }
            for b in blocks
        ]
    }
