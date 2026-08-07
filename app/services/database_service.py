"""
Сервис для работы с базой данных (только майнеры и блоки)
"""
from typing import Optional, List
from datetime import datetime, UTC
from sqlalchemy import select

from app.utils.logging_config import StructuredLogger
from app.models.database import AsyncSessionLocal
from app.models import Miner, Block


logger = StructuredLogger(__name__)


class DatabaseService:
    """Сервис для работы с базой данных"""


    # ========== МАЙНЕРЫ ==========

    @staticmethod
    async def get_miner_by_address(bch_address: str) -> Optional[Miner]:
        """Получить майнера по адресу"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == bch_address)
                )
                miner = result.scalar_one_or_none()

                logger.debug(
                    "Получение майнера по адресу",
                    event="db_get_miner",
                    bch_address=bch_address,
                    found=miner is not None
                )
                return miner

        except Exception as e:
            logger.error(
                "Ошибка получения майнера",
                event="db_get_miner_error",
                bch_address=bch_address,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    async def register_miner(bch_address: str, worker_name: str = "default") -> Optional[Miner]:
        """Регистрация/получение майнера"""
        try:
            async with AsyncSessionLocal() as session:
                # Проверяем существование
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == bch_address)
                )
                miner = result.scalar_one_or_none()

                if miner:
                    # Обновляем worker_name если изменился
                    if miner.worker_name != worker_name:
                        miner.worker_name = worker_name
                        await session.commit()
                        await session.refresh(miner)

                        logger.info(
                            "Обновлено имя воркера майнера",
                            event="db_update_worker_name",
                            bch_address=bch_address,
                            old_worker_name=miner.worker_name,
                            new_worker_name=worker_name
                        )
                    logger.debug(
                        "Майнер уже существует",
                        event="db_miner_exists",
                        bch_address=bch_address
                    )
                    return miner

                # Создаем нового
                miner = Miner(
                    bch_address=bch_address,
                    worker_name=worker_name[:64],
                    is_active=True,
                    total_shares=0, # Будет обновляться из памяти
                    hashrate=0.0
                )
                session.add(miner)
                await session.commit()
                await session.refresh(miner)

                logger.info(
                    "Майнер зарегистрирован",
                    event="db_miner_registered",
                    bch_address=bch_address,
                    worker_name=worker_name,
                    miner_id=miner.id
                )
                return miner

        except Exception as e:
            logger.error(
                "Ошибка регистрации майнера",
                event="db_register_miner_error",
                bch_address=bch_address,
                error=str(e),
            )
            return None

    # ========== БЛОКИ (ТОЛЬКО БЛОКИ СОХРАНЯЕМ В БД!) ==========
    @staticmethod
    async def save_block(
            height: int,
            block_hash: str,
            miner_address: str,
            confirmed: bool = False
    ) -> bool:
        """Сохранение информации о найденном блоке"""
        try:
            async with AsyncSessionLocal() as session:
                # Проверяем существование
                result = await session.execute(
                    select(Block).where(Block.hash == block_hash)
                )
                existing_block = result.scalar_one_or_none()

                if existing_block:
                    logger.warning(
                        "Блок уже существует в БД",
                        event="db_block_exists",
                        block_hash=block_hash,
                        height=height,
                        miner_address=miner_address
                    )
                    return False

                # Создаем запись
                block = Block(
                    height=height,
                    hash=block_hash,
                    miner_address=miner_address,
                    confirmed=confirmed,
                    found_at=datetime.now(UTC)
                )
                session.add(block)

                # Обновляем счетчик блоков у майнера
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == miner_address)
                )
                miner = result.scalar_one_or_none()
                if miner:
                    miner.total_blocks += 1

                await session.commit()

                logger.info(
                    "Блок сохранен в БД",
                    event="db_block_saved",
                    height=height,
                    block_hash=block_hash[:16] + "...",
                    miner_address=miner_address[:20] + "...",
                    confirmed=confirmed
                )
                return True

        except Exception as e:
            logger.error(
                f"Ошибка сохранения блока: {e}",
                event="db_block_save_error",
                error=str(e)
            )
            return False

    @staticmethod
    async def get_blocks_by_miner(
            miner_address: str,
            limit: int = 50,
            skip: int = 0
    ) -> List[Block]:
        """Получить блоки майнера"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Block)
                    .where(Block.miner_address == miner_address)
                    .order_by(Block.height.desc())
                    .offset(skip)
                    .limit(limit)
                )
                blocks = result.scalars().all()

                logger.debug(
                    "Получены блоки майнера",
                    event="db_get_blocks",
                    miner_address=miner_address[:20] + "...",
                    count=len(blocks)
                )
                return blocks

        except Exception as e:
            logger.error(
                "Ошибка получения блоков майнера",
                event="db_get_blocks_error",
                miner_address=miner_address[:20] + "...",
                error=str(e)
            )
            return []

    @staticmethod
    async def get_all_blocks(limit: int = 100, skip: int = 0) -> List[Block]:
        """Получить все блоки"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Block)
                    .order_by(Block.height.desc())
                    .offset(skip)
                    .limit(limit)
                )
                return result.scalars().all()

        except Exception as e:
            logger.error(
                "Ошибка получения всех блоков",
                event="db_get_all_blocks_error",
                error=str(e)
            )
            return []


