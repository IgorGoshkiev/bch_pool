import asyncio
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime, UTC
import hashlib

logger = logging.getLogger(__name__)


class MockBCHNodeClient:
    """Мок-клиент BCH ноды для тестирования"""

    def __init__(self):
        self.block_height = 840000
        self.difficulty = 12345.6789

    async def get_block_template(self) -> Optional[Dict]:
        """Возвращает тестовый шаблон блока"""
        await asyncio.sleep(0.05)  # Небольшая задержка для реализма

        # Генерируем "случайный" предыдущий хэш
        timestamp = int(time.time())
        prev_hash_input = f"block_{self.block_height}_{timestamp}"
        prev_hash = hashlib.sha256(prev_hash_input.encode()).hexdigest()

        return {
            "previousblockhash": prev_hash,
            "coinbaseaux": {"flags": ""},
            "coinbasevalue": 625000000,  # 6.25 BCH в сатоши
            "longpollid": prev_hash + "999",
            "target": "00000000ffff0000000000000000000000000000000000000000000000000000",
            "mintime": timestamp - 7200,  # 2 часа назад
            "mutable": ["time", "transactions", "prevblock"],
            "noncerange": "00000000ffffffff",
            "sigoplimit": 80000,
            "sizelimit": 4000000,
            "curtime": timestamp,
            "bits": "1d00ffff",
            "height": self.block_height,
            "version": 0x20000000,
            "transactions": []
        }

    async def get_blockchain_info(self) -> Optional[Dict]:
        """Информация о блокчейне"""
        await asyncio.sleep(0.05)
        return {
            "chain": "test",
            "blocks": self.block_height,
            "headers": self.block_height,
            "difficulty": self.difficulty,
            "networkhashps": self.difficulty * 2 ** 32 / 600  # Примерный хэшрейт сети
        }

    async def submit_block(self, block_data: str) -> Optional[Dict]:
        """Имитация отправки блока"""
        await asyncio.sleep(0.1)
        logger.info(f"📤 [MOCK] Блок отправлен в сеть: {block_data[:64]}...")
        return {"status": "accepted"}


class JobManager:
    """Менеджер заданий для майнинг пула"""

    def __init__(self):
        self.node_client = MockBCHNodeClient()
        self.current_job = None
        self.job_history = []  # История заданий
        self.job_counter = 0
        self.stratum_server = None  # Будет установлено через set_stratum_server

    def set_stratum_server(self, stratum_server):
        """Установить ссылку на Stratum сервер"""
        self.stratum_server = stratum_server

    async def initialize(self) -> bool:
        """Инициализация менеджера"""
        try:
            # Проверяем "подключение" к ноде
            info = await self.node_client.get_blockchain_info()
            if info:
                logger.info(f"JobManager инициализирован. Высота блокчейна: {info.get('blocks')}")
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка инициализации JobManager: {e}")
            return False

    async def create_new_job(self, miner_address: str = None) -> Optional[Dict]:
        """Создать новое задание для майнера"""
        try:
            # Получаем шаблон блока
            template = await self.node_client.get_block_template()
            if not template:
                logger.warning("Не удалось получить шаблон блока")
                return None

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
            logger.debug(f"Предыдущий хэш: {template.get('previousblockhash', '')[:16]}...")
            logger.debug(f"Время: {template.get('curtime', 'unknown')}")

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
            return False

        # Отправляем задание
        websocket = self.stratum_server.active_connections[connection_id]
        await websocket.send_json(job_data)

        logger.info(f"Персональное задание отправлено майнеру {miner_address}")
        return True

    async def validate_and_save_share(self, miner_address: str, share_data: Dict) -> Dict:
        """Валидация и сохранение шара"""
        # Здесь будет реальная валидация хэшей
        # Пока просто логируем и "принимаем"

        logger.info(f"Шар от майнера {miner_address}: {share_data}")

        return {
            "status": "accepted",
            "message": "Share accepted (mock validation)",
            "difficulty": 1.0,
            "job_id": share_data.get("job_id", "unknown"),
            "timestamp": datetime.now(UTC).isoformat()
        }

    async def submit_block_solution(self, miner_address: str, block_data: Dict) -> Dict:
        """Обработка найденного блока"""
        logger.info(f"БЛОК НАЙДЕН! Майнер: {miner_address}")
        logger.info(f"Данные блока: {block_data}")

        # Имитация отправки в сеть
        result = await self.node_client.submit_block(str(block_data))

        if result and result.get("status") == "accepted":
            # "Увеличиваем" высоту блокчейна для следующего задания
            self.node_client.block_height += 1

            return {
                "status": "accepted",
                "message": "Block accepted by network (mock)",
                "miner": miner_address,
                "reward": 3.125,  # BCH 3.125 BCH
                "height": self.node_client.block_height
            }
        else:
            return {
                "status": "rejected",
                "message": "Block rejected (mock)",
                "miner": miner_address
            }

    def get_stats(self) -> Dict:
        """Получить статистику JobManager"""
        return {
            "current_job": self.current_job["id"] if self.current_job else None,
            "total_jobs_created": self.job_counter,
            "job_history_size": len(self.job_history),
            "node_info": {
                "block_height": self.node_client.block_height,
                "difficulty": self.node_client.difficulty
            }
        }


# Глобальный экземпляр JobManager
# job_manager = JobManager()
# используем dependencies.py