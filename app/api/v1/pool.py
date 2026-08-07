"""
Pool API endpoints - статистика пула из памяти
"""
from datetime import datetime, UTC

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_config import StructuredLogger
from app.schemas.models import ApiResponse
from app.models.database import get_db

# Новый сервис статистики в памяти
from app.services.miner_stats import miner_stats_service
from app.utils.protocol_helpers import format_hashrate

logger = StructuredLogger(__name__)

router = APIRouter(prefix="/pool", tags=["pool"])


@router.get("/", response_model=ApiResponse)
async def pool_root():
    """Корневой эндпоинт pool API"""
    return ApiResponse(
        status="success",
        message="Pool API доступен",
        data={
            "endpoints": ["/stats", "/stats/live", "/hashrate", "/blocks"],
            "service": "BCH Solo Pool",
            "timestamp": datetime.now(UTC).isoformat()
        }
    )


@router.get("/stats/live", response_model=ApiResponse)
async def pool_stats_live():
    """
    Живая статистика пула (из памяти, без БД)
    """
    try:
        logger.debug("Запрос живой статистики пула")

        summary = await miner_stats_service.get_summary()
        active_miners = await miner_stats_service.get_active_miners_count()

        # Получаем детальную статистику по каждому майнеру
        all_stats = await miner_stats_service.get_all_stats()
        miners_detail = []
        total_hashrate = 0.0

        for address, stats in all_stats.items():
            hashrate = stats.get_hashrate(600)  # За последние 10 минут
            total_hashrate += hashrate
            miners_detail.append({
                "address": address,
                "shares": stats.total_shares,
                "accepted": stats.accepted_shares,
                "rejected": stats.rejected_shares,
                "hashrate": hashrate,
                "hashrate_formatted": format_hashrate(hashrate)
            })

        return ApiResponse(
            status="success",
            message="Живая статистика пула получена",
            data={
                "pool": {
                    "active_miners": active_miners,
                    "total_shares": summary["total_shares"],
                    "total_accepted": summary["total_accepted"],
                    "total_rejected": summary["total_rejected"],
                    "global_acceptance_rate": round(summary["global_acceptance_rate"], 4),
                    "total_hashrate": total_hashrate,
                    "total_hashrate_formatted": format_hashrate(total_hashrate)
                },
                "miners": miners_detail,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return ApiResponse(
            status="error",
            message=f"Ошибка получения статистики: {str(e)}",
            data={}
        )


@router.get("/hashrate", response_model=ApiResponse)
async def pool_hashrate():
    """
    Получение суммарного хэшрейта всех майнеров пула (из памяти)
    """
    try:
        all_stats = await miner_stats_service.get_all_stats()
        total_hashrate = 0.0

        for address, stats in all_stats.items():
            total_hashrate += stats.get_hashrate(600)

        return ApiResponse(
            status="success",
            message="Хэшрейт пула получен",
            data={
                "hashrate": {
                    "total": total_hashrate,
                    "unit": "H/s",
                    "formatted": format_hashrate(total_hashrate)
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Ошибка получения хэшрейта: {e}")
        return ApiResponse(
            status="error",
            message=f"Ошибка получения хэшрейта: {str(e)}",
            data={}
        )


@router.get("/blocks", response_model=ApiResponse)
async def pool_blocks(
        limit: int = 50,
        skip: int = 0,
        db: AsyncSession = Depends(get_db)
):
    """
    Получение последних блоков пула (из БД)
    """
    try:
        from app.dependencies import database_service

        blocks = await database_service.get_all_blocks(limit=limit, skip=skip)

        return ApiResponse(
            status="success",
            message=f"Получено {len(blocks)} блоков",
            data={
                "blocks": [
                    {
                        "id": b.id,
                        "height": b.height,
                        "hash": b.hash[:16] + "...",
                        "miner": b.miner_address,
                        "confirmed": b.confirmed,
                        "found_at": b.found_at.isoformat()
                    }
                    for b in blocks
                ],
                "count": len(blocks),
                "skip": skip,
                "limit": limit,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Ошибка получения блоков: {e}")
        return ApiResponse(
            status="error",
            message=f"Ошибка получения блоков: {str(e)}",
            data={}
        )