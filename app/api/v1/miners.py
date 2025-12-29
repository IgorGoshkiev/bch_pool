from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError

from app.models.database import get_db
from app.models.miner import Miner

router = APIRouter(prefix="/miners", tags=["miners"])


@router.get(
    "/",
    summary="Список всех майнеров",
    response_description="Список зарегистрированных майнеров"
) # Будет /api/v1/miners/
async def list_miners(
        skip: int = 0,
        limit: int = 100,
        active_only: bool = False,
        db: AsyncSession = Depends(get_db)
):
    """
    Получение списка майнеров с пагинацией.

    - **skip**: Сколько записей пропустить (для пагинации)
    - **limit**: Максимальное количество записей (по умолчанию 100)
    - **active_only**: Только активные майнеры (опционально)
    """
    query = select(Miner)

    if active_only:
        query = query.where(Miner.is_active == True)

    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    miners = result.scalars().all()

    return {
        "count": len(miners),
        "skip": skip,
        "limit": limit,
        "active_only": active_only,
        "miners": [
            {
                "id": m.id,
                "bch_address": m.bch_address,
                "worker_name": m.worker_name,
                "is_active": m.is_active,
                "total_shares": m.total_shares,
                "total_blocks": m.total_blocks,
                "hashrate": m.hashrate,
                "registered_at": m.created_at.isoformat() if hasattr(m, 'created_at') else None
            }
            for m in miners
        ]
    }


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация нового майнера",
    response_description="Данные зарегистрированного майнера"
)  # Будет /api/v1/miners/register
async def register_miner(
        bch_address: str,
        worker_name: str = "default",
        db: AsyncSession = Depends(get_db)
):
    """
    Регистрация майнера в соло-пуле.

    - **bch_address**: Адрес Bitcoin Cash для выплат (обязательно)
    - **worker_name**: Имя воркера (опционально, по умолчанию "default")
    """
    # Проверка формата адреса (упрощённо)
    if not bch_address or len(bch_address) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный BCH адрес"
        )

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

        return {
            "status": "registered",
            "message": "Майнер успешно зарегистрирован",
            "miner": {
                "id": new_miner.id,
                "bch_address": new_miner.bch_address,
                "worker_name": new_miner.worker_name,
                "registered_at": new_miner.created_at.isoformat() if hasattr(new_miner, 'created_at') else None,
                "total_shares": new_miner.total_shares,
                "total_blocks": new_miner.total_blocks
            }
        }

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