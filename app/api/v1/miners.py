"""
API endpoints for miners management
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.utils.helpers import humanize_time_ago
from app.utils.protocol_helpers import format_hashrate
from app.utils.protocol_helpers import DEFAULT_PAGINATION_LIMIT, MAX_PAGINATION_LIMIT
from app.schemas.models import ApiResponse, MinerResponse, MinerCreate

from app.models.database import get_db
from app.models.miner import Miner

# Новый сервис статистики в памяти
from app.services.miner_stats import miner_stats_service

logger = StructuredLogger(__name__)

router = APIRouter(prefix="/miners", tags=["miners"])


class ListMinersParams:
    """Параметры для списка майнеров"""

    def __init__(
            self,
            skip: int = Query(0, ge=0, description="Сколько записей пропустить"),
            limit: int = Query(DEFAULT_PAGINATION_LIMIT, ge=1, le=MAX_PAGINATION_LIMIT,
                               description="Максимальное количество записей"),
            active_only: bool = Query(False, description="Только активные майнеры")
    ):
        self.skip = skip
        self.limit = limit
        self.active_only = active_only


async def get_miner_or_404(bch_address: str, db: AsyncSession) -> Miner:
    """Найти майнера по адресу или вернуть 404"""
    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    miner = result.scalar_one_or_none()
    if not miner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Майнер с адресом {bch_address} не найден"
        )
    return miner


# ========== ОСНОВНЫЕ ЭНДПОИНТЫ (РАБОТАЮТ С БД) ==========

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
    """Получение списка майнеров с пагинацией."""
    query = select(Miner)

    if params.active_only:
        query = query.where(Miner.is_active.is_(true()))

    query = query.offset(params.skip).limit(params.limit)

    result = await db.execute(query)
    miners = result.scalars().all()

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
        miner_data: MinerCreate,
        db: AsyncSession = Depends(get_db)
):
    """Регистрация майнера в соло-пуле."""
    bch_address = miner_data.bch_address
    worker_name = miner_data.worker_name

    result = await db.execute(
        select(Miner).where(Miner.bch_address == bch_address)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Майнер с адресом {bch_address} уже зарегистрирован"
        )

    new_miner = Miner(
        bch_address=bch_address,
        worker_name=worker_name
    )

    try:
        db.add(new_miner)
        await db.commit()
        await db.refresh(new_miner)

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
)
async def get_miner(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """Получение информации о конкретном майнере по BCH адресу."""
    miner = await get_miner_or_404(bch_address, db)

    return ApiResponse(
        status="success",
        message=f"Информация о майнере {bch_address}",
        data={
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
    )


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
    """Удаление майнера из системы."""
    miner = await get_miner_or_404(bch_address, db)

    try:
        miner.is_active = False
        await db.commit()

        return ApiResponse(
            status="success",
            message=f"Майнер {bch_address} успешно деактивирован",
            data={
                "bch_address": bch_address,
                "action": "soft_delete",
                "note": "Майнер деактивирован, но данные сохранены в БД"
            }
        )

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
    """Обновление данных майнера."""
    miner = await get_miner_or_404(bch_address, db)

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

        return ApiResponse(
            status="success",
            message=f"Данные майнера {bch_address} обновлены",
            data={
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
        )

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении данных: {str(e)}"
        )


# ========== НОВЫЕ ЭНДПОИНТЫ ДЛЯ ЖИВОЙ СТАТИСТИКИ (ИЗ ПАМЯТИ) ==========

@router.get(
    "/{bch_address}/stats/live",
    summary="Живая статистика майнера",
    response_description="Статистика из памяти (последние 10 минут)"
)
async def get_miner_live_stats(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """Живая статистика майнера из памяти (без БД)."""
    miner = await get_miner_or_404(bch_address, db)

    stats = await miner_stats_service.get_stats(bch_address)

    if not stats:
        return ApiResponse(
            status="success",
            message=f"Майнер {bch_address} активен, но пока нет шаров",
            data={
                "miner": {
                    "bch_address": bch_address,
                    "worker_name": miner.worker_name,
                    "is_active": miner.is_active
                },
                "stats": {
                    "total_shares": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "acceptance_rate": 0,
                    "hashrate": 0,
                    "hashrate_formatted": "0 H/s",
                    "max_difficulty": 0,
                    "last_update": None
                }
            }
        )

    hashrate = await miner_stats_service.get_hashrate(bch_address)

    return ApiResponse(
        status="success",
        message=f"Живая статистика майнера {bch_address} получена",
        data={
            "miner": {
                "bch_address": bch_address,
                "worker_name": miner.worker_name,
                "is_active": miner.is_active
            },
            "stats": {
                "total_shares": stats.total_shares,
                "accepted": stats.accepted_shares,
                "rejected": stats.rejected_shares,
                "acceptance_rate": round(stats.accepted_shares / stats.total_shares if stats.total_shares > 0 else 0, 4),
                "hashrate": hashrate,
                "hashrate_formatted": format_hashrate(hashrate),
                "max_difficulty": stats.max_difficulty,
                "last_update": stats.last_update.isoformat()
            },
            "timestamp": datetime.now(UTC).isoformat()
        }
    )


@router.get(
    "/{bch_address}/shares/last",
    summary="Последние шары майнера",
    response_description="Последние N шаров из памяти"
)
async def get_last_shares(
        bch_address: str,
        limit: int = Query(50, ge=1, le=200, description="Количество шаров"),
        db: AsyncSession = Depends(get_db)
):
    """Получить последние N шаров майнера из памяти."""
    await get_miner_or_404(bch_address, db)

    shares = await miner_stats_service.get_last_shares(bch_address, limit)

    return ApiResponse(
        status="success",
        message=f"Получено {len(shares)} последних шаров",
        data={
            "address": bch_address,
            "count": len(shares),
            "shares": [s.to_dict() for s in shares],
            "timestamp": datetime.now(UTC).isoformat()
        }
    )


@router.get(
    "/{bch_address}/max-share",
    summary="Самый сложный шар майнера",
    response_description="Шар с максимальной сложностью"
)
async def get_max_difficulty_share(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """Получить шар с максимальной сложностью для майнера."""
    await get_miner_or_404(bch_address, db)

    share = await miner_stats_service.get_max_difficulty_share(bch_address)

    if not share:
        return ApiResponse(
            status="success",
            message="У майнера пока нет шаров",
            data={"share": None}
        )

    return ApiResponse(
        status="success",
        message="Самый сложный шар найден",
        data={
            "share": share.to_dict()
        }
    )


@router.get(
    "/{bch_address}/blocks",
    summary="Блоки майнера",
    response_description="Список найденных блоков майнера"
)
async def get_miner_blocks(
        bch_address: str,
        skip: int = Query(0, ge=0, description="Сколько пропустить"),
        limit: int = Query(20, ge=1, le=100, description="Количество блоков"),
        confirmed_only: bool = Query(False, description="Только подтвержденные"),
        db: AsyncSession = Depends(get_db)
):
    """Получение списка блоков, найденных майнером (из БД)."""
    miner = await get_miner_or_404(bch_address, db)

    from app.dependencies import database_service
    blocks = await database_service.get_blocks_by_miner(
        bch_address,
        limit=limit,
        skip=skip
    )

    if confirmed_only:
        blocks = [b for b in blocks if b.confirmed]

    return ApiResponse(
        status="success",
        message=f"Найдено {len(blocks)} блоков",
        data={
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
            ],
            "timestamp": datetime.now(UTC).isoformat()
        }
    )


@router.get(
    "/{bch_address}/stats/weekly",
    summary="Статистика майнера за неделю",
    response_description="Агрегированная статистика за 7 дней"
)
async def get_miner_weekly_stats(
        bch_address: str,
        db: AsyncSession = Depends(get_db)
):
    """Получить агрегированную статистику майнера за неделю"""
    await get_miner_or_404(bch_address, db)

    from app.services.aggregated_stats_service import AggregatedStatsService
    stats = await AggregatedStatsService.get_weekly_stats(bch_address)

    return ApiResponse(
        status="success",
        message=f"Статистика за неделю для {bch_address}",
        data=stats
    )