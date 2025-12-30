import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect, APIRouter
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


class StratumServer:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.miner_addresses: Dict[str, str] = {}  # websocket_id -> bch_address
        self.subscriptions: Dict[str, Set[str]] = {}  # miner_address -> job_ids

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
                self.subscriptions.pop(miner_address, None)

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

        # Проверяем, зарегистрирован ли майнер
        # Здесь можно добавить проверку в базу данных

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

        logger.info(f"Шар от {miner_address}: job={job_id}, nonce={nonce}")

        # Здесь будет проверка валидности шара
        # Пока просто принимаем всё

        response = {
            "id": msg_id,
            "result": True,
            "error": None
        }
        await websocket.send_json(response)

        # Обновляем статистику майнера в базе данных
        # await self.update_miner_stats(miner_address)

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
        job_id = f"job_{datetime.now(UTC).timestamp()}"

        # Здесь будет реальное задание из JobManager
        # Пока отправляем заглушку
        job = {
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

        await websocket.send_json(job)

        # Сохраняем подписку
        if miner_address in self.subscriptions:
            self.subscriptions[miner_address].add(job_id)

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

                await websocket.send_json(job_data_copy)

                if miner_address in self.subscriptions:
                    self.subscriptions[miner_address].add(job_id)


# Создаём экземпляр сервера
stratum_server = StratumServer()

# Создаём FastAPI роутер для WebSocket
#router = APIRouter()


#@router.websocket("/ws/{miner_address}")
#async def websocket_endpoint(websocket: WebSocket, miner_address: str):
#    """WebSocket endpoint для подключения майнеров"""
#    connection_id = await stratum_server.connect(websocket, miner_address)

#    try:
#        while True:
#            data = await websocket.receive_json()
#            await stratum_server.handle_message(websocket, connection_id, data)

#    except WebSocketDisconnect:
#        await stratum_server.disconnect(connection_id)
#    except Exception as e:
#        logger.error(f"Ошибка в WebSocket соединении: {e}")
#        await stratum_server.disconnect(connection_id)