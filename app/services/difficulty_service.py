"""
Сервис для управления динамической сложностью
"""
import statistics
from typing import Dict, List, Tuple
from datetime import datetime, UTC, timedelta
from collections import deque

from app.utils.logging_config import StructuredLogger
from app.utils.config import settings


logger = StructuredLogger(__name__)


class DifficultyService:
    """Сервис для расчета и управления сложностью"""

    def __init__(self, network_manager=None):
        # Используем network_manager если передан, иначе создаем временный
        if network_manager:
            self.network_manager = network_manager
        else:
            # Временное решение для обратной совместимости
            from app.utils.network_config import NetworkManager
            self.network_manager = NetworkManager()

        # Используем конфигурацию сети
        network_config = self.network_manager.config

        self.current_difficulty = network_config['default_difficulty']
        self.target_shares_per_minute = settings.target_shares_per_minute
        self.min_difficulty = settings.min_difficulty
        self.max_difficulty = settings.max_difficulty

        # История шаров для расчета сложности
        self.share_timestamps: Dict[str, deque] = {}
        self.share_history: List[Dict] = []
        self.max_history_size = 1000

        # Статистика
        self.total_shares = 0
        self.shares_last_hour = 0
        self.average_hashrate = 0.0
        self.last_difficulty_update = datetime.now(UTC)

        logger.info(
            "DifficultyService инициализирован",
            event="difficulty_service_initialized",
            current_difficulty=self.current_difficulty,
            target_shares_per_minute=self.target_shares_per_minute,
            min_difficulty=self.min_difficulty,
            max_difficulty=self.max_difficulty,
            network=self.network_manager.network,
            enable_dynamic_difficulty=settings.enable_dynamic_difficulty
        )

    async def add_share(self, miner_address: str, difficulty: float = 1.0) -> None:
        """Добавление шара для расчета сложности"""
        try:
            timestamp = datetime.now(UTC)

            # Инициализируем очередь для майнера если нужно
            if miner_address not in self.share_timestamps:
                self.share_timestamps[miner_address] = deque(maxlen=100)

            # Добавляем timestamp
            self.share_timestamps[miner_address].append(timestamp)

            # Добавляем в общую историю
            share_record = {
                'timestamp': timestamp,
                'miner_address': miner_address,
                'difficulty': difficulty
            }
            self.share_history.append(share_record)

            # Ограничиваем размер истории
            if len(self.share_history) > self.max_history_size:
                self.share_history = self.share_history[-self.max_history_size:]

            self.total_shares += 1

            # Обновляем статистику за последний час
            hour_ago = timestamp - timedelta(hours=1)
            self.shares_last_hour = sum(
                1 for share in self.share_history
                if share['timestamp'] > hour_ago
            )

            logger.debug(
                "Шар добавлен для расчета сложности",
                event="difficulty_share_added",
                miner_address=miner_address[:20] + "...",
                total_shares=self.total_shares,
                shares_last_hour=self.shares_last_hour
            )

        except Exception as e:
            logger.error(
                "Ошибка добавления шара для сложности",
                event="difficulty_share_add_error",
                miner_address=miner_address[:20] + "..." if miner_address else "unknown",
                error=str(e)
            )

    async def calculate_difficulty(self) -> float:
        """Расчет новой сложности на основе статистики"""
        if not settings.enable_dynamic_difficulty:
            return self.current_difficulty

        if not settings.enable_dynamic_difficulty:
            return self.current_difficulty

        try:
            now = datetime.now(UTC)
            minute_ago = now - timedelta(minutes=1)

            # Собираем статистику за разные периоды
            shares_last_hour = self.shares_last_hour
            shares_last_minute = sum(
                1 for share in self.share_history
                if share['timestamp'] > minute_ago
            )

            # Если нет достаточно данных, возвращаем текущую сложность
            if shares_last_hour < 10:
                logger.debug(
                    "Недостаточно данных для расчета сложности",
                    event="difficulty_insufficient_data",
                    shares_last_hour=shares_last_hour,
                    min_required=10
                )
                return self.current_difficulty

            # Рассчитываем фактическое количество шаров в минуту
            actual_shares_per_minute = shares_last_hour / 60

            # Рассчитываем отношение фактических к целевым
            ratio = actual_shares_per_minute / self.target_shares_per_minute

            # Плавная корректировка сложности
            adjustment_factor = ratio ** 0.5

            # Рассчитываем новую сложность
            new_difficulty = self.current_difficulty * adjustment_factor

            # Ограничиваем минимальную и максимальную сложность
            new_difficulty = max(self.min_difficulty, min(self.max_difficulty, new_difficulty))

            # Ограничиваем максимальное изменение
            max_change_factor = 4.0
            if new_difficulty / self.current_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty * max_change_factor
            elif self.current_difficulty / new_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty / max_change_factor

            logger.info(
                "Рассчитана новая сложность",
                event="difficulty_calculated",
                old_difficulty=self.current_difficulty,
                new_difficulty=new_difficulty,
                adjustment_factor=adjustment_factor,
                actual_shares_per_minute=actual_shares_per_minute,
                target_shares_per_minute=self.target_shares_per_minute,
                ratio=ratio,
                shares_last_hour=shares_last_hour,
                shares_last_minute=shares_last_minute
            )

            return new_difficulty

        except Exception as e:
            logger.error(
                "Ошибка расчета сложности",
                event="difficulty_calculation_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return self.current_difficulty

    async def update_difficulty(self) -> Tuple[bool, float, str]:
        """Обновление сложности и рассылка майнерам"""
        try:
            # Рассчитываем новую сложность
            new_difficulty = await self.calculate_difficulty()

            # Проверяем, нужно ли обновлять
            if abs(new_difficulty - self.current_difficulty) < 0.01:  # Изменение менее 1%
                logger.debug(
                    "Изменение сложности слишком мало",
                    event="difficulty_change_too_small",
                    current=self.current_difficulty,
                    new=new_difficulty,
                    difference=abs(new_difficulty - self.current_difficulty)
                )
                return False, self.current_difficulty, "Change too small"

            # Обновляем текущую сложность
            old_difficulty = self.current_difficulty
            self.current_difficulty = new_difficulty
            self.last_difficulty_update = datetime.now(UTC)

            # Рассылаем обновление майнерам
            await self._broadcast_difficulty_update()

            logger.info(
                "Сложность обновлена",
                event="difficulty_updated",
                old_difficulty=old_difficulty,
                new_difficulty=new_difficulty,
                change_percent=((new_difficulty / old_difficulty) - 1) * 100
            )

            return True, new_difficulty, "Difficulty updated"

        except Exception as e:
            logger.error(
                "Ошибка обновления сложности",
                event="difficulty_update_error",
                error=str(e)
            )
            return False, self.current_difficulty, f"Error: {str(e)}"

    async def _broadcast_difficulty_update(self) -> None:
        """Рассылка обновления сложности всем майнерам"""
        try:
            from app.dependencies import stratum_server, tcp_stratum_server

            # WebSocket майнеры
            if stratum_server:
                await stratum_server.update_difficulty(self.current_difficulty)

            # TCP майнеры
            # TODO: Реализовать broadcast для TCP сервера
            logger.info(
                "Обновление сложности разослано майнерам",
                event="difficulty_broadcasted",
                difficulty=self.current_difficulty
            )

        except Exception as e:
            logger.error(
                "Ошибка рассылки обновления сложности",
                event="difficulty_broadcast_error",
                error=str(e)
            )

    async def get_miner_hashrate(self, miner_address: str, period_minutes: int = 5) -> float:
        """Расчет хэшрейта майнера за период"""
        try:
            if miner_address not in self.share_timestamps:
                return 0.0

            timestamps = list(self.share_timestamps[miner_address])
            if not timestamps:
                return 0.0

            # Фильтруем по периоду
            cutoff_time = datetime.now(UTC) - timedelta(minutes=period_minutes)
            recent_timestamps = [ts for ts in timestamps if ts > cutoff_time]

            if len(recent_timestamps) < 2:
                return 0.0

            # Рассчитываем время между шарами
            time_diffs = []
            for i in range(1, len(recent_timestamps)):
                diff = (recent_timestamps[i] - recent_timestamps[i - 1]).total_seconds()
                time_diffs.append(diff)

            # Если все шары были почти одновременно, используем минимальное время
            if not time_diffs:
                avg_time_between_shares = 1.0
            else:
                avg_time_between_shares = statistics.mean(time_diffs)
                if avg_time_between_shares < 0.1:  # Минимум 0.1 секунды
                    avg_time_between_shares = 0.1

            # Рассчитываем хэшрейт
            # Каждый шар при сложности 1.0 соответствует 2^32 хэшей
            hashes_per_share = 2 ** 32  # 4,294,967,296
            hashrate = hashes_per_share / avg_time_between_shares

            logger.debug(
                "Рассчитан хэшрейт майнера",
                event="difficulty_miner_hashrate_calculated",
                miner_address=miner_address[:20] + "...",
                hashrate=hashrate,
                shares_count=len(recent_timestamps),
                period_minutes=period_minutes,
                avg_time_between_shares=avg_time_between_shares
            )

            return hashrate

        except Exception as e:
            logger.error(
                "Ошибка расчета хэшрейта майнера",
                event="difficulty_miner_hashrate_error",
                miner_address=miner_address[:20] + "..." if miner_address else "unknown",
                error=str(e)
            )
            return 0.0

    async def get_pool_hashrate(self, period_minutes: int = 5) -> float:
        """Расчет общего хэшрейта пула"""
        try:
            total_hashrate = 0.0

            for miner_address in self.share_timestamps.keys():
                hashrate = await self.get_miner_hashrate(miner_address, period_minutes)
                total_hashrate += hashrate

            logger.debug(
                "Рассчитан общий хэшрейт пула",
                event="difficulty_pool_hashrate_calculated",
                total_hashrate=total_hashrate,
                period_minutes=period_minutes,
                active_miners=len(self.share_timestamps)
            )

            return total_hashrate

        except Exception as e:
            logger.error(
                "Ошибка расчета хэшрейта пула",
                event="difficulty_pool_hashrate_error",
                error=str(e)
            )
            return 0.0

    def cleanup_old_data(self, max_age_hours: int = 24) -> None:
        """Очистка старых данных"""
        try:
            cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

            # Очищаем историю шаров
            initial_count = len(self.share_history)
            self.share_history = [
                share for share in self.share_history
                if share['timestamp'] > cutoff_time
            ]

            # Очищаем таймстампы майнеров
            for miner_address in list(self.share_timestamps.keys()):
                timestamps = self.share_timestamps[miner_address]
                # Удаляем старые таймстампы
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()

                # Если у майнера не осталось таймстампов, удаляем его
                if not timestamps:
                    del self.share_timestamps[miner_address]

            removed_count = initial_count - len(self.share_history)

            if removed_count > 0:
                logger.info(
                    "Очищены старые данные сложности",
                    event="difficulty_data_cleaned",
                    removed_records=removed_count,
                    remaining_records=len(self.share_history),
                    max_age_hours=max_age_hours
                )

        except Exception as e:
            logger.error(
                "Ошибка очистки данных сложности",
                event="difficulty_cleanup_error",
                error=str(e)
            )

    def get_stats(self) -> Dict:
        """Получение статистики сервиса сложности"""
        return {
            "current_difficulty": self.current_difficulty,
            "total_shares": self.total_shares,
            "shares_last_hour": self.shares_last_hour,
            "active_miners": len(self.share_timestamps),
            "target_shares_per_minute": self.target_shares_per_minute,
            "last_update": self.last_difficulty_update.isoformat(),
            "enable_dynamic": settings.enable_dynamic_difficulty,
            "min_difficulty": self.min_difficulty,
            "max_difficulty": self.max_difficulty,
            "history_size": len(self.share_history)
        }


