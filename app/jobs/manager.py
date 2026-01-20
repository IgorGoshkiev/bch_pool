import time

from typing import Optional, Dict
from datetime import datetime, UTC

from app.utils.config import settings
from app.utils.logging_config import StructuredLogger

from app.jobs.real_node_client import RealBCHNodeClient
from app.services.job_service import JobService
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1

logger = StructuredLogger("job_manager")


class JobManager:
    """Менеджер заданий для майнинг пула - только реальная нода"""

    def __init__(self):
        # Используем настройки из config.py
        self.node_client = RealBCHNodeClient(
            rpc_host=settings.bch_rpc_host,
            rpc_port=settings.bch_rpc_port,
            rpc_user=settings.bch_rpc_user,
            rpc_password=settings.bch_rpc_password,
            use_cookie=settings.bch_rpc_use_cookie
        )
        self.job_service = JobService()
        self.current_job = None
        self.job_counter = 0
        self.block_height = 0
        self.difficulty = 0.0


    async def initialize(self) -> bool:
        """Инициализация менеджера с реальной нодой"""
        init_start = datetime.now(UTC)

        try:
            logger.info(
                "Инициализация JobManager",
                event="job_manager_initializing",
                rpc_host=settings.bch_rpc_host,
                rpc_port=settings.bch_rpc_port,
                use_cookie=settings.bch_rpc_use_cookie
            )

            if await self.node_client.connect():
                # Обновляем локальные переменные из клиента
                self.block_height = self.node_client.block_height
                self.difficulty = self.node_client.difficulty

                init_time = (datetime.now(UTC) - init_start).total_seconds() * 1000

                logger.info(
                    "JobManager успешно инициализирован",
                    event="job_manager_initialized",
                    block_height=self.block_height,
                    difficulty=self.difficulty,
                    chain=self.node_client.blockchain_info.get('chain', 'unknown') if hasattr(self.node_client,
                                                                                              'blockchain_info') else 'unknown',
                    init_time_ms=init_time
                )
                return True
            else:
                init_time = (datetime.now(UTC) - init_start).total_seconds() * 1000
                logger.error(
                    "Не удалось инициализировать JobManager",
                    event="job_manager_init_failed",
                    init_time_ms=init_time,
                    rpc_url=f"{settings.bch_rpc_host}:{settings.bch_rpc_port}"
                )
                return False

        except Exception as e:
            init_time = (datetime.now(UTC) - init_start).total_seconds() * 1000
            logger.error(
                "Ошибка инициализации JobManager",
                event="job_manager_init_error",
                error=str(e),
                error_type=type(e).__name__,
                init_time_ms=init_time
            )
            return False

    async def create_new_job(self, miner_address: str = None) -> Optional[Dict]:
        """Создать новое задание для майнера"""
        try:
            logger.debug(
                "Создание нового задания",
                event="job_manager_creating_job",
                miner_address=miner_address or "broadcast"
            )

            # Получаем шаблон блока от реальной ноды
            template = await self.node_client.get_block_template()
            if not template:
                logger.warning(
                    "Не удалось получить шаблон блока от ноды",
                    event="job_manager_no_template",
                    miner_address=miner_address
                )
                return None

            # Обновляем высоту блока
            if 'height' in template:
                old_height = self.block_height
                self.block_height = template['height']
                self.node_client.block_height = template['height']

                if old_height != self.block_height:
                    logger.info(
                        "Высота блокчейна обновлена",
                        event="job_manager_block_height_updated",
                        old_height=old_height,
                        new_height=self.block_height
                    )

            # Создаем уникальный ID задания
            self.job_counter += 1
            timestamp = int(time.time())

            if miner_address:
                job_id = f"job_{timestamp}_{self.job_counter:08x}_{miner_address[:8]}"
            else:
                job_id = f"job_{timestamp}_{self.job_counter:08x}"

            # Конвертируем в Stratum формат
            stratum_job = self._convert_to_stratum_job(template, job_id)

            # Сохраняем задание в job_service
            if miner_address:
                self.job_service.add_job(job_id, stratum_job, miner_address)
            else:
                # Для broadcast задания сохраняем как последнее общее
                self.job_service.set_last_broadcast_job(stratum_job)

            # Сохраняем локально для истории
            self.current_job = {
                "id": job_id,
                "template": template,
                "stratum_data": stratum_job,
                "created_at": datetime.now(UTC),
                "miner_address": miner_address
            }

            logger.info(
                "Создано новое задание",
                event="job_manager_job_created",
                job_id=job_id,
                miner_address=miner_address or "broadcast",
                height=template.get('height', 'unknown'),
                previous_hash=template.get('previousblockhash', '')[:16] + "...",
                coinbase_value=template.get('coinbasevalue', 0),
                job_counter=self.job_counter
            )

            return stratum_job

        except Exception as e:
            logger.error(
                "Ошибка при создании задания",
                event="job_manager_create_job_error",
                miner_address=miner_address,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def _convert_to_stratum_job(template: Dict, job_id: str) -> Dict:
        """Конвертировать шаблон блока в Stratum задание"""
        curtime = template.get("curtime", int(time.time()))
        ntime_hex = format(curtime, '08x')

        # Формируем Stratum сообщение mining.notify
        job_data = {
            "method": "mining.notify",
            "params": [
                job_id,  # Job ID
                template.get("previousblockhash", "0" * 64),  # prevhash
                "fdfd0800",  # coinb1 (часть coinbase транзакции)
                "",  # coinb2 (остальная часть coinbase)
                [],  # merkle_branch
                format(template.get("version", 0x20000000), '08x'),  # version
                template.get("bits", "1d00ffff"),  # nbits
                ntime_hex,  # ntime
                True  # clean_jobs
            ],
            "extra_nonce1": STRATUM_EXTRA_NONCE1  # Добавляем для валидатора
        }

        return job_data

    async def broadcast_new_job_to_all(self):
        """Рассылать новое задание всем подключенным майнерам"""
        # Создаем общее задание для всех майнеров
        job_data = await self.create_new_job()
        if not job_data:
            logger.warning("Не удалось создать задание для рассылки")
            return

        # Задание уже сохранено в job_service через create_new_job()
        # Рассылку делают серверы через job_service.get_job_for_miner()

        logger.info(f"Broadcast задание создано: {job_data['params'][0]}")

    async def send_job_to_miner(self, miner_address: str) -> bool:
        """Отправить персональное задание конкретному майнеру"""
        # Создаем персональное задание
        job_data = await self.create_new_job(miner_address)
        if not job_data:
            logger.warning(f"Не удалось создать задание для майнера {miner_address}")
            return False

        # Задание уже сохранено в job_service через create_new_job()
        logger.info(f"Персональное задание создано для майнера {miner_address}")
        return True

    @staticmethod
    async def validate_and_save_share(miner_address: str, share_data: Dict) -> Dict:
        """Валидация и сохранение шара - теперь делегируем job_service"""
        logger.info(f"Шар от майнера {miner_address}: job={share_data.get('job_id')}")

        # Большая часть валидации теперь делается в job_service и validator
        # Этот метод оставляем для совместимости и дополнительной логики

        return {
            "status": "accepted",
            "message": "Share accepted (delegated to job_service)",
            "difficulty": 1.0,
            "job_id": share_data.get('job_id'),
            "timestamp": datetime.now(UTC).isoformat()
        }

    @staticmethod
    async def submit_block_solution(miner_address: str, block_data: Dict) -> Dict:
        """Обработка найденного блока"""
        logger.info(f"БЛОК НАЙДЕН! Майнер: {miner_address}")

        # TODO: Реализовать обработку блока с использованием block_builder
        # Пока возвращаем заглушку

        return {
            "status": "rejected",
            "message": "Block processing not implemented yet",
            "miner": miner_address
        }

    def get_stats(self) -> Dict:
        """Получить статистику JobManager"""
        return {
            "status": "connected" if self.block_height > 0 else "disconnected",
            "current_job": self.current_job["id"] if self.current_job else None,
            "total_jobs_created": self.job_counter,
            "node_info": {
                "block_height": self.block_height,
                "difficulty": self.difficulty,
                "connection": f"{settings.bch_rpc_host}:{settings.bch_rpc_port}",
                "auth_method": "cookie" if settings.bch_rpc_use_cookie else "user/pass"
            }
        }

    async def get_current_difficulty(self) -> float:
        """Получить текущую сложность сети"""
        try:
            mining_info = await self.node_client.get_mining_info()
            if mining_info and 'difficulty' in mining_info:
                return float(mining_info['difficulty'])
            return self.difficulty
        except Exception as e:
            logger.error(f"Ошибка получения сложности: {e}")
            return self.difficulty