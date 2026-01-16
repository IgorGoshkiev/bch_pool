import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket


from app.services.auth_service import AuthService
from app.services.database_service import DatabaseService
from app.services.job_service import JobService
from app.jobs.manager import JobManager

logger = logging.getLogger(__name__)


class StratumServer:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.miner_addresses: Dict[str, str] = {}  # websocket_id -> bch_address
        self.subscriptions: Dict[str, Set[str]] = {}  # miner_address -> job_ids
        self.current_job_id = None
        self.auth_service = AuthService()
        self.database_service = DatabaseService()
        self.job_service = JobService()
        self.job_manager = JobManager()

        logger.info("WebSocket Stratum сервер инициализирован")

    async def connect(self, websocket: WebSocket, miner_address: str):
        """Подключение майнера"""
        await websocket.accept()

        connection_id = str(id(websocket))
        self.active_connections[connection_id] = websocket
        self.miner_addresses[connection_id] = miner_address
        self.subscriptions[miner_address] = set()

        logger.info(f"Майнер {miner_address} подключился (ID: {connection_id})")

        # Отправляем приветственное сообщение
        await self._send_welcome(websocket)

        return connection_id

    async def disconnect(self, connection_id: str):
        """Отключение майнера"""
        if connection_id in self.active_connections:
            miner_address = self.miner_addresses.get(connection_id)

            # Удаляем из всех словарей
            self.active_connections.pop(connection_id, None)
            self.miner_addresses.pop(connection_id, None)

            if miner_address:
                # Очищаем подписки майнера
                self.subscriptions.pop(miner_address, None)

                # Очищаем задания майнера в job_service
                self.job_service.cleanup_miner_jobs(miner_address)

            logger.info(f"Майнер {miner_address} отключился (ID: {connection_id})")

    @staticmethod
    async def _send_welcome(websocket: WebSocket):
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
                await self._handle_subscribe(websocket, msg_id)
            elif method == "mining.authorize":
                await self.handle_authorize(websocket, msg_id, params)
            elif method == "mining.submit":
                await self.handle_submit(websocket, msg_id, params, miner_address)
            elif method == "mining.get_transactions":
                await self._handle_get_transactions(websocket, msg_id)
            else:
                await self._send_error(websocket, msg_id, f"Unknown method: {method}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения от {miner_address}: {e}")
            await self._send_error(websocket, data.get("id"), str(e))

    @staticmethod
    async def _handle_subscribe(websocket: WebSocket, msg_id: int):
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

    async def handle_authorize(self, websocket: WebSocket, msg_id: int, params: list):
        """Авторизация майнера"""
        if len(params) < 2:
            await self._send_error(websocket, msg_id, "Invalid parameters")
            return

        username = params[0]
        # password = params[1]  # Не используется, но оставляем для совместимости протокола

        # Используем auth_service для авторизации
        success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")

        if not success:
            await self._send_error(websocket, msg_id, error_msg or "Authorization failed")
            return

        # Обновляем адрес майнера в маппинге
        connection_id = str(id(websocket))
        self.miner_addresses[connection_id] = authorized_address

        # Отправляем успешный ответ
        response = {
            "id": msg_id,
            "result": True,
            "error": None
        }
        await websocket.send_json(response)

        logger.info(f"Майнер авторизован: {authorized_address}")

        # После успешной авторизации отправляем первое задание
        await self.send_new_job(websocket, authorized_address)

    async def handle_submit(self, websocket: WebSocket, msg_id: int, params: list, miner_address: str):
        """Обработка найденного решения (шара)"""
        if len(params) < 5:
            await self._send_error(websocket, msg_id, "Invalid submit parameters")
            return

        worker_name = params[0]
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(f"Шар от {miner_address}: job={job_id}")

        # Используем job_service для валидации
        is_valid, error_msg, job_data = self.job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2=extra_nonce2,
            ntime=ntime,
            nonce=nonce,
            miner_address=miner_address
        )

        if not is_valid:
            logger.warning(f"Невалидный шар от {miner_address}: {error_msg}")
            await self._send_error(websocket, msg_id, f"Invalid share: {error_msg}")
            return

        # Шар валиден - сохраняем в БД через database_service
        try:
            saved, share_id = await self.database_service.save_share(
                miner_address=miner_address,
                job_id=job_id,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce,
                difficulty=1.0,
                is_valid=True
            )

            if not saved:
                await self._send_error(websocket, msg_id, "Failed to save share to database")
                return

            # Также передаем в JobManager для обработки (если нужно)
            share_data = {
                "job_id": job_id,
                "worker_name": worker_name,
                "extra_nonce2": extra_nonce2,
                "ntime": ntime,
                "nonce": nonce,
                "miner_address": miner_address,
                "share_id": share_id
            }

            # Передаем шар в JobManager для дальнейшей обработки
            result = await self.job_manager.validate_and_save_share(miner_address, share_data)

            if result.get("status") != "accepted":
                logger.warning(f"JobManager отклонил шар: {result.get('message')}")
                await self._send_error(websocket, msg_id, result.get("message", "Share rejected"))
                return

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await websocket.send_json(response)

            logger.info(f"Валидный шар принят от {miner_address}, ID={share_id}")

        except Exception as e:
            logger.error(f"Ошибка при сохранении шара: {e}")
            await self._send_error(websocket, msg_id, f"Database error: {str(e)}")

    @staticmethod
    async def _handle_get_transactions(websocket: WebSocket, msg_id: int):
        """Получение транзакций для блока"""
        response = {
            "id": msg_id,
            "result": [],
            "error": None
        }
        await websocket.send_json(response)

    async def send_new_job(self, websocket: WebSocket, miner_address: str):
        """Отправка нового задания майнеру"""
        logger.info(f"Запрос задания для майнера {miner_address}")

        try:
            # Получаем задание из job_service
            job_data = self.job_service.get_job_for_miner(miner_address)

            if not job_data:
                logger.warning(f"Не удалось получить задание для {miner_address}")
                await self._send_error(websocket, 0, "No job available")  # msg_id = 0 для внутренних ошибок
                return

            # Отправляем задание майнеру
            await websocket.send_json(job_data)

            # Добавляем в подписки майнера
            job_id = job_data["params"][0]
            if miner_address in self.subscriptions:
                self.subscriptions[miner_address].add(job_id)
            else:
                self.subscriptions[miner_address] = {job_id}

            logger.info(f"Задание отправлено майнеру {miner_address}: {job_id}")

        except Exception as e:
            logger.error(f"Ошибка при отправке задания майнеру {miner_address}: {e}")
            await self._send_error(websocket, 0, f"Job error: {str(e)}")

    @staticmethod
    async def _send_error(websocket: WebSocket, msg_id: Optional[int], error_msg: str):
        """Отправка ошибки"""
        error_response = {
            "id": msg_id if msg_id is not None else 0,
            "result": None,
            "error": [20, error_msg, None]  # Код 20 - другие ошибки
        }
        await websocket.send_json(error_response)

    async def broadcast_new_job(self, job_data: dict):
        """Рассылка нового задания всем подключенным майнерам"""
        if not self.active_connections:
            logger.debug("Нет активных подключений для рассылки")
            return

        successful_sends = 0

        for connection_id, websocket in self.active_connections.items():
            miner_address = self.miner_addresses.get(connection_id)
            if miner_address:
                try:
                    # Создаем персональную копию задания
                    job_data_copy = job_data.copy()
                    job_id = self.job_service.create_job_id(miner_address)
                    job_data_copy["params"][0] = job_id

                    # Сохраняем в job_service
                    self.job_service.add_job(job_id, job_data_copy, miner_address)

                    # Отправляем майнеру
                    await websocket.send_json(job_data_copy)

                    # Обновляем подписки
                    if miner_address in self.subscriptions:
                        self.subscriptions[miner_address].add(job_id)
                    else:
                        self.subscriptions[miner_address] = {job_id}

                    successful_sends += 1

                except Exception as e:
                    logger.error(f"Ошибка рассылки задания майнеру {miner_address}: {e}")

        if successful_sends > 0:
            logger.info(f"Задание разослано {successful_sends} майнерам")
        else:
            logger.warning("Не удалось разослать задание ни одному майнеру")

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий из локальных подписок"""
        # Очистка делается через job_service
        self.job_service.cleanup_old_jobs(max_age_seconds)

        # Также очищаем локальные подписки для несуществующих заданий
        for miner_address in list(self.subscriptions.keys()):
            valid_jobs = set()
            for job_id in self.subscriptions[miner_address]:
                if self.job_service.get_job(job_id) is not None:
                    valid_jobs.add(job_id)

            if valid_jobs:
                self.subscriptions[miner_address] = valid_jobs
            else:
                # Если нет валидных заданий, удаляем запись
                self.subscriptions.pop(miner_address, None)

    async def update_difficulty(self, difficulty: float):
        """Обновление сложности для всех майнеров"""
        for connection_id, websocket in self.active_connections.items():
            try:
                difficulty_msg = {
                    "method": "mining.set_difficulty",
                    "params": [difficulty]
                }
                await websocket.send_json(difficulty_msg)
            except Exception as e:
                logger.error(f"Ошибка отправки сложности майнеру: {e}")

        logger.info(f"Сложность обновлена до {difficulty}")

    def get_stats(self) -> dict:
        """Получение статистики сервера"""
        return {
            "active_connections": len(self.active_connections),
            "active_miners": len(set(self.miner_addresses.values())),
            "subscriptions": len(self.subscriptions),
            "total_subscriptions": sum(len(jobs) for jobs in self.subscriptions.values())
        }

    def cleanup_all(self):
        """Полная очистка всех данных"""
        self.active_connections.clear()
        self.miner_addresses.clear()
        self.subscriptions.clear()

        logger.info("WebSocket сервер очищен")