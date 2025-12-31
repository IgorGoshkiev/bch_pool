import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect, APIRouter
from datetime import datetime, UTC

from app.stratum.validator import share_validator  # Импортируем валидатор
from app.models.database import get_db  # Для работы с БД
from app.models.miner import Miner
from app.models.share import Share
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class StratumServer:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.miner_addresses: Dict[str, str] = {}  # websocket_id -> bch_address
        self.subscriptions: Dict[str, Set[str]] = {}  # miner_address -> job_ids
        self.jobs: Dict[str, dict] = {}  # job_id -> job_data
        self.current_job_id = None

    async def connect(self, websocket: WebSocket, miner_address: str):
        """Подключение майнера"""
        await websocket.accept()

        connection_id = str(id(websocket))
        self.active_connections[connection_id] = websocket
        self.miner_addresses[connection_id] = miner_address
        self.subscriptions[miner_address] = set()

        logger.info(f"Майнер {miner_address} подключился (ID: {connection_id})")

        # Отправляем приветственное сообщение
        await self.send_welcome(websocket, miner_address)

        return connection_id

    async def disconnect(self, connection_id: str):
        """Отключение майнера"""
        if connection_id in self.active_connections:
            miner_address = self.miner_addresses.get(connection_id)

            # Удаляем из всех словарей
            self.active_connections.pop(connection_id, None)
            self.miner_addresses.pop(connection_id, None)

            if miner_address:
                # Удаляем все задания майнера из валидатора
                miner_jobs = self.subscriptions.pop(miner_address, set())
                for job_id in miner_jobs:
                    share_validator.remove_job(job_id)

            logger.info(f"Майнер {miner_address} отключился (ID: {connection_id})")

    async def send_welcome(self, websocket: WebSocket, miner_address: str):
        """Отправляем приветственное сообщение майнеру"""
        welcome_message = {
            "id": 1,
            "result": {
                "version": "1.0.0",
                "protocol": "stratum",
                "motd": "Welcome to BCH Solo Pool",
                "extensions": ["mining.set_difficulty", "mining.notify"],
                "difficulty": 1.0
            },
            "error": None
        }
        await websocket.send_json(welcome_message)

    async def handle_message(self, websocket: WebSocket, connection_id: str, data: dict):
        """Обработка входящих сообщений от майнера"""
        miner_address = self.miner_addresses.get(connection_id, "unknown")

        try:
            method = data.get("method")
            params = data.get("params", [])
            msg_id = data.get("id")

            logger.debug(f"Сообщение от {miner_address}: method={method}, id={msg_id}")

            if method == "mining.subscribe":
                await self.handle_subscribe(websocket, msg_id, params)
            elif method == "mining.authorize":
                await self.handle_authorize(websocket, msg_id, params, miner_address)
            elif method == "mining.submit":
                await self.handle_submit(websocket, msg_id, params, miner_address)
            elif method == "mining.get_transactions":
                await self.handle_get_transactions(websocket, msg_id)
            else:
                await self.send_error(websocket, msg_id, f"Unknown method: {method}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения от {miner_address}: {e}")
            await self.send_error(websocket, data.get("id"), str(e))

    async def handle_subscribe(self, websocket: WebSocket, msg_id: int, params: list):
        """Обработка подписки майнера"""
        response = {
            "id": msg_id,
            "result": [
                [["mining.set_difficulty", "difficulty"], ["mining.notify", "job_id"]],
                "ae6812eb4cd7735a302a8a9dd95cf71f",  # Extra nonce 1
                4  # Extra nonce 2 size
            ],
            "error": None
        }
        await websocket.send_json(response)

    async def handle_authorize(self, websocket: WebSocket, msg_id: int, params: list, miner_address: str):
        """Авторизация майнера"""
        if len(params) < 2:
            await self.send_error(websocket, msg_id, "Invalid parameters")
            return

        username = params[0]
        password = params[1]  # В реальном пуле здесь проверка пароля


        # TODO: Реальная проверка майнера в БД
        # Пока просто проверяем формат адреса
        if not miner_address or len(miner_address) < 10:
            await self.send_error(websocket, msg_id, "Invalid miner address")
            return

        response = {
            "id": msg_id,
            "result": True,
            "error": None
        }
        await websocket.send_json(response)

        # После успешной авторизации отправляем первое задание
        await self.send_new_job(websocket, miner_address)

    async def handle_submit(self, websocket: WebSocket, msg_id: int, params: list, miner_address: str):
        """Обработка найденного решения (шара)"""
        if len(params) < 5:
            await self.send_error(websocket, msg_id, "Invalid submit parameters")
            return

        worker_name = params[0]
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(f"Шар от {miner_address}: job={job_id}, extra_nonce2={extra_nonce2}, ntime={ntime}, nonce={nonce}")

        # Валидация шара
        is_valid, error_msg = share_validator.validate_share(
            job_id=job_id,
            extra_nonce2=extra_nonce2,
            ntime=ntime,
            nonce=nonce,
            miner_address=miner_address
        )

        if not is_valid:
            logger.warning(f"Невалидный шар от {miner_address}: {error_msg}")
            await self.send_error(websocket, msg_id, f"Invalid share: {error_msg}")
            return

        # Шар валиден - сохраняем в БД и удаляем задание из кэша если clean_jobs=True
        try:
            await self.save_share_to_db(miner_address, job_id, is_valid=True)

            # Если задание было с флагом clean_jobs=True, удаляем его
            # TODO: проверять флаг clean_jobs из задания
            share_validator.remove_job(job_id)

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await websocket.send_json(response)

            logger.info(f"Валидный шар принят от {miner_address}")

        except Exception as e:
            logger.error(f"Ошибка при сохранении шара: {e}")
            await self.send_error(websocket, msg_id, f"Database error: {str(e)}")

    async def save_share_to_db(self, miner_address: str, job_id: str, is_valid: bool = True):
        """Сохранение шара в базу данных"""
        # TODO: Реализовать реальное сохранение с учетом сложности
        # Пока заглушка
        logger.debug(f"Сохранение шара в БД: miner={miner_address}, job={job_id}, valid={is_valid}")

        # Пример сохранения (нужно будет реализовать с сессией БД)
        # async with AsyncSessionLocal() as session:
        #     share = Share(
        #         miner_address=miner_address,
        #         job_id=job_id,
        #         difficulty=1.0,
        #         is_valid=is_valid
        #     )
        #     session.add(share)
        #     await session.commit()

    async def handle_get_transactions(self, websocket: WebSocket, msg_id: int):
        """Получение транзакций для блока"""
        response = {
            "id": msg_id,
            "result": [],
            "error": None
        }
        await websocket.send_json(response)

    async def send_new_job(self, websocket: WebSocket, miner_address: str):
        """Отправка нового задания майнеру"""
        job_id = f"job_{datetime.now(UTC).timestamp()}_{miner_address[:8]}"

        # TODO: Получать реальное задание от JobManager
        # Пока отправляем тестовое задание
        job_data = {
            "method": "mining.notify",
            "params": [
                job_id,  # Job ID
                "0000000000000000000000000000000000000000000000000000000000000000",  # prevhash
                "0000000000000000000000000000000000000000000000000000000000000000",  # coinb1
                "0000000000000000000000000000000000000000000000000000000000000000",  # coinb2
                [],  # merkle_branch
                "0000000000000000000000000000000000000000000000000000000000000000",  # version
                "00000000",  # nbits
                "00000000",  # ntime
                True  # clean_jobs
            ]
        }

        # Сохраняем задание в кэше валидатора
        share_validator.add_job(job_id, job_data)

        # Сохраняем в локальном кэше
        self.jobs[job_id] = job_data
        self.current_job_id = job_id

        # Добавляем в подписки майнера
        if miner_address in self.subscriptions:
            self.subscriptions[miner_address].add(job_id)
        else:
            self.subscriptions[miner_address] = {job_id}

        await websocket.send_json(job_data)
        logger.info(f"Отправлено задание {job_id} майнеру {miner_address}")

    async def send_error(self, websocket: WebSocket, msg_id: int, error_msg: str):
        """Отправка ошибки"""
        error_response = {
            "id": msg_id,
            "result": None,
            "error": [20, error_msg, None]  # Код 20 - другие ошибки
        }
        await websocket.send_json(error_response)

    async def broadcast_new_job(self, job_data: dict):
        """Рассылка нового задания всем подключенным майнерам"""
        for connection_id, websocket in self.active_connections.items():
            miner_address = self.miner_addresses.get(connection_id)
            if miner_address:
                job_data_copy = job_data.copy()
                # Добавляем уникальный job_id для каждого майнера
                job_id = f"job_{datetime.now(UTC).timestamp()}_{connection_id}"
                job_data_copy["params"][0] = job_id

                # Сохраняем в валидаторе
                share_validator.add_job(job_id, job_data_copy)
                # Сохраняем в локальном кэше
                self.jobs[job_id] = job_data_copy

                await websocket.send_json(job_data_copy)

                if miner_address in self.subscriptions:
                    self.subscriptions[miner_address].add(job_id)
                else:
                    self.subscriptions[miner_address] = {job_id}

        logger.info(f"Задание разослано {len(self.active_connections)} майнерам")

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий (старше 5 минут)"""
        current_time = datetime.now(UTC)
        jobs_to_remove = []

        for job_id, job_data in self.jobs.items():
            # Извлекаем timestamp из job_id
            try:
                # job_id format: "job_{timestamp}_{suffix}"
                timestamp_str = job_id.split('_')[1]
                job_time = datetime.fromtimestamp(float(timestamp_str), UTC)

                age = (current_time - job_time).total_seconds()
                if age > max_age_seconds:
                    jobs_to_remove.append(job_id)

            except (IndexError, ValueError):
                # Если не можем распарсить timestamp, удаляем
                jobs_to_remove.append(job_id)

        # Удаляем старые задания
        for job_id in jobs_to_remove:
            # Удаляем из всех мест
            self.jobs.pop(job_id, None)
            share_validator.remove_job(job_id)

            # Удаляем из подписок майнеров
            for miner_address in self.subscriptions:
                if job_id in self.subscriptions[miner_address]:
                    self.subscriptions[miner_address].remove(job_id)

        if jobs_to_remove:
            logger.info(f"Очищено {len(jobs_to_remove)} старых заданий")


    async def update_difficulty(self, difficulty: float):
        """Обновление сложности для всех майнеров"""
        for connection_id, websocket in self.active_connections.items():
            difficulty_msg = {
                "method": "mining.set_difficulty",
                "params": [difficulty]
            }
            await websocket.send_json(difficulty_msg)

        logger.info(f"Сложность обновлена до {difficulty}")

    def get_stats(self) -> dict:
        """Получение статистики сервера"""
        return {
            "active_connections": len(self.active_connections),
            "active_miners": len(set(self.miner_addresses.values())),
            "total_jobs": len(self.jobs),
            "subscriptions": len(self.subscriptions)
        }

    def cleanup_all(self):
        """Полная очистка всех данных"""
        self.active_connections.clear()
        self.miner_addresses.clear()
        self.subscriptions.clear()
        self.jobs.clear()

        # Очищаем валидатор
        for job_id in list(share_validator.jobs_cache.keys()):
            share_validator.remove_job(job_id)

        logger.info("Все данные сервера очищены")


# Создаём экземпляр сервера
stratum_server = StratumServer()
