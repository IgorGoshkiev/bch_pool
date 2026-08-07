"""
Сервис для агрегированной статистики (за день/неделю)
"""
from datetime import datetime, UTC, timedelta, date
from typing import  Dict, Any
from sqlalchemy import select

from app.utils.logging_config import StructuredLogger
from app.models.database import AsyncSessionLocal
from app.models.aggregated_stats import AggregatedStats
from app.services.miner_stats import miner_stats_service

logger = StructuredLogger(__name__)


class AggregatedStatsService:
    """Сервис для агрегированной статистики"""

    @staticmethod
    async def aggregate_daily_stats(target_date: date = None):
        """
        Агрегировать статистику за день из памяти в БД.
        Запускается ежедневно в полночь.
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)  # Вчера

        logger.info(
            f"Начало агрегации статистики за {target_date}",
            event="aggregate_daily_stats_start",
            date=target_date.isoformat()
        )

        try:
            # Получаем все данные из памяти
            all_stats = await miner_stats_service.get_all_stats()

            if not all_stats:
                logger.info("Нет данных для агрегации", event="aggregate_no_data")
                return

            async with AsyncSessionLocal() as session:
                for address, stats in all_stats.items():
                    # Проверяем, есть ли уже запись за этот день
                    existing = await session.execute(
                        select(AggregatedStats)
                        .where(AggregatedStats.miner_address == address)
                        .where(AggregatedStats.date == target_date)
                    )
                    existing_record = existing.scalar_one_or_none()

                    # Фильтруем шары за нужный день
                    day_shares = [
                        s for s in stats.last_shares
                        if s.timestamp.date() == target_date
                    ]

                    if not day_shares:
                        continue

                    total_shares = len(day_shares)
                    accepted = sum(1 for s in day_shares if s.is_valid)
                    rejected = total_shares - accepted
                    total_difficulty = sum(s.difficulty for s in day_shares)
                    max_difficulty = max((s.difficulty for s in day_shares), default=0)
                    avg_difficulty = total_difficulty / total_shares if total_shares > 0 else 0

                    if existing_record:
                        # Обновляем существующую запись
                        existing_record.total_shares = total_shares
                        existing_record.accepted_shares = accepted
                        existing_record.rejected_shares = rejected
                        existing_record.total_difficulty = total_difficulty
                        existing_record.max_difficulty = max_difficulty
                        existing_record.avg_difficulty = avg_difficulty
                        existing_record.created_at = datetime.now(UTC)
                    else:
                        # Создаем новую запись
                        new_record = AggregatedStats(
                            miner_address=address,
                            date=target_date,
                            total_shares=total_shares,
                            accepted_shares=accepted,
                            rejected_shares=rejected,
                            total_difficulty=total_difficulty,
                            max_difficulty=max_difficulty,
                            avg_difficulty=avg_difficulty,
                            created_at=datetime.now(UTC)
                        )
                        session.add(new_record)

                await session.commit()
                logger.info(
                    "Агрегация завершена",
                    event="aggregate_daily_stats_completed",
                    date=target_date.isoformat(),
                    miners_count=len(all_stats)
                )

        except Exception as e:
            logger.error(
                f"Ошибка агрегации: {e}",
                event="aggregate_daily_stats_error",
                error=str(e)
            )

    @staticmethod
    async def get_weekly_stats(miner_address: str) -> Dict[str, Any]:
        """Получить статистику за 7 дней"""
        try:
            async with AsyncSessionLocal() as session:
                week_ago = date.today() - timedelta(days=7)

                result = await session.execute(
                    select(AggregatedStats)
                    .where(AggregatedStats.miner_address == miner_address)
                    .where(AggregatedStats.date >= week_ago)
                    .order_by(AggregatedStats.date.desc())
                )
                stats = result.scalars().all()

                # Если нет данных в БД, пробуем собрать из памяти
                if not stats:
                    return await AggregatedStatsService._get_weekly_from_memory(miner_address)

                total_shares = sum(s.total_shares for s in stats)
                total_accepted = sum(s.accepted_shares for s in stats)
                total_rejected = sum(s.rejected_shares for s in stats)

                return {
                    "miner": miner_address,
                    "period": "7d",
                    "summary": {
                        "total_shares": total_shares,
                        "accepted": total_accepted,
                        "rejected": total_rejected,
                        "acceptance_rate": total_accepted / total_shares if total_shares > 0 else 0
                    },
                    "daily": [
                        {
                            "date": s.date.isoformat(),
                            "total_shares": s.total_shares,
                            "accepted": s.accepted_shares,
                            "rejected": s.rejected_shares,
                            "max_difficulty": s.max_difficulty,
                            "avg_difficulty": s.avg_difficulty
                        }
                        for s in stats
                    ],
                    "timestamp": datetime.now(UTC).isoformat()
                }

        except Exception as e:
            logger.error(f"Ошибка получения недельной статистики: {e}")
            return {"miner": miner_address, "error": str(e)}

    @staticmethod
    async def _get_weekly_from_memory(miner_address: str) -> Dict[str, Any]:
        """Собрать недельную статистику из памяти (если нет в БД)"""
        stats = await miner_stats_service.get_stats(miner_address)
        if not stats:
            return {
                "miner": miner_address,
                "period": "7d",
                "summary": {"total_shares": 0, "accepted": 0, "rejected": 0, "acceptance_rate": 0},
                "daily": [],
                "from_memory": True
            }

        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)

        week_shares = [s for s in stats.last_shares if s.timestamp >= week_ago]

        if not week_shares:
            return {
                "miner": miner_address,
                "period": "7d",
                "summary": {"total_shares": 0, "accepted": 0, "rejected": 0, "acceptance_rate": 0},
                "daily": [],
                "from_memory": True
            }

        total = len(week_shares)
        accepted = sum(1 for s in week_shares if s.is_valid)
        rejected = total - accepted

        return {
            "miner": miner_address,
            "period": "7d",
            "summary": {
                "total_shares": total,
                "accepted": accepted,
                "rejected": rejected,
                "acceptance_rate": accepted / total if total > 0 else 0
            },
            "daily": [],
            "from_memory": True
        }