from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.models.database import get_db
from app.models.miner import Miner
from app.models.share import Share
from app.models.block import Block

router = APIRouter(prefix="/pool", tags=["pool"])


@router.get("/")
async def pool_root():
    return {"message": "Pool API", "endpoints": ["/stats", "/hashrate"]}


@router.get(
    "/stats",
    summary="Статистика пула",
    response_description="Общая статистика майнинг-пула"
)
async def pool_stats(db: AsyncSession = Depends(get_db)):
    """
    Получение общей статистики пула:
    - Количество майнеров
    - Количество шаров (shares)
    - Количество найденных блоков
    - Общий хэшрейт
    """
    # Количество майнеров
    result = await db.execute(select(func.count(Miner.id)))
    total_miners = result.scalar()

    # Активные майнеры
    result = await db.execute(select(func.count(Miner.id)).where(Miner.is_active == True))
    active_miners = result.scalar()

    # Общее количество шаров
    result = await db.execute(select(func.count(Share.id)))
    total_shares = result.scalar()

    # Валидные шары
    result = await db.execute(select(func.count(Share.id)).where(Share.is_valid == True))
    valid_shares = result.scalar()

    # Количество блоков
    result = await db.execute(select(func.count(Block.id)))
    total_blocks = result.scalar()

    # Подтверждённые блоки
    result = await db.execute(select(func.count(Block.id)).where(Block.confirmed == True))
    confirmed_blocks = result.scalar()

    return {
        "pool": {
            "miners": {
                "total": total_miners,
                "active": active_miners,
                "inactive": total_miners - active_miners
            },
            "shares": {
                "total": total_shares,
                "valid": valid_shares,
                "invalid": total_shares - valid_shares
            },
            "blocks": {
                "total": total_blocks,
                "confirmed": confirmed_blocks,
                "unconfirmed": total_blocks - confirmed_blocks
            }
        }
    }


@router.get(
    "/hashrate",
    summary="Общий хэшрейт пула",
    response_description="Суммарный хэшрейт всех майнеров"
)
async def pool_hashrate(db: AsyncSession = Depends(get_db)):
    """
    Получение суммарного хэшрейта всех майнеров пула.
    """
    result = await db.execute(select(func.sum(Miner.hashrate)))
    total_hashrate = result.scalar() or 0.0

    return {
        "hashrate": {
            "total": total_hashrate,
            "unit": "H/s",
            "formatted": f"{total_hashrate:,.2f} H/s"
        }
    }