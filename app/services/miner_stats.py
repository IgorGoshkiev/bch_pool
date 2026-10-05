"""
Сервис для хранения статистики майнеров в памяти
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, UTC, timedelta
from collections import deque
import asyncio
from dataclasses import dataclass, field

from app.utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


@dataclass
class ShareInfo:
    """Информация о шаре"""
    hash: str
    difficulty: float                 # ← Best Share (SoloFury style), для отображения
    is_valid: bool
    timestamp: datetime
    job_id: str
    nonce: str
    ntime: str

    # ===== Display difficulty (то, что пул отправил ASIC через mining.set_difficulty) =====
    # Управляет частотой шаров. НЕ используется для расчёта Best Share или хэшрейта.
    display_difficulty: float = 0.0

    # ===== Share difficulty (сложность шара относительно 1) =====
    # Формула: share_difficulty = difficulty_1_target / hash_int
    # где difficulty_1_target = network_target × network_difficulty (из ноды).
    #
    # ЕДИНИЦЫ: безразмерное число (не SoloFury-единицы!).
    # НАЗНАЧЕНИЕ: расчёт хэшрейта.
    #   hashrate = Σ (share_difficulty × 2^32) / period_seconds
    #
    # ВАЖНО: НЕ путать с best_share (см. ниже).
    share_difficulty: float = 0.0

    # ===== Best Share (SoloFury style) =====
    # Формула: best_share = share_difficulty × network_difficulty
    #
    # ЕДИНИЦЫ: SoloFury-единицы (то же, что показывает SoloFury в bestDifficulty).
    # НАЗНАЧЕНИЕ: отображение в дашборде как «Best Share».
    #   Прогресс раунда = Σ best_share / network_difficulty
    #   (то есть round_share_sum хранит сумму share_difficulty, а не best_share —
    #    см. ниже, почему)
    #
    # ПРИМЕР: share_difficulty = 3.05e-10, network_difficulty = 5.4e11
    #         best_share = 3.05e-10 × 5.4e11 = 164.7
    best_share: float = 0.0
    # ================================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь для API"""
        return {
            "hash": self.hash[:16] + "...",
            "difficulty": self.difficulty,
            "is_valid": self.is_valid,
            "timestamp": self.timestamp.isoformat(),
            "job_id": self.job_id,
            "nonce": self.nonce,
            "ntime": self.ntime
        }


@dataclass
class MinerStatsData:
    """Статистика одного майнера"""
    address: str
    total_shares: int = 0
    accepted_shares: int = 0
    rejected_shares: int = 0
    total_difficulty: float = 0.0
    max_difficulty: float = 0.0
    max_difficulty_share: Optional[ShareInfo] = None

    # ===== Best Share (SoloFury) =====
    max_share_difficulty: float = 0.0
    max_share_difficulty_share: Optional[ShareInfo] = None
    # =================================

    # ===== Round info =====
    round_start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    round_share_sum: float = 0.0
    # ======================

    last_shares: deque = field(default_factory=lambda: deque(maxlen=1000))
    hashrate_history: deque = field(default_factory=lambda: deque(maxlen=360))
    last_update: datetime = field(default_factory=lambda: datetime.now(UTC))

    def add_share(self, share: ShareInfo):
        """Добавить шар в статистику"""
        self.total_shares += 1
        self.total_difficulty += share.difficulty

        # ===== Прогресс раунда =====
        # Храним сумму в SoloFury-единицах (best_share), потому что
        # в дашборде «Share Sum» показывается в тех же единицах,
        # что и Best Share (как у SoloFury).
        #
        # ВАЖНО: если хочешь показывать прогресс как «сумму сложностей
        # относительно 1», замени на share.share_difficulty.
        # Но тогда progress_percent надо считать по-другому.
        self.round_share_sum += share.best_share
        # ============================

        if share.is_valid:
            self.accepted_shares += 1
        else:
            self.rejected_shares += 1

        # ===== Best Share (SoloFury style) =====
        # max_difficulty теперь = best_share (SoloFury-единицы).
        # Это то, что показывается в дашборде как «Best Share».
        if share.difficulty > self.max_difficulty:
            self.max_difficulty = share.difficulty
            self.max_difficulty_share = share

        # Дублируем в max_share_difficulty для совместимости с API
        # (get_max_share_difficulty возвращает max_share_difficulty_share).
        if share.best_share > self.max_share_difficulty:
            self.max_share_difficulty = share.best_share
            self.max_share_difficulty_share = share
        # ======================================

        # Добавляем в историю
        self.last_shares.append(share)
        self.last_update = datetime.now(UTC)

    def get_hashrate(self, period_seconds: int = 600) -> float:
        """
        Рассчитать хэшрейт за последние N секунд.

        ВАЖНО: используем share_difficulty (сложность относительно 1),
        а НЕ best_share!

        ПОЧЕМУ:
        - 2^32 — это количество хэшей на 1 единицу сложности относительно 1.
        - Если использовать best_share (= share_difficulty × network_difficulty),
          то получим хэшрейт, умноженный на network_difficulty.

        Формула:
            hashrate = Σ (share_difficulty × 2^32) / period_seconds
        """
        if period_seconds <= 0:
            return 0.0

        now = datetime.now(UTC)
        total_hashes = 0.0

        for share in self.last_shares:
            age = (now - share.timestamp).total_seconds()
            if age <= period_seconds and share.is_valid:
                share_diff = share.share_difficulty or 0.0
                if share_diff > 0:
                    total_hashes += share_diff * (2 ** 32)

        if total_hashes == 0:
            return 0.0

        return total_hashes / period_seconds


    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь для API"""
        return {
            "address": self.address,
            "total_shares": self.total_shares,
            "accepted_shares": self.accepted_shares,
            "rejected_shares": self.rejected_shares,
            "acceptance_rate": self.accepted_shares / self.total_shares if self.total_shares > 0 else 0,
            "max_difficulty": self.max_difficulty,
            "last_update": self.last_update.isoformat()
        }


class MinerStatsService:
    """Сервис для хранения статистики майнеров в памяти"""

    def __init__(self):
        self._stats: Dict[str, MinerStatsData] = {}
        self._lock = asyncio.Lock()
        self._max_age_seconds = 600  # 10 минут
        self._cleanup_task = None
        self._running = False

        logger.info(
            "MinerStatsService инициализирован",
            event="miner_stats_service_initialized",
            max_age_seconds=self._max_age_seconds
        )

    async def start(self):
        """Запуск фоновой очистки"""
        if not self._running:
            self._running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(
                "Фоновая очистка статистики запущена",
                event="miner_stats_cleanup_started",
                cleanup_interval_seconds=60
            )

    async def get_round_info(self, address: str) -> Dict[str, Any]:
        """Получить информацию о текущем раунде майнера"""
        stats = await self.get_stats(address)
        if not stats:
            return {
                "round_start_time": None,
                "round_elapsed_seconds": 0,
                "round_share_sum": 0.0,
            }

        now = datetime.now(UTC)
        elapsed = (now - stats.round_start_time).total_seconds()

        return {
            "round_start_time": stats.round_start_time.isoformat(),
            "round_elapsed_seconds": elapsed,
            "round_share_sum": stats.round_share_sum,
        }

    async def reset_round(self, address: str):
        """Сбросить раунд (при нахождении блока или новом блоке)"""
        stats = await self.get_stats(address)
        if stats:
            stats.round_start_time = datetime.now(UTC)
            stats.round_share_sum = 0.0

    async def get_max_share_difficulty(self, address: str) -> Optional[ShareInfo]:
        """Получить шар с максимальным Best Share (SoloFury)"""
        stats = await self.get_stats(address)
        if not stats:
            return None
        return stats.max_share_difficulty_share


    async def stop(self):
        """Остановка фоновой очистки"""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info(
                "Фоновая очистка статистики остановлена",
                event="miner_stats_cleanup_stopped"
            )

    async def _cleanup_loop(self):
        """Фоновый цикл очистки"""
        while self._running:
            try:
                await asyncio.sleep(60)  # Каждую минуту
                await self._cleanup_old_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Ошибка в цикле очистки: {e}",
                    event="miner_stats_cleanup_loop_error",
                    error=str(e)
                )
                await asyncio.sleep(60)

    async def _cleanup_old_data(self):
        """Очистка данных старше max_age_seconds"""
        now = datetime.now(UTC)
        cutoff_time = now - timedelta(seconds=self._max_age_seconds)
        cleaned_count = 0

        async with self._lock:
            for address in list(self._stats.keys()):
                stats = self._stats[address]

                # Удаляем старые шары
                while stats.last_shares:
                    if stats.last_shares[0].timestamp < cutoff_time:
                        stats.last_shares.popleft()
                    else:
                        break

                # Если у майнера нет свежих данных - удаляем его
                if not stats.last_shares:
                    del self._stats[address]
                    cleaned_count += 1

            if cleaned_count > 0:
                logger.debug(
                    f"Очищено {cleaned_count} неактивных майнеров",
                    event="miner_stats_cleanup_completed",
                    cleaned_count=cleaned_count,
                    active_miners=len(self._stats)
                )

    async def add_share(self, address: str, share: ShareInfo):
        """Добавить шар в статистику майнера"""
        async with self._lock:
            if address not in self._stats:
                self._stats[address] = MinerStatsData(address=address)
                logger.debug(
                    f"Новый майнер добавлен в статистику: {address[:20]}...",
                    event="miner_stats_new_miner",
                    address=address[:20]
                )

            self._stats[address].add_share(share)

    async def get_stats(self, address: str) -> Optional[MinerStatsData]:
        """Получить статистику майнера"""
        async with self._lock:
            return self._stats.get(address)

    async def get_all_stats(self) -> Dict[str, MinerStatsData]:
        """Получить статистику всех майнеров"""
        async with self._lock:
            return self._stats.copy()

    async def get_hashrate(self, address: str, period_seconds: int = 600) -> float:
        """Получить хэшрейт майнера за период"""
        stats = await self.get_stats(address)
        if not stats:
            return 0.0
        return stats.get_hashrate(period_seconds)

    async def get_accepted_rejected(self, address: str) -> Dict:
        """Получить количество принятых/отклоненных шаров"""
        stats = await self.get_stats(address)
        if not stats:
            return {"accepted": 0, "rejected": 0, "total": 0}
        return {
            "accepted": stats.accepted_shares,
            "rejected": stats.rejected_shares,
            "total": stats.total_shares
        }

    async def get_max_difficulty_share(self, address: str) -> Optional[ShareInfo]:
        """Получить самый сложный шар майнера"""
        stats = await self.get_stats(address)
        if not stats:
            return None
        return stats.max_difficulty_share

    async def get_last_shares(self, address: str, limit: int = 50) -> List[ShareInfo]:
        """Получить последние N шаров майнера"""
        stats = await self.get_stats(address)
        if not stats:
            return []
        return list(stats.last_shares)[-limit:]

    async def get_hashrate_history(self, address: str) -> List[float]:
        """Получить историю хэшрейта для графика"""
        stats = await self.get_stats(address)
        if not stats:
            return []
        return list(stats.hashrate_history)

    async def get_active_miners_count(self) -> int:
        """Получить количество активных майнеров"""
        async with self._lock:
            return len(self._stats)

    async def get_summary(self) -> Dict:
        """Получить общую сводку по всем майнерам"""
        async with self._lock:
            total_shares = 0
            total_accepted = 0
            total_rejected = 0

            for stats in self._stats.values():
                total_shares += stats.total_shares
                total_accepted += stats.accepted_shares
                total_rejected += stats.rejected_shares

            return {
                "active_miners": len(self._stats),
                "total_shares": total_shares,
                "total_accepted": total_accepted,
                "total_rejected": total_rejected,
                "global_acceptance_rate": total_accepted / total_shares if total_shares > 0 else 0
            }


# Глобальный экземпляр
miner_stats_service = MinerStatsService()