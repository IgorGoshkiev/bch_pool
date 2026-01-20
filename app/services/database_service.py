"""
Сервис для работы с базой данных (общий для всех серверов)
"""
from typing import Optional, Dict, List, Tuple
from datetime import datetime, UTC, timedelta
from sqlalchemy import select, func

from app.utils.logging_config import StructuredLogger
from app.models.database import AsyncSessionLocal
from app.models.miner import Miner
from app.models.share import Share
from app.models.block import Block

logger = StructuredLogger("database")


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
                    total_shares=0,
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
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    async def update_miner_stats(miner_address: str, is_valid_share: bool = True) -> bool:
        """Обновление статистики майнера после шара"""
        try:
            async with AsyncSessionLocal() as session:
                # Получаем майнера
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == miner_address)
                )
                miner = result.scalar_one_or_none()

                if not miner:
                    logger.warning(
                        "Майнер не найден при обновлении статистики",
                        event="db_update_miner_stats_not_found",
                        miner_address=miner_address
                    )
                    return False

                # Логируем состояние до обновления
                old_shares = miner.total_shares
                old_hashrate = miner.hashrate

                # Обновляем счетчик шаров
                if is_valid_share:
                    miner.total_shares += 1

                # Пересчитываем хэшрейт
                miner.hashrate = await DatabaseService._calculate_hashrate_for_miner(miner_address)

                await session.commit()

                logger.info(
                    "Статистика майнера обновлена",
                    event="db_miner_stats_updated",
                    miner_address=miner_address,
                    shares_before=old_shares,
                    shares_after=miner.total_shares,
                    hashrate_before=old_hashrate,
                    hashrate_after=miner.hashrate,
                    was_valid_share=is_valid_share
                )

                return True

        except Exception as e:
            logger.error(
                "Ошибка обновления статистики майнера",
                event="db_update_miner_stats_error",
                miner_address=miner_address,
                error=str(e)
            )
            return False

    # ========== ШАРЫ (SHARES) ==========

    @staticmethod
    async def save_share(
            miner_address: str,
            job_id: str,
            extra_nonce2: str = "",
            ntime: str = "",
            nonce: str = "",
            difficulty: float = 1.0,
            is_valid: bool = True
    ) -> Tuple[bool, Optional[int]]:
        """Сохранение шара в базу данных, возвращает (успех, ID шара)"""
        try:
            async with AsyncSessionLocal() as session:
                # Создаем запись о шаре
                share = Share(
                    miner_address=miner_address,
                    job_id=job_id,
                    difficulty=difficulty,
                    is_valid=is_valid,
                    extra_nonce2=extra_nonce2,
                    ntime=ntime,
                    nonce=nonce
                )
                session.add(share)
                await session.flush()  # Получаем ID
                share_id = share.id

                await session.commit()

                # Обновляем статистику майнера
                if is_valid:
                    await DatabaseService.update_miner_stats(miner_address, True)

                logger.info(
                    "Шар сохранен в БД",
                    event="db_share_saved",
                    miner_address=miner_address,
                    share_id=share_id,
                    job_id=job_id,
                    is_valid=is_valid,
                    difficulty=difficulty
                )
                return True, share_id

        except Exception as e:
            logger.error(
                "Ошибка сохранения шара в БД",
                event="db_save_share_error",
                miner_address=miner_address,
                job_id=job_id,
                error=str(e),
                error_type=type(e).__name__
            )
            return False, None

    @staticmethod
    async def get_shares_by_miner(miner_address: str, limit: int = 100) -> List[Share]:
        """Получить последние шары майнера"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Share)
                    .where(Share.miner_address == miner_address)
                    .order_by(Share.submitted_at.desc())
                    .limit(limit)
                )
                shares = result.scalars().all()

                logger.debug(
                    "Получены шары майнера",
                    event="db_get_miner_shares",
                    miner_address=miner_address,
                    limit=limit,
                    shares_count=len(shares)
                )
                return shares

        except Exception as e:
            logger.error(
                "Ошибка получения шаров майнера",
                event="db_get_miner_shares_error",
                miner_address=miner_address,
                error=str(e)
            )
            return []

    # ========== ХЭШРЕЙТ ==========

    @staticmethod
    async def _calculate_hashrate_for_miner(miner_address: str, time_period: int = 600) -> float:
        """Рассчитать хэшрейт майнера за последние N секунд"""
        try:
            if time_period <= 0:
                logger.debug(
                    "Некорректный период для расчета хэшрейта",
                    event="db_hashrate_invalid_period",
                    miner_address=miner_address,
                    time_period=time_period
                )
                return 0.0

            async with AsyncSessionLocal() as session:
                # Время начала периода
                start_time = datetime.now(UTC) - timedelta(seconds=time_period)

                # Получаем количество валидных шаров за период
                result = await session.execute(
                    select(func.count(Share.id))
                    .where(Share.miner_address == miner_address)
                    .where(Share.is_valid == True)
                    .where(Share.submitted_at >= start_time)
                )
                shares_count = result.scalar() or 0

                if shares_count == 0:
                    logger.debug(
                        "Нет шаров для расчета хэшрейта",
                        event="db_hashrate_no_shares",
                        miner_address=miner_address,
                        time_period=time_period,
                        start_time=start_time.isoformat()
                    )
                    return 0.0

                # При сложности 1.0, каждый шар = 2^32 хэшей
                hashes_per_share = 2 ** 32  # 4,294,967,296
                hashrate = (shares_count * hashes_per_share) / time_period

                logger.debug(
                    "Рассчитан хэшрейт майнера",
                    event="db_hashrate_calculated",
                    miner_address=miner_address,
                    shares_count=shares_count,
                    time_period_seconds=time_period,
                    hashrate=hashrate,
                    hashes_per_share=hashes_per_share
                )

                return hashrate

        except Exception as e:
            logger.error(
                "Ошибка расчета хэшрейта",
                event="db_hashrate_calculation_error",
                miner_address=miner_address,
                error=str(e),
                error_type=type(e).__name__
            )
            return 0.0

    @staticmethod
    async def get_miner_hashrate(miner_address: str) -> float:
        """Получить текущий хэшрейт майнера"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Miner.hashrate).where(Miner.bch_address == miner_address)
                )
                hashrate = result.scalar()

                logger.debug(
                    "Получен хэшрейт майнера из БД",
                    event="db_get_miner_hashrate",
                    miner_address=miner_address,
                    hashrate=hashrate or 0.0
                )

                return float(hashrate) if hashrate is not None else 0.0
        except Exception as e:
            logger.error(
                "Ошибка получения хэшрейта майнера",
                event="db_get_miner_hashrate_error",
                miner_address=miner_address,
                error=str(e)
            )
            return 0.0

    # ========== БЛОКИ ==========

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
                    logger.debug(
                        "Обновлен счетчик блоков майнера",
                        event="db_miner_blocks_updated",
                        miner_address=miner_address,
                        new_total_blocks=miner.total_blocks
                    )

                await session.commit()
                logger.info(
                    "Блок сохранен в БД",
                    event="db_block_saved",
                    height=height,
                    block_hash=block_hash[:16] + "...",
                    miner_address=miner_address,
                    confirmed=confirmed
                )
                return True

        except Exception as e:
            logger.error(f"Ошибка сохранения блока: {e}")
            return False

    # ========== СТАТИСТИКА ==========

    @staticmethod
    async def get_pool_stats() -> Dict:
        """Получить статистику пула"""
        try:
            async with AsyncSessionLocal() as session:
                # Майнеры
                result = await session.execute(select(func.count(Miner.id)))
                total_miners = result.scalar() or 0

                result = await session.execute(
                    select(func.count(Miner.id)).where(Miner.is_active == True)
                )
                active_miners = result.scalar() or 0

                # Шары
                result = await session.execute(select(func.count(Share.id)))
                total_shares = result.scalar() or 0

                result = await session.execute(
                    select(func.count(Share.id)).where(Share.is_valid == True)
                )
                valid_shares = result.scalar() or 0

                # Блоки
                result = await session.execute(select(func.count(Block.id)))
                total_blocks = result.scalar() or 0

                result = await session.execute(
                    select(func.count(Block.id)).where(Block.confirmed == True)
                )
                confirmed_blocks = result.scalar() or 0

                # Общий хэшрейт пула
                result = await session.execute(select(func.sum(Miner.hashrate)))
                total_hashrate = float(result.scalar() or 0.0)

                stats = {
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
                }

                logger.debug(
                    "Получена статистика пула из БД",
                    event="db_pool_stats",
                    total_miners=total_miners,
                    active_miners=active_miners,
                    total_shares=total_shares,
                    valid_shares=valid_shares,
                    total_blocks=total_blocks
                )
                return stats

        except Exception as e:
            logger.error(
                "Ошибка получения статистики пула из БД",
                event="db_pool_stats_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return {}

    @staticmethod
    async def get_miner_detailed_stats(miner_address: str, hours: int = 24) -> Dict:
        """Подробная статистика майнера"""
        try:
            async with AsyncSessionLocal() as session:
                # Получаем майнера
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == miner_address)
                )
                miner = result.scalar_one_or_none()

                if not miner:
                    logger.warning(
                        "Майнер не найден для детальной статистики",
                        event="db_miner_detailed_stats_not_found",
                        miner_address=miner_address
                    )
                    return {}

                # Временной диапазон
                start_time = datetime.now(UTC) - timedelta(hours=hours)

                # Шары за период
                result = await session.execute(
                    select(func.count(Share.id))
                    .where(Share.miner_address == miner_address)
                    .where(Share.submitted_at >= start_time)
                )
                recent_shares = result.scalar() or 0

                result = await session.execute(
                    select(func.count(Share.id))
                    .where(Share.miner_address == miner_address)
                    .where(Share.is_valid == True)
                    .where(Share.submitted_at >= start_time)
                )
                recent_valid_shares = result.scalar() or 0

                # Хэшрейт за период
                period_hashrate = await DatabaseService._calculate_hashrate_for_miner(
                    miner_address, hours * 3600
                )

                # Блоки за период
                result = await session.execute(
                    select(func.count(Block.id))
                    .where(Block.miner_address == miner_address)
                    .where(Block.found_at >= start_time)
                )
                recent_blocks = result.scalar() or 0

                stats = {
                    "miner": {
                        "address": miner.bch_address,
                        "worker_name": miner.worker_name,
                        "is_active": miner.is_active,
                        "total_shares": miner.total_shares,
                        "total_blocks": miner.total_blocks,
                        "current_hashrate": miner.hashrate
                    },
                    "recent": {
                        "hours": hours,
                        "shares": {
                            "total": recent_shares,
                            "valid": recent_valid_shares,
                            "invalid": recent_shares - recent_valid_shares,
                            "validity_rate": recent_valid_shares / recent_shares if recent_shares > 0 else 0
                        },
                        "blocks": recent_blocks,
                        "hashrate": period_hashrate
                    }
                }

                logger.info(
                    "Получена детальная статистика майнера",
                    event="db_miner_detailed_stats",
                    miner_address=miner_address,
                    hours=hours,
                    recent_shares=recent_shares,
                    recent_valid_shares=recent_valid_shares,
                    period_hashrate=period_hashrate
                )
                return stats

        except Exception as e:
            logger.error(
                "Ошибка получения детальной статистики майнера",
                event="db_miner_detailed_stats_error",
                miner_address=miner_address,
                hours=hours,
                error=str(e)
            )
            return {}
