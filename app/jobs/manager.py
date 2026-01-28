import asyncio
import json
import time

from typing import Optional, Dict
from datetime import datetime, UTC

from app.utils.config import settings
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1
from app.utils.logging_config import StructuredLogger

from app.jobs.real_node_client import RealBCHNodeClient

from app.dependencies import job_service, block_builder

logger = StructuredLogger(__name__)


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
        self.job_service = job_service
        self.current_job = None
        self.job_counter = 0
        self.block_height = 0
        self.difficulty = 0.0

        logger.info(
            "JobManager инициализирован",
            event="job_manager_created",
            rpc_host=settings.bch_rpc_host,
            rpc_port=settings.bch_rpc_port,
            use_cookie=settings.bch_rpc_use_cookie
        )

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

            # Пробуем подключиться несколько раз
            max_retries = 3
            for attempt in range(max_retries):
                try:
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
                            init_time_ms=init_time,
                            connection_attempts=attempt + 1
                        )
                        return True
                    else:
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt  # Экспоненциальная задержка
                            logger.warning(
                                f"Попытка {attempt + 1} из {max_retries} не удалась, повтор через {wait_time} сек",
                                event="job_manager_retry",
                                attempt=attempt + 1,
                                max_retries=max_retries,
                                wait_time_seconds=wait_time
                            )
                            await asyncio.sleep(wait_time)
                except Exception as e:
                    logger.error(
                        f"Ошибка при попытке подключения {attempt + 1}",
                        event="job_manager_connection_error",
                        attempt=attempt + 1,
                        error=str(e)
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)

            # Все попытки исчерпаны
            init_time = (datetime.now(UTC) - init_start).total_seconds() * 1000
            logger.error(
                "Не удалось инициализировать JobManager после всех попыток",
                event="job_manager_init_failed",
                init_time_ms=init_time,
                rpc_url=f"{settings.bch_rpc_host}:{settings.bch_rpc_port}",
                max_retries=max_retries
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

            # Используем block_builder для создания Stratum задания
            stratum_job = await self._create_stratum_job_from_template(template, job_id, miner_address)

            if not stratum_job:
                logger.warning(
                    "Не удалось создать Stratum задание из шаблона",
                    event="job_manager_stratum_job_failed",
                    height=template.get('height', 'unknown')
                )
                return None


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

    async def _create_stratum_job_from_template(self, template: Dict, job_id: str, miner_address: str = None) -> \
    Optional[Dict]:
        """Создать Stratum задание из шаблона блока"""
        try:
            # Если есть адрес майнера, используем его, иначе адрес пула
            if miner_address:
                address = miner_address
            else:
                # публичный тестовый адрес из Bitcoin Cash документации
                address = settings.pool_wallet if settings.pool_wallet else "qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"

            # Используем block_builder для создания данных задания
            job_data = block_builder.create_stratum_job_data(
                template=template,
                job_id=job_id,
                miner_address=address,
                extra_nonce1=STRATUM_EXTRA_NONCE1
            )

            if not job_data:
                logger.warning(
                    "BlockBuilder не смог создать данные задания",
                    event="job_manager_block_builder_failed",
                    job_id=job_id
                )
                # Создаем fallback задание
                return self._create_fallback_stratum_job(template, job_id)

            return job_data

        except Exception as e:
            logger.error(
                "Ошибка создания Stratum задания из шаблона",
                event="job_manager_stratum_conversion_error",
                job_id=job_id,
                error=str(e)
            )
            return self._create_fallback_stratum_job(template, job_id)


    @staticmethod
    def _create_fallback_stratum_job(template: Dict, job_id: str) -> Dict:
        """Создать fallback Stratum задание"""
        curtime = template.get("curtime", int(time.time()))
        ntime_hex = format(curtime, '08x')

        return {
            "method": "mining.notify",
            "params": [
                job_id,
                template.get("previousblockhash", "0" * 64),
                "fdfd0800",  # coinb1
                "",  # coinb2
                [],  # merkle_branch
                format(template.get("version", 0x20000000), '08x'),
                template.get("bits", "1d00ffff"),
                ntime_hex,
                True
            ],
            "extra_nonce1": STRATUM_EXTRA_NONCE1,
            "template": template
        }



    async def broadcast_new_job_to_all(self):
        """Рассылать новое задание всем подключенным майнерам"""
        logger.info(
            "Начинаем рассылку задания всем майнерам",
            event="job_manager_broadcast_start"
        )

        # Создаем общее задание для всех майнеров
        job_data = await self.create_new_job()
        if not job_data:
            logger.warning(
                "Не удалось создать задание для рассылки",
                event="job_manager_broadcast_no_job"
            )
            return

        # Задание уже сохранено в job_service через create_new_job()
        # Рассылку делают серверы через job_service.get_job_for_miner()

        logger.info(
            "Broadcast задание создано",
            event="job_manager_broadcast_job_created",
            job_id=job_data['params'][0] if 'params' in job_data else 'unknown'
        )

    async def send_job_to_miner(self, miner_address: str) -> bool:
        """Отправить персональное задание конкретному майнеру"""
        logger.info(
            "Создание персонального задания для майнера",
            event="job_manager_personal_job_start",
            miner_address=miner_address
        )

        # Создаем персональное задание
        job_data = await self.create_new_job(miner_address)
        if not job_data:
            logger.warning(
                "Не удалось создать персональное задание",
                event="job_manager_personal_job_failed",
                miner_address=miner_address
            )
            return False

        # Задание уже сохранено в job_service через create_new_job()
        logger.info(
            "Персональное задание создано",
            event="job_manager_personal_job_created",
            miner_address=miner_address,
            job_id=job_data['params'][0] if 'params' in job_data else 'unknown'
        )
        return True

    @staticmethod
    async def validate_and_save_share(miner_address: str, share_data: Dict) -> Dict:
        """Валидация и сохранение шара - теперь делегируем job_service"""
        logger.info(
            "Валидация и сохранение шара",
            event="job_manager_validate_share_start",
            miner_address=miner_address,
            job_id=share_data.get('job_id', 'unknown'),
            share_id=share_data.get('share_id')
        )

        # Большая часть валидации теперь делается в job_service и validator
        # Этот метод оставляем для совместимости и дополнительной логики

        result = {
            "status": "accepted",
            "message": "Share accepted (delegated to job_service)",
            "difficulty": 1.0,
            "job_id": share_data.get('job_id'),
            "timestamp": datetime.now(UTC).isoformat()
        }

        logger.info(
            "Шар принят (делегировано job_service)",
            event="job_manager_share_accepted",
            miner_address=miner_address,
            job_id=share_data.get('job_id'),
            share_id=share_data.get('share_id')
        )

        return result

    async def submit_block_solution(self, miner_address: str, block_data: Dict) -> Dict:
        """Обработка найденного блока"""
        logger.info(
            "БЛОК НАЙДЕН! Обработка решения",
            event="job_manager_block_found",
            miner_address=miner_address,
            block_data_keys=list(block_data.keys()) if block_data else []
        )

        try:
            # Получаем необходимые данные
            job_id = block_data.get('job_id')
            extra_nonce2 = block_data.get('extra_nonce2')
            ntime = block_data.get('ntime')
            nonce = block_data.get('nonce')

            if not all([job_id, extra_nonce2, ntime, nonce]):
                return {
                    "status": "rejected",
                    "message": "Missing required block data",
                    "miner": miner_address
                }

            # Получаем шаблон задания из job_service
            job_data = self.job_service.get_job(job_id)
            if not job_data:
                return {
                    "status": "rejected",
                    "message": "Job not found",
                    "miner": miner_address,
                    "job_id": job_id
                }

            # Получаем шаблон блока из данных задания
            template = job_data.get('template')
            if not template:
                return {
                    "status": "rejected",
                    "message": "Template not found in job data",
                    "miner": miner_address
                }

            # Создаем полный блок
            complete_block = block_builder.create_complete_block(
                template=template,
                miner_address=miner_address,
                extra_nonce1=STRATUM_EXTRA_NONCE1,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce
            )

            if not complete_block:
                return {
                    "status": "rejected",
                    "message": "Failed to build complete block",
                    "miner": miner_address
                }

            logger.info(
                "Блок успешно собран",
                event="block_built_successfully",
                miner_address=miner_address,
                block_hash=complete_block.get('header_hash', '')[:16] + "...",
                height=complete_block.get('height')
            )

            # Отправляем блок в BCH ноду
            submit_result = await self.node_client.submit_block(complete_block['block_hex'])

            if submit_result and submit_result.get("status") == "accepted":
                return {
                    "status": "accepted",
                    "message": "Block solution accepted and submitted to node",
                    "miner": miner_address,
                    "block_hash": complete_block.get('header_hash'),
                    "height": complete_block.get('height'),
                    "node_response": submit_result
                }
            else:
                error_msg = submit_result.get("message", "Unknown error") if submit_result else "Node submission failed"
                return {
                    "status": "rejected",
                    "message": f"Node rejected block: {error_msg}",
                    "miner": miner_address,
                    "block_hash": complete_block.get('header_hash')
                }

        except Exception as e:
            logger.error(
                "Ошибка обработки блока",
                event="job_manager_block_processing_error",
                miner_address=miner_address,
                error=str(e),
                error_type=type(e).__name__
            )

            return {
                "status": "rejected",
                "message": f"Block processing error: {str(e)}",
                "miner": miner_address
            }

    def get_stats(self) -> Dict:
        """Получить статистику JobManager"""
        stats = {
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

        logger.debug(
            "Получение статистики JobManager",
            event="job_manager_get_stats",
            block_height=self.block_height,
            job_counter=self.job_counter,
            has_current_job=self.current_job is not None
        )

        return stats

    async def get_current_difficulty(self) -> float:
        """Получить текущую сложность сети"""
        try:
            logger.debug(
                "Запрос текущей сложности сети",
                event="job_manager_get_difficulty"
            )

            mining_info = await self.node_client.get_mining_info()
            if mining_info and 'difficulty' in mining_info:
                difficulty = float(mining_info['difficulty'])

                logger.debug(
                    "Получена сложность сети",
                    event="job_manager_difficulty_received",
                    difficulty=difficulty
                )
                return difficulty

            logger.debug(
                "Используется сохраненная сложность",
                event="job_manager_using_cached_difficulty",
                difficulty=self.difficulty
            )
            return self.difficulty

        except Exception as e:
            logger.error(
                "Ошибка получения сложности сети",
                event="job_manager_get_difficulty_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return self.difficulty
