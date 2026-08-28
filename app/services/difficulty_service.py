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

    # ===== КОНСТАНТЫ ДЛЯ БЫСТРОЙ АДАПТАЦИИ =====
    TARGET_TIME_BETWEEN_SHARES = 5.0  # 5 секунд между шарами
    TARGET_TIME_MIN_RATIO = 0.5
    TARGET_TIME_MAX_RATIO = 2.0
    ADAPTATION_RATE = 0.3  #  30% адаптации за шаг
    MAX_CHANGE_PERCENT = 0.3  # 30% максимум за шаг
    MIN_TIMESTAMPS = 20  #  20 шаров для первого расчета

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
        self.target_shares_per_minute = getattr(settings, 'target_shares_per_minute', 15)
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
        Расчет оптимальной сложности для конкретного майнера на основе частоты шаров.

        АДАПТИВНЫЙ АЛГОРИТМ:
        1. Анализируем частоту шаров
        2. Рассчитываем хэшрейт майнера
        3. Вычисляем оптимальную сложность = хэшрейт * target_time / 2^32
        4. Не поднимаем выше оптимальной сложности
        5. Не опускаем ниже min_difficulty
        """
        print(f"🔍 [DIFF_CALC] START for {miner_address[:20]}...", flush=True)

        # Проверяем наличие данных о шарах
        if miner_address not in self.share_timestamps:
            print(f"🔍 [DIFF_CALC] No timestamps, returning min: {self.min_difficulty}", flush=True)
            return self.min_difficulty

        timestamps = list(self.share_timestamps[miner_address])
        print(f"🔍 [DIFF_CALC] timestamps count: {len(timestamps)}", flush=True)

        # Минимум 3 шара для первого расчета
        if len(timestamps) < 3:
            current = self.miner_difficulties.get(miner_address, self.min_difficulty)
            print(f"🔍 [DIFF_CALC] Too few timestamps ({len(timestamps)} < 3), keeping: {current:.10f}", flush=True)
            return current

        # Получаем текущую сложность майнера
        current_diff = self.miner_difficulties.get(miner_address, self.min_difficulty)
        print(f"🔍 [DIFF_CALC] Current difficulty: {current_diff:.10f}", flush=True)

        # Анализируем последние шары (берем последние 20)
        recent = timestamps[-20:] if len(timestamps) > 20 else timestamps
        print(f"🔍 [DIFF_CALC] Analyzing {len(recent)} recent shares", flush=True)

        # Вычисляем интервалы между шарами
        intervals = []
        for i in range(1, len(recent)):
            diff = (recent[i] - recent[i - 1]).total_seconds()
            if 0.05 < diff < 60:
                intervals.append(diff)

        if not intervals:
            print(f"🔍 [DIFF_CALC] No valid intervals, keeping: {current_diff:.10f}", flush=True)
            return current_diff

        # Используем медиану для устойчивости к выбросам
        median_interval = statistics.median(intervals)
        print(f"🔍 [DIFF_CALC] Median interval between shares: {median_interval:.3f}s", flush=True)

        # Целевое время между шарами (из настроек)
        target_time = getattr(settings, 'difficulty_target_time', 4.0)
        print(f"🔍 [DIFF_CALC] Target time: {target_time:.1f}s", flush=True)

        # Защита от деления на ноль
        if median_interval < 0.01:
            median_interval = 0.01
            print(f"🔍 [DIFF_CALC] Interval too small, clamped to 0.01s", flush=True)

        # ===== ОСНОВНОЙ РАСЧЕТ =====
        # Коэффициент изменения сложности
        ratio = target_time / median_interval
        print(f"🔍 [DIFF_CALC] Raw ratio (target/median): {ratio:.3f}", flush=True)

        # Ограничиваем изменение в 2 раза за шаг
        if ratio > 2.0:
            ratio = 2.0
            print(f"🔍 [DIFF_CALC] Ratio capped at 2.0", flush=True)
        elif ratio < 0.5:
            ratio = 0.5
            print(f"🔍 [DIFF_CALC] Ratio capped at 0.5", flush=True)

        # Применяем коэффициент адаптации
        adaptation_rate = getattr(settings, 'difficulty_adaptation_rate', 0.3)
        print(f"🔍 [DIFF_CALC] Adaptation rate: {adaptation_rate:.2f}", flush=True)

        if ratio > 1.0:
            new_diff = current_diff * (1 + (ratio - 1) * adaptation_rate)
            print(f"🔍 [DIFF_CALC] Increasing difficulty (ratio > 1)", flush=True)
        else:
            new_diff = current_diff * (1 - (1 - ratio) * adaptation_rate * 1.5)
            print(f"🔍 [DIFF_CALC] Decreasing difficulty (ratio < 1)", flush=True)

        print(f"🔍 [DIFF_CALC] New diff before limits: {new_diff:.10f}", flush=True)

        # ===== РАССЧИТЫВАЕМ ОПТИМАЛЬНУЮ СЛОЖНОСТЬ НА ОСНОВЕ ХЭШРЕЙТА =====
        # Хэшрейт за последние 5 минут
        hashrate = await self.get_miner_hashrate(miner_address, period_minutes=5)
        print(f"🔍 [DIFF_CALC] Hashrate: {hashrate:.2f} H/s", flush=True)

        # Оптимальная сложность = хэшрейт * target_time / 2^32
        # Это дает ~1 шар в target_time секунд
        if hashrate > 0:
            optimal_difficulty = (hashrate * target_time) / (2 ** 32)
            optimal_difficulty = max(1.0, float(int(optimal_difficulty)))
            print(f"🔍 [DIFF_CALC] Optimal difficulty: {optimal_difficulty:.0f}", flush=True)

            # НЕ ПОДНИМАЕМ ВЫШЕ ОПТИМАЛЬНОЙ СЛОЖНОСТИ
            if new_diff > optimal_difficulty:
                new_diff = optimal_difficulty
                print(f"🔍 [DIFF_CALC] Capped at optimal difficulty: {optimal_difficulty:.0f}", flush=True)
        else:
            print(f"🔍 [DIFF_CALC] Cannot calculate optimal difficulty (hashrate=0)", flush=True)
        # ============================================================

        # ===== ПРИМЕНЯЕМ ОГРАНИЧЕНИЯ =====
        # Минимальная сложность
        if new_diff < self.min_difficulty:
            new_diff = self.min_difficulty
            print(f"🔍 [DIFF_CALC] Capped by min: {self.min_difficulty}", flush=True)

        if new_diff < 1.0:
            new_diff = 1.0
            print(f"🔍 [DIFF_CALC] Capped at 1.0", flush=True)

        # Максимальная сложность из настроек (защита от дурака)
        if self.max_difficulty and new_diff > self.max_difficulty:
            new_diff = self.max_difficulty
            print(f"🔍 [DIFF_CALC] Capped at max_difficulty: {self.max_difficulty}", flush=True)

        # Округляем до целого числа для ASIC
        new_diff_rounded = max(1.0, float(int(new_diff)))
        print(f"🔍 [DIFF_CALC] Rounded for ASIC: {new_diff:.10f} -> {new_diff_rounded:.0f}", flush=True)

        if current_diff > 0:
            change_percent = ((new_diff_rounded / current_diff - 1) * 100)
            print(f"🔍 [DIFF_CALC] Change: {change_percent:+.1f}%", flush=True)

        print(f"🔍 [DIFF_CALC] FINAL new_diff: {new_diff_rounded:.0f}", flush=True)

        self.miner_difficulties[miner_address] = new_diff_rounded

        print(f"🔍 [DIFF_CALC] ===== END =====", flush=True)
        return new_diff_rounded

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

        print(f"\n{'=' * 60}", flush=True)
        print(f"📊 [DIFF_GLOBAL] ===== START =====", flush=True)
        print(f"📊 [DIFF_GLOBAL] Time: {datetime.now(UTC).strftime('%H:%M:%S')}", flush=True)

        if not settings.enable_dynamic_difficulty:
            print(f"📊 [DIFF_GLOBAL] Dynamic difficulty disabled, returning: {self.current_difficulty}", flush=True)
            print(f"{'=' * 60}\n", flush=True)
            return self.current_difficulty

        try:
            shares_last_hour = self.shares_last_hour
            print(f"📊 [DIFF_GLOBAL] shares_last_hour: {shares_last_hour}", flush=True)
            print(f"📊 [DIFF_GLOBAL] current_difficulty: {self.current_difficulty}", flush=True)
            print(f"📊 [DIFF_GLOBAL] target_shares_per_minute: {self.target_shares_per_minute}", flush=True)
            print(f"📊 [DIFF_GLOBAL] min_difficulty: {self.min_difficulty}", flush=True)
            print(f"📊 [DIFF_GLOBAL] max_difficulty: {self.max_difficulty}", flush=True)

            if shares_last_hour < 10:
                print(
                    f"📊 [DIFF_GLOBAL] Too few shares ({shares_last_hour} < 10), returning current: {self.current_difficulty}",
                    flush=True)
                print(f"{'=' * 60}\n", flush=True)
                return self.current_difficulty

            actual_shares_per_minute = shares_last_hour / 60
            print(f"📊 [DIFF_GLOBAL] actual_shares_per_minute: {actual_shares_per_minute:.4f}", flush=True)

            # ===== ЗАЩИТА ОТ ДЕЛЕНИЯ НА НОЛЬ =====
            if self.target_shares_per_minute <= 0:
                print(f"⚠️ [DIFF_GLOBAL] target_shares_per_minute is {self.target_shares_per_minute}, using default 15",
                      flush=True)
                self.target_shares_per_minute = 15

            ratio = actual_shares_per_minute / self.target_shares_per_minute
            print(f"📊 [DIFF_GLOBAL] ratio: {ratio:.6f}", flush=True)

            adjustment_factor = ratio ** 0.5
            print(f"📊 [DIFF_GLOBAL] adjustment_factor: {adjustment_factor:.6f}", flush=True)

            new_difficulty = self.current_difficulty * adjustment_factor
            print(f"📊 [DIFF_GLOBAL] new_difficulty (before limits): {new_difficulty:.10f}", flush=True)

            # Ограничиваем минимальную и максимальную сложность
            if self.max_difficulty:
                new_difficulty = min(new_difficulty, self.max_difficulty)
                print(f"📊 [DIFF_GLOBAL] after max limit ({self.max_difficulty}): {new_difficulty:.10f}", flush=True)

            new_difficulty = max(self.min_difficulty, new_difficulty)
            print(f"📊 [DIFF_GLOBAL] after min limit ({self.min_difficulty}): {new_difficulty:.10f}", flush=True)

            # Ограничиваем максимальное изменение
            max_change_factor = 4.0
            if new_difficulty / self.current_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty * max_change_factor
                print(f"📊 [DIFF_GLOBAL] capped at +{max_change_factor}x: {new_difficulty:.10f}", flush=True)
            elif self.current_difficulty / new_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty / max_change_factor
                print(f"📊 [DIFF_GLOBAL] capped at -{max_change_factor}x: {new_difficulty:.10f}", flush=True)

            print(f"📊 [DIFF_GLOBAL] FINAL new_difficulty: {new_difficulty:.10f}", flush=True)
            print(f"📊 [DIFF_GLOBAL] change: {((new_difficulty / self.current_difficulty - 1) * 100):.2f}%", flush=True)
            print(f"{'=' * 60}\n", flush=True)

            return new_difficulty

        except Exception as e:
            print(f"🔴 [DIFF_GLOBAL] EXCEPTION: {e}", flush=True)
            import traceback
            traceback.print_exc()
            logger.error(f"Ошибка расчета сложности: {e}")
            print(f"{'=' * 60}\n", flush=True)
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