from datetime import datetime, UTC

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, true

from app.utils.logging_config import StructuredLogger

from app.schemas.models import ApiResponse
from app.models.database import get_db
from app.models.miner import Miner
from app.models.share import Share
from app.models.block import Block

logger = StructuredLogger(__name__)

router = APIRouter(prefix="/pool", tags=["pool"])


@router.get("/", response_model=ApiResponse)
async def pool_root():
    return ApiResponse(
        status="success",
        message="Pool API доступен",
        data={
            "endpoints": ["/stats", "/hashrate"],
            "service": "BCH Solo Pool",
            "timestamp": datetime.now(UTC).isoformat()
        }
    )


@router.get("/stats", response_model=ApiResponse)
async def pool_stats(db: AsyncSession = Depends(get_db)):
    """
    Получение общей статистики пула:
    - Количество майнеров
    - Количество шаров (shares)
    - Количество найденных блоков
    - Общий хэшрейт
    """
    try:
        logger.debug("Запрос статистики пула")
        # Количество майнеров
        result = await db.execute(select(func.count(Miner.id)))
        total_miners = result.scalar() or 0

        # Активные майнеры
        result = await db.execute(select(func.count(Miner.id)).where(Miner.is_active.is_(true())))
        active_miners = result.scalar() or 0

        # Общее количество шаров
        result = await db.execute(select(func.count(Share.id)))
        total_shares = result.scalar() or 0

        # Валидные шары
        result = await db.execute(select(func.count(Share.id)).where(Share.is_valid.is_(true())))
        valid_shares = result.scalar() or 0

        # Количество блоков
        result = await db.execute(select(func.count(Block.id)))
        total_blocks = result.scalar() or 0

        # Подтверждённые блоки
        result = await db.execute(select(func.count(Block.id)).where(Block.confirmed.is_(true())))
        confirmed_blocks = result.scalar() or 0

        # Общий хэшрейт
        result = await db.execute(select(func.sum(Miner.hashrate)))
        total_hashrate = float(result.scalar() or 0.0)

        return ApiResponse(
            status="success",
            message="Статистика пула получена",
            data={
                "pool": {
                    "miners": {
                        "total": total_miners,
                        "active": active_miners,
                        "inactive": total_miners - active_miners
                    },
                    "shares": {
                        "total": total_shares,
                        "valid": valid_shares,
                        "invalid": total_shares - valid_shares,
                        "validity_rate": valid_shares / total_shares if total_shares > 0 else 0
                    },
                    "blocks": {
                        "total": total_blocks,
                        "confirmed": confirmed_blocks,
                        "unconfirmed": total_blocks - confirmed_blocks,
                        "confirmation_rate": confirmed_blocks / total_blocks if total_blocks > 0 else 0
                    },
                    "hashrate": {
                        "total": total_hashrate,
                        "unit": "H/s"
                    }
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )

    except Exception as e:
        return ApiResponse(
            status="error",
            message=f"Ошибка получения статистики: {str(e)}",
            data={}
        )


@router.get("/hashrate", response_model=ApiResponse)
async def pool_hashrate(db: AsyncSession = Depends(get_db)):
    """
    Получение суммарного хэшрейта всех майнеров пула.
    """
    try:
        result = await db.execute(select(func.sum(Miner.hashrate)))
        total_hashrate = result.scalar() or 0.0

        return ApiResponse(
            status="success",
            message="Хэшрейт пула получен",
            data={
                "hashrate": {
                    "total": total_hashrate,
                    "unit": "H/s",
                    "formatted": f"{total_hashrate:,.2f} H/s"
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message=f"Ошибка получения хэшрейта: {str(e)}",
            data={}
        )