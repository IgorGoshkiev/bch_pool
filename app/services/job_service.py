"""
Сервис для управления заданиями (jobs) - координация между JobManager и Stratum серверами
"""
from typing import Dict, Optional, Set, List, Tuple
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1

logger = StructuredLogger(__name__)


class JobService:
    """Сервис для управления заданиями майнинг-пула"""

    def __init__(self, validator=None):

        # Активные задания: job_id -> job_data
        self.active_jobs: Dict[str, dict] = {}

        # Подписки майнеров: miner_address -> set of job_ids
        self.miner_subscriptions: Dict[str, Set[str]] = {}

        # Счетчик заданий для уникальных ID
        self.job_counter = 0

        # История заданий (последние N)
        self.job_history: List[dict] = []
        self.max_history_size = 100

        # Последнее общее задание
        self.last_broadcast_job: Optional[dict] = None

        # Валидатор - создаем ЭКЗЕМПЛЯР класса
        self.validator = validator  # Может быть None, если не передан

        logger.info("JobService инициализирован")


    # ========== УПРАВЛЕНИЕ ЗАДАНИЯМИ ==========

    def create_job_id(self, miner_address: str = None) -> str:
        """Создание уникального ID задания"""
        self.job_counter += 1
        timestamp = int(datetime.now(UTC).timestamp())

        if miner_address:
            # Персональное задание для майнера
            job_id = f"job_{timestamp}_{self.job_counter:08x}_{miner_address[:8]}"
        else:
            # Общее задание для broadcast
            job_id = f"job_{timestamp}_{self.job_counter:08x}_broadcast"

        logger.debug(
            "Создан ID задания",
            event="job_service_job_id_created",
            job_id=job_id,
            miner_address=miner_address or "broadcast",
            job_counter=self.job_counter
        )

        return job_id

    def add_job(self, job_id: str, job_data: dict, miner_address: str = None):
        """
        Добавить задание в систему

        Args:
            job_id: Уникальный ID задания
            job_data: Данные задания в формате Stratum
            miner_address: Адрес майнера (если персональное задание)
        """
        try:
            # Добавляем extra_nonce1 в данные задания для валидатора
            if 'extra_nonce1' not in job_data:
                job_data['extra_nonce1'] = STRATUM_EXTRA_NONCE1

            # Сохраняем задание
            self.active_jobs[job_id] = job_data

            # Сохраняем в валидаторе
            self.validator.add_job(job_id, job_data)  # noqa

            # Если это персональное задание, добавляем в подписки майнера
            if miner_address:
                if miner_address not in self.miner_subscriptions:
                    self.miner_subscriptions[miner_address] = set()
                self.miner_subscriptions[miner_address].add(job_id)

            # Добавляем в историю
            job_record = {
                "id": job_id,
                "created_at": datetime.now(UTC),
                "miner_address": miner_address,
                "data": job_data,
                "type": "personal" if miner_address else "broadcast"
            }
            self.job_history.append(job_record)

            # Ограничиваем размер истории
            if len(self.job_history) > self.max_history_size:
                self.job_history = self.job_history[-self.max_history_size:]

            logger.info(
                "Задание добавлено в систему",
                event="job_added",
                job_id=job_id,
                miner_address=miner_address or "broadcast",
                job_type="personal" if miner_address else "broadcast",
                total_active_jobs=len(self.active_jobs),
                total_subscribed_miners=len(self.miner_subscriptions)
            )


        except Exception as e:
            logger.error(
                "Ошибка добавления задания",
                event="job_add_error",
                job_id=job_id,
                miner_address=miner_address,
                error=str(e)
            )

    def remove_job(self, job_id: str):
        """Удалить задание из системы"""
        try:
            # Удаляем из активных заданий
            job_data = self.active_jobs.pop(job_id, None)

            if not job_data:
                logger.warning(f"Задание {job_id} не найдено при удалении")
                return

            # Удаляем из подписок майнеров
            for miner_address in list(self.miner_subscriptions.keys()):
                if job_id in self.miner_subscriptions[miner_address]:
                    self.miner_subscriptions[miner_address].remove(job_id)

                    # Если у майнера больше нет подписок, удаляем запись
                    if not self.miner_subscriptions[miner_address]:
                        self.miner_subscriptions.pop(miner_address, None)

            # Удаляем из валидатора
            self.validator.remove_job(job_id)  # noqa

            logger.debug(f"Задание удалено: {job_id}")

        except Exception as e:
            logger.error(f"Ошибка удаления задания {job_id}: {e}")

    def get_job(self, job_id: str) -> Optional[dict]:
        """Получить задание по ID"""
        job = self.active_jobs.get(job_id)

        logger.debug(
            "Получение задания по ID",
            event="job_service_get_job",
            job_id=job_id,
            found=job is not None
        )

        return job

    def get_miner_jobs(self, miner_address: str) -> Set[str]:
        """Получить все задания майнера"""
        jobs = self.miner_subscriptions.get(miner_address, set())

        logger.debug(
            "Получение заданий майнера",
            event="job_service_get_miner_jobs",
            miner_address=miner_address,
            jobs_count=len(jobs)
        )

        return jobs

    def get_job_for_miner(self, miner_address: str) -> Optional[dict]:
        """
        Получить актуальное задание для майнера

        Returns:
            dict: Данные задания или None если нет активных заданий
        """
        try:
            # Сначала ищем персональные задания майнера
            miner_jobs = self.get_miner_jobs(miner_address)
            if miner_jobs:
                # Берем последнее задание майнера
                for job_id in sorted(miner_jobs, reverse=True):
                    job_data = self.get_job(job_id)
                    if job_data:
                        logger.debug(
                            "Найдено персональное задание для майнера",
                            event="job_service_found_personal_job",
                            miner_address=miner_address,
                            job_id=job_id
                        )
                        return job_data

            # Если нет персональных заданий, используем последнее общее
            if self.last_broadcast_job:
                logger.debug(
                    "Используется broadcast задание для майнера",
                    event="job_service_using_broadcast_job",
                    miner_address=miner_address
                )
                return self.last_broadcast_job

            # Если вообще нет заданий, создаем fallback
            return self.create_fallback_job(miner_address)


        except Exception as e:
            logger.error(
                "Ошибка получения задания для майнера",
                event="job_service_get_job_for_miner_error",
                miner_address=miner_address,
                error=str(e)
            )

            return self.create_fallback_job(miner_address)

    # ========== BROADCAST ==========

    def set_last_broadcast_job(self, job_data: dict):
        """Установить последнее broadcast задание"""
        self.last_broadcast_job = job_data.copy()

        # Также сохраняем в активных заданиях
        job_id = job_data["params"][0]
        self.add_job(job_id, job_data)

        logger.info(
            "Установлено broadcast задание",
            event="job_service_set_broadcast_job",
            job_id=job_id
        )

    def create_fallback_job(self, miner_address: str = None) -> dict:
        """Создать fallback задание (когда нет реальных заданий)"""
        from datetime import datetime, UTC

        job_id = self.create_job_id(miner_address)
        timestamp = int(datetime.now(UTC).timestamp())

        job_data = {
            "method": "mining.notify",
            "params": [
                job_id,
                "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9",  # prevhash
                "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff",  # coinb1
                "ffffffff0100f2052a010000001976a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac00000000",  # coinb2
                [],  # merkle_branch
                "20000000",  # version
                "1d00ffff",  # nbits
                format(timestamp, '08x'),  # ntime
                True  # clean_jobs
            ],
            "extra_nonce1": "ae6812eb4cd7735a302a8a9dd95cf71f"
        }

        # Сохраняем в системе
        self.add_job(job_id, job_data, miner_address)

        logger.warning(
            "Создание fallback задания",
            event="job_service_create_fallback",
            miner_address=miner_address or "unknown",
            reason="no_real_jobs_available"
        )
        return job_data

    # ========== ОЧИСТКА ==========

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий"""
        current_time = datetime.now(UTC)
        jobs_to_remove = []

        for job_id, job_data in self.active_jobs.items():
            try:
                # Извлекаем timestamp из job_id
                if job_id.startswith("job_"):
                    parts = job_id.split('_')
                    if len(parts) >= 2:
                        timestamp_str = parts[1]
                        job_time = datetime.fromtimestamp(float(timestamp_str), UTC)

                        age = (current_time - job_time).total_seconds()
                        if age > max_age_seconds:
                            jobs_to_remove.append(job_id)
            except (IndexError, ValueError, AttributeError):
                # Если не можем распарсить, удаляем
                jobs_to_remove.append(job_id)

        # Удаляем старые задания
        for job_id in jobs_to_remove:
            self.remove_job(job_id)

        if jobs_to_remove:
            logger.info(f"JobService: очищено {len(jobs_to_remove)} старых заданий")

    def cleanup_miner_jobs(self, miner_address: str):
        """Очистка всех заданий майнера"""
        try:
            miner_jobs = self.miner_subscriptions.get(miner_address, set()).copy()

            for job_id in miner_jobs:
                self.remove_job(job_id)

            if miner_jobs:
                logger.info(f"Очищено {len(miner_jobs)} заданий майнера {miner_address}")

        except Exception as e:
            logger.error(f"Ошибка очистки заданий майнера {miner_address}: {e}")

    # ========== СТАТИСТИКА ==========

    def get_stats(self) -> Dict:
        """Получить статистику сервиса"""
        total_subscriptions = sum(len(jobs) for jobs in self.miner_subscriptions.values())

        return {
            "active_jobs": len(self.active_jobs),
            "subscribed_miners": len(self.miner_subscriptions),
            "total_subscriptions": total_subscriptions,
            "job_counter": self.job_counter,
            "history_size": len(self.job_history),
            "has_broadcast_job": self.last_broadcast_job is not None
        }

    def get_job_history(self, limit: int = 10) -> List[dict]:
        """Получить историю заданий"""
        history = self.job_history[-limit:] if self.job_history else []

        # Форматируем для отображения
        formatted_history = []
        for job in reversed(history):  # Новые сверху
            formatted_history.append({
                "id": job["id"],
                "created_at": job["created_at"].isoformat(),
                "miner_address": job["miner_address"] or "broadcast",
                "type": job["type"],
                "job_id_short": job["id"][:16] + "..." if len(job["id"]) > 16 else job["id"]
            })

        return formatted_history

    def get_miner_job_stats(self, miner_address: str) -> Dict:
        """Получить статистику заданий майнера"""
        miner_jobs = self.get_miner_jobs(miner_address)

        return {
            "miner_address": miner_address,
            "total_jobs": len(miner_jobs),
            "active_jobs": len([j for j in miner_jobs if j in self.active_jobs]),
            "job_ids": list(miner_jobs)[:10]  # Первые 10 ID
        }

    # ========== ВАЛИДАЦИЯ ШАРОВ ==========

    def validate_and_process_share(self,
                                   job_id: str,
                                   extra_nonce2: str,
                                   ntime: str,
                                   nonce: str,
                                   miner_address: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Валидация и обработка шара

        Returns:
            Tuple[bool, error_message, job_data]
        """
        try:
            # Получаем задание
            job_data = self.get_job(job_id)

            if not job_data:
                return False, f"Задание {job_id} не найдено", None

            if self.validator is None:
                logger.error("Валидатор не инициализирован в JobService")
                return False, "Validator not initialized", None

            # Валидируем
            is_valid, error_msg = self.validator.validate_share(
                job_id=job_id,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce,
                miner_address=miner_address
            )

            if not is_valid:
                return False, error_msg, None

            # Если шар валиден, можно обновить статистику задания
            # (например, счетчик принятых шаров для этого задания)

            return True, None, job_data

        except Exception as e:
            logger.error(f"Ошибка валидации шара {job_id}: {e}")
            return False, f"Validation error: {str(e)}", None
