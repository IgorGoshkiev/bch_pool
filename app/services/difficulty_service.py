"""
Сервис для управления динамической сложностью
"""
import math
import statistics
from typing import Dict, List, Tuple
from datetime import datetime, UTC, timedelta
from collections import deque

from app.utils.logging_config import StructuredLogger
from app.utils.config import settings

logger = StructuredLogger(__name__)


class DifficultyService:
    """Сервис для расчета и управления сложностью"""

    def __init__(self, network_manager=None, stratum_server=None, tcp_stratum_server=None):
        # Персональные сложности майнеров (display_difficulty — то, что отправляем ASIC)
        self.miner_difficulties: Dict[str, float] = {}

        # Целевые сложности от ASIC (из suggest_difficulty)
        self.miner_target_difficulties: Dict[str, float] = {}

        # Минимальные сложности для каждого ASIC (из suggest_difficulty)
        # Индивидуально для каждого ASIC. Если suggest не было — START_DISPLAY_DIFFICULTY.
        self.min_asic_difficulties: Dict[str, float] = {}

        # Время последнего обновления сложности для каждого майнера
        # Нужно, чтобы не менять сложность на КАЖДОМ шаре (иначе улетает в космос)
        self.last_update_time: Dict[str, float] = {}

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

        # ===== DISPLAY DIFFICULTY (для ASIC) =====
        # Это то, что мы ОТПРАВЛЯЕМ ASIC через mining.set_difficulty.
        # Управляет частотой шаров и отображением на панели ASIC.
        self.start_display_difficulty = settings.start_display_difficulty
        self.min_display_difficulty = settings.min_display_difficulty
        self.max_display_difficulty = settings.max_display_difficulty

        # ===== VALIDATION DIFFICULTY (для валидации шаров) =====
        # Это то, с чем мы ВАЛИДИРУЕМ входящие шары.
        # Очень низкая, чтобы принимать ВСЕ шары от ASIC.
        self.default_validation_difficulty = settings.default_validation_difficulty

        # ===== ДИНАМИЧЕСКАЯ СЛОЖНОСТЬ =====
        # Все параметры берутся из .env через config.py
        self.difficulty_target_time = settings.difficulty_target_time
        self.difficulty_adaptation_rate = settings.difficulty_adaptation_rate
        self.difficulty_min_change = settings.difficulty_min_change
        self.difficulty_min_update_interval = settings.difficulty_min_update_interval
        # Первое обновление — быстрое (через N секунд после первого шара).
        # Нужно, чтобы ASIC быстро получил правильную сложность в начале.
        self.difficulty_first_update_interval = settings.difficulty_first_update_interval

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
            start_display_difficulty=self.start_display_difficulty,
            min_display_difficulty=self.min_display_difficulty,
            max_display_difficulty=self.max_display_difficulty,
            default_validation_difficulty=self.default_validation_difficulty,
            difficulty_target_time=self.difficulty_target_time,
            difficulty_adaptation_rate=self.difficulty_adaptation_rate,
            difficulty_min_change=self.difficulty_min_change,
            difficulty_min_update_interval=self.difficulty_min_update_interval,
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


    def reset_share_timestamps(self, miner_address: str) -> None:
        """
        Сброс временных меток шаров при смене сложности.

        ВАЖНО: это нужно, чтобы median_interval считался
        ТОЛЬКО по шарам с НОВОЙ сложностью.
        Иначе старые шары (с меньшей сложностью) искажают медиану,
        и пул принимает неправильные решения.

        Вызывается из tcp_server.handle_submit_tcp после успешной
        отправки set_difficulty.
        """
        if miner_address in self.share_timestamps:
            old_count = len(self.share_timestamps[miner_address])
            self.share_timestamps[miner_address].clear()
            print(f"🔄 [DIFF] Reset share_timestamps for {miner_address[:20]}... (was {old_count} entries)", flush=True)
        else:
            print(f"🔄 [DIFF] No share_timestamps to reset for {miner_address[:20]}...", flush=True)

    # ===== ДОБАВЛЕНИЕ ШАРОВ =====

    async def add_share(self, miner_address: str, difficulty: float = 1.0) -> None:
        """Добавление шара для расчета сложности"""
        try:
            timestamp = datetime.now(UTC)

            if miner_address not in self.share_timestamps:
                # Для мощного ASIC нужно больше истории (1000 временных меток)
                self.share_timestamps[miner_address] = deque(maxlen=1000)

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
        Расчет оптимальной сложности для конкретного майнера (как у Molehole).

        ЛОГИКА:
        1. Берём текущую сложность из tcp_stratum_server.
        2. Считаем медианный интервал между последними 20 шарами.
        3. Сравниваем с difficulty_target_time.
        4. Если шары идут ЧАЩЕ target → ПОВЫШАЕМ сложность (шары станут реже).
        5. Если шары идут РЕЖЕ target → ПОНИЖАЕМ сложность (шары станут чаще).
        6. Ограничиваем изменение в 2 раза за шаг.
        7. Округляем до целого.

        ВАЖНО:
        - suggest_difficulty от ASIC — это ТОЛЬКО ориентир.
        - Пул МОЖЕТ опустить сложность ниже suggest, если ASIC
          не справляется (шары идут слишком редко).
        - Пул МОЖЕТ поднять сложность выше suggest, если ASIC
          справляется легко (шары идут слишком часто).
        - DifficultyService НЕ проверяет частоту обновления.
          Это делает handle_submit_tcp в tcp_server.py.
        """
        print(f"\n{'=' * 60}", flush=True)
        print(f"🔍 [DIFF_CALC] ===== START for {miner_address[:20]}... =====", flush=True)

        # Проверяем наличие данных о шарах
        if miner_address not in self.share_timestamps:
            current = self.miner_difficulties.get(miner_address, self.start_display_difficulty)
            print(f"🔍 [DIFF_CALC] No timestamps, returning current: {current}", flush=True)
            return current

        timestamps = list(self.share_timestamps[miner_address])
        print(f"🔍 [DIFF_CALC] timestamps count: {len(timestamps)}", flush=True)

        # Минимум 3 шара для первого расчета
        if len(timestamps) < 3:
            current = self.miner_difficulties.get(miner_address, self.start_display_difficulty)
            print(f"🔍 [DIFF_CALC] Too few timestamps ({len(timestamps)} < 3), keeping: {current:.10f}", flush=True)
            return current

        # ===== КЛЮЧЕВОЕ: берём текущую сложность ИЗ tcp_stratum_server =====
        current_diff = None

        if self.tcp_stratum_server:
            current_diff = self.tcp_stratum_server.miner_difficulties.get(miner_address)
            if current_diff is not None:
                print(f"🔍 [DIFF_CALC] ✅ Current difficulty from tcp_stratum_server: {current_diff}", flush=True)

        # Fallback — своя копия
        if current_diff is None:
            current_diff = self.miner_difficulties.get(miner_address, self.start_display_difficulty)
            print(f"🔍 [DIFF_CALC] ⚠️ Fallback to difficulty_service: {current_diff}", flush=True)

        # ===== ПРОВЕРКА ЧАСТОТЫ ОБНОВЛЕНИЯ — УБРАНА ОТСЮДА =====
        # DifficultyService НЕ проверяет частоту.
        # Это делает handle_submit_tcp.
        print(f"🔍 [DIFF_CALC] ✅ Calculating new difficulty (frequency check in handle_submit_tcp)", flush=True)

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
        print(f"🔍 [DIFF_CALC] Last 5 intervals: {[f'{i:.2f}' for i in intervals[-5:]]}", flush=True)

        # Целевое время между шарами
        target_time = self.difficulty_target_time
        print(f"🔍 [DIFF_CALC] Target time: {target_time:.1f}s", flush=True)

        # Защита от деления на ноль
        if median_interval < 0.01:
            median_interval = 0.01
            print(f"🔍 [DIFF_CALC] Interval too small, clamped to 0.01s", flush=True)

        # ===== ОСНОВНОЙ РАСЧЕТ ПО ЧАСТОТЕ ШАРОВ =====
        # ratio = target_time / median_interval
        # - Если median_interval < target_time → ratio > 1 → ПОВЫШАЕМ
        # - Если median_interval > target_time → ratio < 1 → ПОНИЖАЕМ
        ratio = target_time / median_interval
        print(f"🔍 [DIFF_CALC] Raw ratio (target/median): {ratio:.3f}", flush=True)

        # Ограничиваем изменение в 2 раза за шаг
        if ratio > 2.0:
            ratio = 2.0
            print(f"🔍 [DIFF_CALC] Ratio capped at 2.0 (max increase 2x)", flush=True)
        elif ratio < 0.5:
            ratio = 0.5
            print(f"🔍 [DIFF_CALC] Ratio capped at 0.5 (max decrease 2x)", flush=True)

        # Применяем коэффициент адаптации
        adaptation_rate = self.difficulty_adaptation_rate
        print(f"🔍 [DIFF_CALC] Adaptation rate: {adaptation_rate:.2f}", flush=True)

        if ratio > 1.0:
            new_diff = current_diff * (1 + (ratio - 1) * adaptation_rate)
            print(f"🔍 [DIFF_CALC] Increasing difficulty (ratio > 1)", flush=True)
        else:
            new_diff = current_diff * (1 - (1 - ratio) * adaptation_rate * 1.5)
            print(f"🔍 [DIFF_CALC] Decreasing difficulty (ratio < 1)", flush=True)

        print(f"🔍 [DIFF_CALC] New diff by frequency: {new_diff:.10f}", flush=True)

        # ===== НИЖНЯЯ ГРАНИЦА — min_display_difficulty =====
        # ВАЖНО: Molehole опускает сложность, если ASIC не справляется.
        # Мы тоже опускаем, но НЕ ниже min_display_difficulty.
        if new_diff < self.min_display_difficulty:
            new_diff = self.min_display_difficulty
            print(f"🔍 [DIFF_CALC] Capped by min_display_difficulty: {self.min_display_difficulty}", flush=True)

        if new_diff < 1.0:
            new_diff = 1.0
            print(f"🔍 [DIFF_CALC] Capped at 1.0", flush=True)

        # Максимальная сложность из настроек
        if self.max_display_difficulty and new_diff > self.max_display_difficulty:
            new_diff = self.max_display_difficulty
            print(f"🔍 [DIFF_CALC] Capped at max_display_difficulty: {self.max_display_difficulty}", flush=True)

        # Округляем до целого числа для ASIC
        new_diff_rounded = max(1.0, float(int(new_diff)))
        print(f"🔍 [DIFF_CALC] Rounded for ASIC: {new_diff:.10f} -> {new_diff_rounded:.0f}", flush=True)

        # ===== ОКРУГЛЯЕМ ДО СТЕПЕНИ ДВОЙКИ (как Molehole) =====
        # ASIC (WhatsMiner) ожидает сложность в виде степеней двойки:
        # 16384, 32768, 65536, 131072, 262144, ...
        # Если отправить произвольное число (42583, 55357),
        # ASIC может ИГНОРИРОВАТЬ set_difficulty.

        if new_diff_rounded > 0:
            log2 = math.log2(new_diff_rounded)
            rounded_log2 = round(log2)
            power_of_two = float(2 ** rounded_log2)
            print(f"🔍 [DIFF_CALC] Rounded to power of 2: {new_diff_rounded} -> {power_of_two}", flush=True)
            new_diff_rounded = power_of_two
        # ======================================================

        if current_diff > 0:
            change_percent = ((new_diff_rounded / current_diff - 1) * 100)
            print(f"🔍 [DIFF_CALC] Change: {change_percent:+.1f}%", flush=True)

        print(f"🔍 [DIFF_CALC] FINAL new_diff: {new_diff_rounded:.0f}", flush=True)

        # ===== НЕ СОХРАНЯЕМ ЗДЕСЬ! =====
        # DifficultyService только СЧИТАЕТ.
        # handle_submit_tcp сам сравнит и отправит.
        print(f"🔍 [DIFF_CALC] Returning new_diff WITHOUT saving: {new_diff_rounded:.0f}", flush=True)
        print(f"🔍 [DIFF_CALC] ===== END =====", flush=True)
        print(f"{'=' * 60}\n", flush=True)

        return new_diff_rounded

    # ===== РАСЧЕТ ХЭШРЕЙТА =====

    async def get_miner_hashrate(self, miner_address: str, period_minutes: int = 5) -> float:
        """
        Расчет хэшрейта майнера за период.
        ВАЖНО: текущая сложность берётся ИЗ tcp_stratum_server (источник истины),
        потому что именно там хранится то, что мы ОТПРАВИЛИ ASIC через mining.set_difficulty.
        ВАЖНО: учитываем текущую сложность майнера!
        Каждый шар при сложности D соответствует D * 2^32 хэшей.
        """
        print(f"🔍 [HASHRATE] ===== START for {miner_address[:20]}... =====", flush=True)

        try:
            if miner_address not in self.share_timestamps:
                print(f"🔍 [HASHRATE] No timestamps for {miner_address[:20]}..., returning 0", flush=True)
                return 0.0

            timestamps = list(self.share_timestamps[miner_address])
            print(f"🔍 [HASHRATE] Total timestamps: {len(timestamps)}", flush=True)

            if not timestamps:
                return 0.0

            # Отбираем шары за последние period_minutes минут
            cutoff_time = datetime.now(UTC) - timedelta(minutes=period_minutes)
            recent_timestamps = [ts for ts in timestamps if ts > cutoff_time]
            print(f"🔍 [HASHRATE] Recent timestamps (last {period_minutes}min): {len(recent_timestamps)}", flush=True)

            if len(recent_timestamps) < 2:
                print(f"🔍 [HASHRATE] Not enough recent shares, returning 0", flush=True)
                return 0.0

            # Считаем среднее время между шарами
            time_diffs = []
            for i in range(1, len(recent_timestamps)):
                diff = (recent_timestamps[i] - recent_timestamps[i - 1]).total_seconds()
                if 0.05 < diff < 300:  # игнорируем выбросы
                    time_diffs.append(diff)

            if not time_diffs:
                print(f"🔍 [HASHRATE] No valid time diffs, returning 0", flush=True)
                return 0.0

            avg_time_between_shares = statistics.mean(time_diffs)
            if avg_time_between_shares < 0.1:
                avg_time_between_shares = 0.1

            print(f"🔍 [HASHRATE] avg_time_between_shares: {avg_time_between_shares:.3f}s", flush=True)

            # ===== КЛЮЧЕВОЕ: берём сложность ИЗ tcp_stratum_server =====
            current_diff = None

            if self.tcp_stratum_server:
                current_diff = self.tcp_stratum_server.miner_difficulties.get(miner_address)
                if current_diff:
                    print(f"🔍 [HASHRATE] ✅ Using difficulty from tcp_stratum_server: {current_diff}", flush=True)
                else:
                    print(f"🔍 [HASHRATE] ⚠️ tcp_stratum_server has no difficulty for this miner", flush=True)

            # Fallback — своя копия
            if current_diff is None:
                current_diff = self.miner_difficulties.get(miner_address, 1.0)
                print(f"🔍 [HASHRATE] ⚠️ Fallback to difficulty_service: {current_diff}", flush=True)

            # Каждый шар при сложности D = D * 2^32 хэшей
            hashes_per_share = current_diff * (2 ** 32)
            hashrate = hashes_per_share / avg_time_between_shares

            print(f"🔍 [HASHRATE] hashes_per_share = {hashes_per_share:.2e}", flush=True)
            print(f"🔍 [HASHRATE] hashrate = {hashrate:.2f} H/s ({hashrate / 1e12:.2f} TH/s)", flush=True)
            print(f"🔍 [HASHRATE] ===== END =====", flush=True)

            return hashrate

        except Exception as e:
            logger.error(
                "Ошибка расчета хэшрейта майнера",
                event="difficulty_miner_hashrate_error",
                miner_address=miner_address[:20] + "..." if miner_address else "unknown",
                error=str(e)
            )
            print(f"🔥 [HASHRATE] EXCEPTION: {e}", flush=True)
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
        """
        Расчет ГЛОБАЛЬНОЙ сложности (используется для broadcast).

        ВАЖНО: Этот метод считается УСТАРЕВШИМ. Он оставлен для совместимости.
        Основная логика — в calculate_difficulty_for_miner (персональная сложность).

        Глобальная сложность = медиана персональных сложностей всех майнеров.
        """
        print(f"\n{'=' * 60}", flush=True)
        print(f"📊 [DIFF_GLOBAL] ===== START =====", flush=True)
        print(f"📊 [DIFF_GLOBAL] Time: {datetime.now(UTC).strftime('%H:%M:%S')}", flush=True)

        # Если динамическая сложность выключена — возвращаем текущую
        if not settings.enable_dynamic_difficulty:
            print(f"📊 [DIFF_GLOBAL] Dynamic difficulty disabled, returning: {self.current_difficulty}", flush=True)
            print(f"{'=' * 60}\n", flush=True)
            return self.current_difficulty

        try:
            # ===== 1. СОБИРАЕМ ДАННЫЕ =====
            shares_last_hour = self.shares_last_hour
            print(f"📊 [DIFF_GLOBAL] shares_last_hour: {shares_last_hour}", flush=True)
            print(f"📊 [DIFF_GLOBAL] current_difficulty: {self.current_difficulty}", flush=True)
            print(f"📊 [DIFF_GLOBAL] min_display_difficulty: {self.min_display_difficulty}", flush=True)
            print(f"📊 [DIFF_GLOBAL] max_display_difficulty: {self.max_display_difficulty}", flush=True)

            # Если шаров мало — не меняем сложность
            if shares_last_hour < 10:
                print(
                    f"📊 [DIFF_GLOBAL] Too few shares ({shares_last_hour} < 10), returning current: {self.current_difficulty}",
                    flush=True)
                print(f"{'=' * 60}\n", flush=True)
                return self.current_difficulty

            # ===== 2. СЧИТАЕМ ФАКТИЧЕСКУЮ ЧАСТОТУ ШАРОВ =====
            actual_shares_per_minute = shares_last_hour / 60
            print(f"📊 [DIFF_GLOBAL] actual_shares_per_minute: {actual_shares_per_minute:.4f}", flush=True)

            # ===== 3. СРАВНИВАЕМ С ЦЕЛЕВОЙ ЧАСТОТОЙ =====
            # Целевая частота = 60 / difficulty_target_time
            # Например, target_time = 6 сек → 10 шаров в минуту
            target_shares_per_minute = 60.0 / self.difficulty_target_time
            print(f"📊 [DIFF_GLOBAL] target_shares_per_minute: {target_shares_per_minute:.4f}", flush=True)

            # Защита от деления на ноль
            if target_shares_per_minute <= 0:
                print(f"⚠️ [DIFF_GLOBAL] target_shares_per_minute is {target_shares_per_minute}, using default 10",
                      flush=True)
                target_shares_per_minute = 10.0

            # ===== 4. РАССЧИТЫВАЕМ НОВУЮ СЛОЖНОСТЬ =====
            ratio = actual_shares_per_minute / target_shares_per_minute
            print(f"📊 [DIFF_GLOBAL] ratio (actual/target): {ratio:.6f}", flush=True)

            # Используем квадратный корень для плавности
            adjustment_factor = ratio ** 0.5
            print(f"📊 [DIFF_GLOBAL] adjustment_factor: {adjustment_factor:.6f}", flush=True)

            new_difficulty = self.current_difficulty * adjustment_factor
            print(f"📊 [DIFF_GLOBAL] new_difficulty (before limits): {new_difficulty:.10f}", flush=True)

            # ===== 5. ПРИМЕНЯЕМ ГРАНИЦЫ =====
            # Максимальная сложность
            if self.max_display_difficulty:
                new_difficulty = min(new_difficulty, self.max_display_difficulty)
                print(f"📊 [DIFF_GLOBAL] after max limit ({self.max_display_difficulty}): {new_difficulty:.10f}",
                      flush=True)

            # Минимальная сложность
            new_difficulty = max(self.min_display_difficulty, new_difficulty)
            print(f"📊 [DIFF_GLOBAL] after min limit ({self.min_display_difficulty}): {new_difficulty:.10f}", flush=True)

            # ===== 6. ОГРАНИЧИВАЕМ МАКСИМАЛЬНОЕ ИЗМЕНЕНИЕ =====
            # Не более 4x за шаг
            max_change_factor = 4.0
            if new_difficulty / self.current_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty * max_change_factor
                print(f"📊 [DIFF_GLOBAL] capped at +{max_change_factor}x: {new_difficulty:.10f}", flush=True)
            elif self.current_difficulty / new_difficulty > max_change_factor:
                new_difficulty = self.current_difficulty / max_change_factor
                print(f"📊 [DIFF_GLOBAL] capped at -{max_change_factor}x: {new_difficulty:.10f}", flush=True)

            # ===== 7. ОКРУГЛЯЕМ ДО ЦЕЛОГО =====
            # ASIC ожидает целое число
            new_difficulty = max(1.0, float(int(new_difficulty)))
            print(f"📊 [DIFF_GLOBAL] rounded for ASIC: {new_difficulty:.0f}", flush=True)

            # ===== 8. ЛОГИРУЕМ ИЗМЕНЕНИЕ =====
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
                    # Также удаляем сложность если нет данных
                    self.miner_difficulties.pop(miner_address, None)

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
            "last_update": self.last_difficulty_update.isoformat(),
            "enable_dynamic": settings.enable_dynamic_difficulty,
            "start_display_difficulty": self.start_display_difficulty,
            "min_display_difficulty": self.min_display_difficulty,
            "max_display_difficulty": self.max_display_difficulty,
            "default_validation_difficulty": self.default_validation_difficulty,
            "difficulty_target_time": self.difficulty_target_time,
            "difficulty_adaptation_rate": self.difficulty_adaptation_rate,
            "difficulty_min_change": self.difficulty_min_change,
            "difficulty_min_update_interval": self.difficulty_min_update_interval,
            "history_size": len(self.share_history)
        }