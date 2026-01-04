import asyncio
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime, UTC
import hashlib

from app.utils.config import settings
from app.jobs.real_node_client import RealBCHNodeClient

logger = logging.getLogger(__name__)


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
        self.current_job = None
        self.job_history = []  # История заданий
        self.job_counter = 0
        self.stratum_server = None  # Будет установлено через set_stratum_server
        self.block_height = 0
        self.difficulty = 0.0

    def set_stratum_server(self, stratum_server):
        """Установить ссылку на Stratum сервер"""
        self.stratum_server = stratum_server

    async def initialize(self) -> bool:
        """Инициализация менеджера с реальной нодой"""
        try:
            logger.info(f"Подключение к BCH ноде: {settings.bch_rpc_host}:{settings.bch_rpc_port}")

            if await self.node_client.connect():
                # Обновляем локальные переменные из клиента
                self.block_height = self.node_client.block_height
                self.difficulty = self.node_client.difficulty

                # Логируем успешное подключение
                logger.info(f"✅ JobManager инициализирован. Высота блокчейна: {self.block_height}")
                logger.info(
                    f"   Цепочка: {self.node_client.blockchain_info.get('chain', 'unknown') if hasattr(self.node_client, 'blockchain_info') else 'unknown'}")
                logger.info(f"   Сложность: {self.difficulty}")
                return True
            else:
                logger.error("Не удалось подключиться к BCH ноде")
                logger.error("Проверьте:")
                logger.error("1. Запущена ли нода на сервере")
                logger.error("2. Настройки RPC в bitcoin.conf")
                logger.error("3. Доступность порта 28332")
                if settings.bch_rpc_use_cookie:
                    logger.error("4. Существование .cookie файла")
                return False

        except Exception as e:
            logger.error(f"Ошибка инициализации JobManager: {e}")
            return False

    async def create_new_job(self, miner_address: str = None) -> Optional[Dict]:
        """Создать новое задание для майнера"""
        try:
            # Получаем шаблон блока от реальной ноды
            template = await self.node_client.get_block_template()
            if not template:
                logger.warning("Не удалось получить шаблон блока от ноды")
                return None

            # Обновляем высоту блока
            if 'height' in template:
                self.block_height = template['height']
                self.node_client.block_height = template['height']

            # Создаем уникальный ID задания
            self.job_counter += 1
            timestamp = int(time.time())

            if miner_address:
                job_id = f"job_{timestamp}_{self.job_counter:08x}_{miner_address[:8]}"
            else:
                job_id = f"job_{timestamp}_{self.job_counter:08x}"

            # Конвертируем в Stratum формат
            stratum_job = self._convert_to_stratum_job(template, job_id)

            # Сохраняем задание
            self.current_job = {
                "id": job_id,
                "template": template,
                "stratum_data": stratum_job,
                "created_at": datetime.now(UTC),
                "miner_address": miner_address
            }

            # Добавляем в историю (ограничиваем размер)
            self.job_history.append(self.current_job)
            if len(self.job_history) > 100:
                self.job_history = self.job_history[-100:]

            logger.info(f"Создано задание {job_id} для майнера {miner_address or 'broadcast'}")
            logger.debug(f"Высота: {template.get('height', 'unknown')}")
            logger.debug(f"Предыдущий хэш: {template.get('previousblockhash', '')[:16]}...")
            logger.debug(f"Время: {template.get('curtime', 'unknown')}")
            logger.debug(f"Coinbase: {template.get('coinbasevalue', 0)} сатоши")

            return stratum_job

        except Exception as e:
            logger.error(f"Ошибка при создании задания: {e}")
            return None

    def _convert_to_stratum_job(self, template: Dict, job_id: str) -> Dict:
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
            ]
        }

        return job_data

    async def broadcast_new_job_to_all(self):
        """Рассылать новое задание всем подключенным майнерам"""
        if not self.stratum_server:
            logger.warning("Stratum сервер не установлен в JobManager")
            return

        # Создаем общее задание для всех майнеров
        job_data = await self.create_new_job()
        if not job_data:
            logger.warning("Не удалось создать задание для рассылки")
            return

        # Рассылаем через Stratum сервер
        await self.stratum_server.broadcast_new_job(job_data)

        active_miners = len(set(self.stratum_server.miner_addresses.values()))
        logger.info(f"Задание разослано {active_miners} активным майнерам")

    async def send_job_to_miner(self, miner_address: str) -> bool:
        """Отправить персональное задание конкретному майнеру"""
        if not self.stratum_server:
            return False

        # Находим соединение майнера
        connection_id = None
        for conn_id, addr in self.stratum_server.miner_addresses.items():
            if addr == miner_address:
                connection_id = conn_id
                break

        if not connection_id or connection_id not in self.stratum_server.active_connections:
            logger.warning(f"Майнер {miner_address} не найден среди активных соединений")
            return False

        # Создаем задание для этого майнера
        job_data = await self.create_new_job(miner_address)
        if not job_data:
            logger.warning(f"Не удалось создать задание для майнера {miner_address}")
            return False

        # Отправляем задание
        websocket = self.stratum_server.active_connections[connection_id]
        await websocket.send_json(job_data)

        logger.info(f"Персональное задание отправлено майнеру {miner_address}")
        return True

    async def validate_and_save_share(self, miner_address: str, share_data: Dict) -> Dict:
        """Валидация и сохранение шара"""
        logger.info(f"Шар от майнера {miner_address}: job={share_data.get('job_id')}")

        # TODO: Реальная валидация хэшей будет здесь
        # Пока логируем и "принимаем"

        return {
            "status": "accepted",
            "message": "Share accepted (реальная валидация скоро)",
            "difficulty": 1.0,
            "job_id": share_data.get("job_id", "unknown"),
            "timestamp": datetime.now(UTC).isoformat()
        }

    async def submit_block_solution(self, miner_address: str, block_data: Dict) -> Dict:
        """Обработка найденного блока"""
        logger.info(f"БЛОК НАЙДЕН! Майнер: {miner_address}")

        # TODO: Собрать реальный блок из данных
        # Пока отправляем тестовые данные
        hex_data = block_data.get('hex_data', '')

        if not hex_data:
            logger.error("Нет данных блока для отправки")
            return {
                "status": "rejected",
                "message": "No block data provided",
                "miner": miner_address
            }

        # Отправляем блок в реальную сеть
        result = await self.node_client.submit_block(hex_data)

        if result and result.get("status") == "accepted":
            logger.info(f"Блок принят сетью! Награда: 3.125 BCH")

            # Обновляем высоту блока
            self.block_height += 1
            self.node_client.block_height = self.block_height

            return {
                "status": "accepted",
                "message": "Block accepted by network",
                "miner": miner_address,
                "reward": 3.125,  # BCH награда за блок в тестнете
                "height": self.block_height
            }
        else:
            error_msg = result.get("message", "Unknown error") if result else "RPC error"
            logger.error(f"Блок отклонен: {error_msg}")
            return {
                "status": "rejected",
                "message": f"Block rejected: {error_msg}",
                "miner": miner_address
            }

    def get_stats(self) -> Dict:
        """Получить статистику JobManager"""
        return {
            "status": "connected" if self.block_height > 0 else "disconnected",
            "current_job": self.current_job["id"] if self.current_job else None,
            "total_jobs_created": self.job_counter,
            "job_history_size": len(self.job_history),
            "node_info": {
                "block_height": self.block_height,
                "difficulty": self.difficulty,
                "connection": f"{settings.bch_rpc_host}:{settings.bch_rpc_port}",
                "auth_method": "cookie" if settings.bch_rpc_use_cookie else "user/pass"
            }
        }