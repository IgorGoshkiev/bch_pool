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

    # ===== КОНСТАНТЫ ДЛЯ АДАПТАЦИИ (ВЫНЕСЕНЫ В НАЧАЛО КЛАССА) =====
    TARGET_TIME_BETWEEN_SHARES = 3.0  # Оптимальное время между шарами (секунды)
    TARGET_TIME_MIN_RATIO = 0.5  # Минимальное время = TARGET * 0.5 (1.5 сек)
    TARGET_TIME_MAX_RATIO = 2.0  # Максимальное время = TARGET * 2.0 (6.0 сек)
    ADAPTATION_RATE = 0.3  # 30% адаптации за шаг (было 0.03)
    MAX_CHANGE_RATIO = 3.0  # Максимальное изменение за раз (в 3 раза, было 1.3)
    TARGET_APPROACH_RATE = 2.0  # Скорость приближения к цели (в 2 раза)
    TARGET_REDUCE_RATE = 0.5  # Скорость снижения при превышении цели
    FORCED_JUMP_THRESHOLD = 0.01  # 1% от цели для форсированного прыжка

    def __init__(self, network_manager=None, stratum_server=None, tcp_stratum_server=None):
        # Персональные сложности майнеров
        self.miner_difficulties: Dict[str, float] = {}

        # Целевые сложности от ASIC (из suggest_difficulty)
        self.miner_target_difficulties: Dict[str, float] = {}

        # Network manager
        if network_manager:
            self.network_manager = network_manager
        else:
            from app.utils.network_config import NetworkManager
            self.network_manager = NetworkManager()

        self.stratum_server = stratum_server
        self.tcp_stratum_server = tcp_stratum_server

        # Глобальная сложность (для совместимости)
        network_config = self.network_manager.config
        self.current_difficulty = network_config['default_difficulty']
        self.target_shares_per_minute = settings.target_shares_per_minute
        self.min_difficulty = settings.min_difficulty
        self.max_difficulty = getattr(settings, 'max_difficulty', None)

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

    # ===== УПРАВЛЕНИЕ ЦЕЛЕВОЙ СЛОЖНОСТЬЮ =====

    def set_target_difficulty(self, miner_address: str, target: float) -> None:
        """
        Установить целевую сложность для майнера (от ASIC)

        ASIC отправляет mining.suggest_difficulty со своей желаемой сложностью.
        Мы сохраняем это значение как цель, к которой будем стремиться.
        """
        self.miner_target_difficulties[miner_address] = target
        print(f"🎯 [TARGET] Set for {miner_address[:20]}...: {target}", flush=True)

    # ===== ДОБАВЛЕНИЕ ШАРОВ =====

    async def add_share(self, miner_address: str, difficulty: float = 1.0) -> None:
        """Добавление шара для расчета сложности"""
        try:
            timestamp = datetime.now(UTC)

            if miner_address not in self.share_timestamps:
                self.share_timestamps[miner_address] = deque(maxlen=100)

            self.share_timestamps[miner_address].append(timestamp)

            share_record = {
                'timestamp': timestamp,
                'miner_address': miner_address,
                'difficulty': difficulty
            }
            self.share_history.append(share_record)

            if len(self.share_history) > self.max_history_size:
                self.share_history = self.share_history[-self.max_history_size:]

            self.total_shares += 1

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

    # ===== РАСЧЕТ ПЕРСОНАЛЬНОЙ СЛОЖНОСТИ =====

    async def calculate_difficulty_for_miner(self, miner_address: str) -> float:
        """
        Расчет оптимальной сложности для конкретного майнера
        Стремимся к сложности, которую предлагает ASIC
        """
        print(f"🔍 [DIFF_CALC] START for {miner_address[:20]}...", flush=True)

        # Получаем целевую сложность от ASIC
        target_difficulty = self.miner_target_difficulties.get(miner_address, None)
        if target_difficulty:
            print(f"🔍 [DIFF_CALC] Target from ASIC: {target_difficulty}", flush=True)
        else:
            print(f"🔍 [DIFF_CALC] No target from ASIC yet", flush=True)

        # Проверяем наличие данных
        if miner_address not in self.share_timestamps:
            print(f"🔍 [DIFF_CALC] No timestamps, returning min: {self.min_difficulty}", flush=True)
            return self.min_difficulty

        timestamps = list(self.share_timestamps[miner_address])
        print(f"🔍 [DIFF_CALC] timestamps count: {len(timestamps)}", flush=True)

        # Нужно минимум 20 шаров для стабильного расчета
        if len(timestamps) < 20:
            print(f"🔍 [DIFF_CALC] Too few timestamps ({len(timestamps)} < 20), returning min: {self.min_difficulty}",
                  flush=True)
            return self.min_difficulty

        # Анализируем последние 120 секунд
        now = datetime.now(UTC)
        recent = [ts for ts in timestamps if (now - ts).total_seconds() < 120]
        print(f"🔍 [DIFF_CALC] recent timestamps (120s): {len(recent)}", flush=True)

        # Нужно минимум 10 шаров за 2 минуты
        if len(recent) < 10:
            current = self.miner_difficulties.get(miner_address, self.min_difficulty)
            new_diff = max(self.min_difficulty, current / 2)
            self.miner_difficulties[miner_address] = new_diff
            print(f"🔍 [DIFF_CALC] Too few recent, lowering: {current:.10f} -> {new_diff:.10f}", flush=True)
            return new_diff

        # Рассчитываем временные интервалы между шарами
        time_diffs = []
        for i in range(1, len(recent)):
            diff = (recent[i] - recent[i - 1]).total_seconds()
            if 0.01 < diff < 60:  # Игнорируем выбросы
                time_diffs.append(diff)

        if not time_diffs:
            print(f"🔍 [DIFF_CALC] No valid time diffs, keeping current", flush=True)
            return self.miner_difficulties.get(miner_address, self.min_difficulty)

        # Используем медиану для устойчивости к выбросам
        avg_time = statistics.median(time_diffs)
        print(f"🔍 [DIFF_CALC] Median time between shares: {avg_time:.2f}s", flush=True)

        current = self.miner_difficulties.get(miner_address, self.min_difficulty)

        # ===== АДАПТАЦИЯ НА ОСНОВЕ ВРЕМЕНИ МЕЖДУ ШАРАМИ =====
        target_min = self.TARGET_TIME_BETWEEN_SHARES * self.TARGET_TIME_MIN_RATIO
        target_max = self.TARGET_TIME_BETWEEN_SHARES * self.TARGET_TIME_MAX_RATIO

        if avg_time < target_min:
            # Слишком часто — повышаем сложность
            ratio = self.TARGET_TIME_BETWEEN_SHARES / avg_time
            ratio = min(ratio, 3.0)  # Не более чем в 3 раза
            new_diff = current * (1 + (ratio - 1) * self.ADAPTATION_RATE)
            print(f"🔍 [DIFF_CALC] Too fast ({avg_time:.2f}s), raising: {current:.10f} -> {new_diff:.10f}", flush=True)

        elif avg_time > target_max:
            # Слишком редко — снижаем сложность
            ratio = self.TARGET_TIME_BETWEEN_SHARES / avg_time
            ratio = max(ratio, 0.33)
            new_diff = current * (1 - (1 - ratio) * self.ADAPTATION_RATE)
            print(f"🔍 [DIFF_CALC] Too slow ({avg_time:.2f}s), lowering: {current:.10f} -> {new_diff:.10f}", flush=True)

        else:
            # Оптимально — оставляем как есть
            new_diff = current
            print(f"🔍 [DIFF_CALC] Optimal ({avg_time:.2f}s), keeping: {current:.10f}", flush=True)

        # ===== СТРЕМЛЕНИЕ К ЦЕЛЕВОЙ СЛОЖНОСТИ =====
        if target_difficulty and target_difficulty > 0:
            # Если текущая сложность меньше 1% от цели - форсированный прыжок
            if new_diff < target_difficulty * self.FORCED_JUMP_THRESHOLD:
                new_diff = target_difficulty * self.FORCED_JUMP_THRESHOLD
                print(f"🔍 [DIFF_CALC] FORCED JUMP to 1% of target: {new_diff:.10f}", flush=True)
            elif new_diff < target_difficulty:
                # Плавно двигаемся к цели
                max_increase = new_diff * self.TARGET_APPROACH_RATE
                if target_difficulty > max_increase:
                    new_diff = max_increase
                    print(f"🔍 [DIFF_CALC] Approaching target: {new_diff:.10f}", flush=True)
                else:
                    new_diff = target_difficulty
                    print(f"🔍 [DIFF_CALC] Reached target: {target_difficulty:.10f}", flush=True)
            elif new_diff > target_difficulty:
                # Если превысили цель, снижаемся
                max_decrease = new_diff * self.TARGET_REDUCE_RATE
                if target_difficulty < max_decrease:
                    new_diff = max_decrease
                    print(f"🔍 [DIFF_CALC] Too high, reducing: {new_diff:.10f} -> {max_decrease:.10f}", flush=True)
                else:
                    new_diff = target_difficulty
                    print(f"🔍 [DIFF_CALC] Reached target: {target_difficulty:.10f}", flush=True)

        # ===== ЗАЩИТА ОТ РЕЗКИХ ИЗМЕНЕНИЙ =====
        if new_diff > current * self.MAX_CHANGE_RATIO and current > 0:
            new_diff = current * self.MAX_CHANGE_RATIO
            print(f"🔍 [DIFF_CALC] Capped at +{int((self.MAX_CHANGE_RATIO - 1) * 100)}%: {new_diff:.10f}", flush=True)
        elif new_diff < current / self.MAX_CHANGE_RATIO and current > 0:
            new_diff = current / self.MAX_CHANGE_RATIO
            print(f"🔍 [DIFF_CALC] Capped at -{int((1 - 1 / self.MAX_CHANGE_RATIO) * 100)}%: {new_diff:.10f}",
                  flush=True)

        # Защита от микро-значений
        if new_diff < self.min_difficulty:
            new_diff = self.min_difficulty
            print(f"🔍 [DIFF_CALC] Min difficulty: {new_diff:.10f}", flush=True)

        # Сохраняем в кэш
        self.miner_difficulties[miner_address] = new_diff
        print(f"🔍 [DIFF_CALC] FINAL new_diff: {new_diff:.10f}", flush=True)
        return new_diff

    # ===== РАСЧЕТ ХЭШРЕЙТА =====

    async def get_miner_hashrate(self, miner_address: str, period_minutes: int = 5) -> float:
        """Расчет хэшрейта майнера за период"""
        try:
            if miner_address not in self.share_timestamps:
                return 0.0

            timestamps = list(self.share_timestamps[miner_address])
            if not timestamps:
                return 0.0

            cutoff_time = datetime.now(UTC) - timedelta(minutes=period_minutes)
            recent_timestamps = [ts for ts in timestamps if ts > cutoff_time]

            if len(recent_timestamps) < 2:
                return 0.0

            time_diffs = []
            for i in range(1, len(recent_timestamps)):
                diff = (recent_timestamps[i] - recent_timestamps[i - 1]).total_seconds()
                time_diffs.append(diff)

            if not time_diffs:
                avg_time_between_shares = 1.0
            else:
                avg_time_between_shares = statistics.mean(time_diffs)
                if avg_time_between_shares < 0.1:
                    avg_time_between_shares = 0.1

            # Каждый шар при сложности 1.0 соответствует 2^32 хэшей
            hashes_per_share = 2 ** 32
            hashrate = hashes_per_share / avg_time_between_shares

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
            return total_hashrate
        except Exception as e:
            logger.error(
                "Ошибка расчета хэшрейта пула",
                event="difficulty_pool_hashrate_error",
                error=str(e)
            )
            return 0.0

    # ===== ГЛОБАЛЬНАЯ СЛОЖНОСТЬ (для совместимости) =====

    async def calculate_difficulty(self) -> float:
        """Расчет глобальной сложности (используется для broadcast)"""
        if not settings.enable_dynamic_difficulty:
            return self.current_difficulty

        try:
            shares_last_hour = self.shares_last_hour

            if shares_last_hour < 10:
                return self.current_difficulty

            actual_shares_per_minute = shares_last_hour / 60
            ratio = actual_shares_per_minute / self.target_shares_per_minute
            adjustment_factor = ratio ** 0.5

            new_difficulty = self.current_difficulty * adjustment_factor
            new_difficulty = max(self.min_difficulty, min(self.max_difficulty, new_difficulty))

            max_change_factor = 4.0
            if new_difficulty / self.current_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty * max_change_factor
            elif self.current_difficulty / new_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty / max_change_factor

            return new_difficulty

        except Exception as e:
            logger.error(
                "Ошибка расчета сложности",
                event="difficulty_calculation_error",
                error=str(e)
            )
            return self.current_difficulty

    async def update_difficulty(self) -> Tuple[bool, float, str]:
        """Обновление глобальной сложности и рассылка майнерам"""
        try:
            new_difficulty = await self.calculate_difficulty()

            if abs(new_difficulty - self.current_difficulty) < 0.01:
                return False, self.current_difficulty, "Change too small"

            old_difficulty = self.current_difficulty
            self.current_difficulty = new_difficulty
            self.last_difficulty_update = datetime.now(UTC)

            await self._broadcast_difficulty_update()

            logger.info(
                "Сложность обновлена",
                event="difficulty_updated",
                old_difficulty=old_difficulty,
                new_difficulty=new_difficulty
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
        """Рассылка глобального обновления сложности всем майнерам"""
        difficulty = self.current_difficulty

        if self.stratum_server:
            try:
                await self.stratum_server.update_difficulty(difficulty)
            except Exception as e:
                logger.error(f"WebSocket broadcast error: {e}")

        if self.tcp_stratum_server:
            try:
                await self.tcp_stratum_server.broadcast_difficulty(difficulty)
            except Exception as e:
                logger.error(f"TCP broadcast error: {e}")

    # ===== ОЧИСТКА И СТАТИСТИКА =====

    def cleanup_old_data(self, max_age_hours: int = 24) -> None:
        """Очистка старых данных"""
        try:
            cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

            old_count = len(self.share_history)
            self.share_history = [
                share for share in self.share_history
                if share['timestamp'] > cutoff_time
            ]
            removed_count = old_count - len(self.share_history)

            for miner_address in list(self.share_timestamps.keys()):
                timestamps = self.share_timestamps[miner_address]
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()
                if not timestamps:
                    del self.share_timestamps[miner_address]

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