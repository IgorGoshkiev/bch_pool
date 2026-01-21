from typing import Dict, Set, Optional, Any
from datetime import datetime, UTC
from fastapi import WebSocket

from app.utils.logging_config import StructuredLogger
from app.dependencies import auth_service, database_service, job_service
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE

logger = StructuredLogger("stratum_ws")


class StratumServer:
    def __init__(self, job_manager: Optional[Any] = None):
        self.active_connections: Dict[str, WebSocket] = {}
        self.miner_addresses: Dict[str, str] = {}  # websocket_id -> bch_address
        self.subscriptions: Dict[str, Set[str]] = {}  # miner_address -> job_ids
        self.current_job_id = None
        self.auth_service = auth_service
        self.database_service = database_service
        self.job_service = job_service
        self.job_manager = job_manager
        self.start_time = datetime.now(UTC)
        self._connection_times: Dict[str, datetime] = {}

        logger.info(
            "WebSocket Stratum сервер инициализирован",
            event="stratum_ws_initialized",
            start_time=self.start_time.isoformat(),
            has_job_manager=job_manager is not None
        )

    async def connect(self, websocket: WebSocket, miner_address: str):
        """Подключение майнера"""
        await websocket.accept()

        connection_id = str(id(websocket))
        client_ip = websocket.client.host if websocket.client else "unknown"

        self.active_connections[connection_id] = websocket
        self.miner_addresses[connection_id] = miner_address
        self.subscriptions[miner_address] = set()
        self._connection_times[connection_id] = datetime.now(UTC)

        logger.info(
            f"Майнер {miner_address} подключился",
            event="miner_connected",
            connection_id=connection_id,
            miner_address=miner_address,
            client_ip=client_ip,
            total_connections=len(self.active_connections),
            connection_type="websocket"
        )

        # Отправляем приветственное сообщение
        await self._send_welcome(websocket)

        return connection_id

    async def disconnect(self, connection_id: str):
        """Отключение майнера"""
        if connection_id in self.active_connections:
            miner_address = self.miner_addresses.get(connection_id)
            connection_duration = None

            # Рассчитываем длительность подключения если есть информация
            if connection_id in self._connection_times:
                start_time = self._connection_times[connection_id]
                connection_duration = (datetime.now(UTC) - start_time).total_seconds()
                self._connection_times.pop(connection_id, None)

            # Удаляем из всех словарей
            self.active_connections.pop(connection_id, None)
            self.miner_addresses.pop(connection_id, None)

            if miner_address:
                # Очищаем подписки майнера
                subscriptions_count = len(self.subscriptions.get(miner_address, set()))
                self.subscriptions.pop(miner_address, None)

                # Очищаем задания майнера в job_service
                self.job_service.cleanup_miner_jobs(miner_address)

                logger.info(
                    f"Майнер {miner_address} отключился",
                    event="miner_disconnected",
                    connection_id=connection_id,
                    miner_address=miner_address,
                    connection_duration_seconds=connection_duration,
                    subscriptions_count=subscriptions_count,
                    remaining_connections=len(self.active_connections)
                )
            else:
                logger.warning(
                    "Отключение без miner_address",
                    event="miner_disconnected_no_address",
                    connection_id=connection_id,
                    connection_duration_seconds=connection_duration
                )

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

            logger.debug(
                f"Сообщение от {miner_address}",
                event="stratum_message_received",
                connection_id=connection_id,
                miner_address=miner_address,
                method=method,
                msg_id=msg_id,
                params_length=len(params)
            )

            if method == "mining.subscribe":
                await self._handle_subscribe(websocket, msg_id)
                logger.debug(
                    "Обработана подписка",
                    event="stratum_subscribe_handled",
                    connection_id=connection_id,
                    miner_address=miner_address
                )
            elif method == "mining.authorize":
                await self.handle_authorize(websocket, msg_id, params, connection_id)
            elif method == "mining.submit":
                await self.handle_submit(websocket, msg_id, params, miner_address, connection_id)
            elif method == "mining.get_transactions":
                await self._handle_get_transactions(websocket, msg_id)
                logger.debug(
                    "Обработан запрос транзакций",
                    event="stratum_get_transactions_handled",
                    connection_id=connection_id
                )
            else:
                logger.warning(
                    f"Неизвестный метод: {method}",
                    event="stratum_unknown_method",
                    connection_id=connection_id,
                    method=method
                )
                await self._send_error(websocket, msg_id, f"Unknown method: {method}")

        except Exception as e:
            logger.error(
                f"Ошибка обработки сообщения от {miner_address}",
                event="stratum_message_error",
                connection_id=connection_id,
                miner_address=miner_address,
                error=str(e),
                error_type=type(e).__name__,
                data_received=str(data)[:200]  # Логируем первые 200 символов
            )
            await self._send_error(websocket, data.get("id"), f"Internal error: {str(e)}")

    @staticmethod
    async def _handle_subscribe(websocket: WebSocket, msg_id: int):
        """Обработка подписки майнера"""
        response = {
            "id": msg_id,
            "result": [
                [["mining.set_difficulty", "difficulty"], ["mining.notify", "job_id"]],
                STRATUM_EXTRA_NONCE1,  # Extra nonce 1
                EXTRA_NONCE2_SIZE  # Extra nonce 2 size
            ],
            "error": None
        }
        await websocket.send_json(response)

    async def handle_authorize(self, websocket: WebSocket, msg_id: int, params: list, connection_id: str):
        """Авторизация майнера"""
        if len(params) < 2:
            logger.warning(
                "Недостаточно параметров для авторизации",
                event="stratum_auth_invalid_params",
                connection_id=connection_id,
                params_length=len(params)
            )
            await self._send_error(websocket, msg_id, "Invalid parameters")
            return

        username = params[0]
        # password = params[1]  # Не используется, но оставляем для совместимости протокола

        # Используем auth_service для авторизации
        success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")

        if not success:
            logger.warning(
                "Ошибка авторизации майнера",
                event="stratum_auth_failed",
                connection_id=connection_id,
                username=username,
                error_message=error_msg
            )
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

        logger.info(
            f"Майнер авторизован: {authorized_address}",
            event="stratum_auth_success",
            connection_id=connection_id,
            miner_address=authorized_address,
            username=username
        )

        # После успешной авторизации отправляем первое задание
        await self.send_new_job(websocket, authorized_address)


    async def handle_submit(self, websocket: WebSocket, msg_id: int, params: list, miner_address: str, connection_id: str):
        """Обработка найденного решения (шара)"""
        if len(params) < 5:
            logger.warning(
                "Недостаточно параметров для submit",
                event="stratum_submit_invalid_params",
                connection_id=connection_id,
                miner_address=miner_address,
                params_length=len(params)
            )
            await self._send_error(websocket, msg_id, "Invalid submit parameters")
            return

        worker_name = params[0]
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(
            f"Шар от {miner_address}",
            event="stratum_share_received",
            connection_id=connection_id,
            miner_address=miner_address,
            job_id=job_id,
            worker_name=worker_name,
            extra_nonce2_length=len(extra_nonce2),
            nonce=nonce
        )

        # Используем job_service для валидации
        is_valid, error_msg, job_data = self.job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2=extra_nonce2,
            ntime=ntime,
            nonce=nonce,
            miner_address=miner_address
        )

        if not is_valid:
            logger.warning(
                f"Невалидный шар от {miner_address}",
                event="stratum_share_invalid",
                connection_id=connection_id,
                miner_address=miner_address,
                job_id=job_id,
                error_message=error_msg,
                validation_failed=True
            )
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
                logger.error(
                    "Ошибка сохранения шара в БД",
                    event="stratum_share_save_failed",
                    connection_id=connection_id,
                    miner_address=miner_address,
                    job_id=job_id
                )
                await self._send_error(websocket, msg_id, "Failed to save share to database")
                return

            # Также передаем в JobManager для обработки (если нужно)
            # ПРОВЕРЯЕМ, что job_manager установлен
            if not self.job_manager:
                logger.error(
                    "JobManager не установлен в StratumServer",
                    event="stratum_job_manager_missing",
                    connection_id=connection_id
                )
                # Можно сохранить шар без передачи в JobManager
                response = {
                    "id": msg_id,
                    "result": True,
                    "error": None
                }
                await websocket.send_json(response)
                logger.info(
                    f"Валидный шар принят от {miner_address}",
                    event="stratum_share_accepted_no_job_manager",
                    connection_id=connection_id,
                    miner_address=miner_address,
                    share_id=share_id,
                    job_id=job_id
                )
                return

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
                logger.warning(
                    "JobManager отклонил шар",
                    event="stratum_share_rejected_by_job_manager",
                    connection_id=connection_id,
                    miner_address=miner_address,
                    job_id=job_id,
                    rejection_reason=result.get("message", "Unknown")
                )
                await self._send_error(websocket, msg_id, result.get("message", "Share rejected"))
                return

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await websocket.send_json(response)

            logger.info(
                f"Валидный шар принят от {miner_address}",
                event="stratum_share_accepted",
                connection_id=connection_id,
                miner_address=miner_address,
                share_id=share_id,
                job_id=job_id,
                saved_to_db=saved
            )

        except Exception as e:
            logger.error(
                "Ошибка при сохранении шара",
                event="stratum_share_save_error",
                connection_id=connection_id,
                miner_address=miner_address,
                error=str(e),
                error_type=type(e).__name__
            )
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
        logger.info(
            f"Запрос задания для майнера {miner_address}",
            event="stratum_job_requested",
            miner_address=miner_address
        )

        try:
            # Получаем задание из job_service
            job_data = self.job_service.get_job_for_miner(miner_address)

            if not job_data:
                logger.warning(
                    f"Не удалось получить задание для {miner_address}",
                    event="stratum_job_not_found",
                    miner_address=miner_address
                )
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

            logger.info(
                f"Задание отправлено майнеру {miner_address}",
                event="stratum_job_sent",
                miner_address=miner_address,
                job_id=job_id,
                total_subscriptions=len(self.subscriptions.get(miner_address, set()))
            )

        except Exception as e:
            logger.error(
                f"Ошибка при отправке задания майнеру {miner_address}",
                event="stratum_job_send_error",
                miner_address=miner_address,
                error=str(e)
            )
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
            logger.debug(
                "Нет активных подключений для рассылки",
                event="stratum_broadcast_skipped",
                reason="no_active_connections"
            )
            return

        successful_sends = 0
        failed_sends = 0
        total_miners = len(self.active_connections)

        logger.info(
            f"Начинаем рассылку задания {len(self.active_connections)} майнерам",
            event="stratum_broadcast_started",
            total_miners=total_miners
        )

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
                    failed_sends += 1
                    logger.error(
                        f"Ошибка рассылки задания майнеру {miner_address}",
                        event="stratum_broadcast_error",
                        connection_id=connection_id,
                        miner_address=miner_address,
                        error=str(e)
                    )

        if successful_sends > 0:
            logger.info(
                f"Рассылка задания завершена",
                event="stratum_broadcast_completed",
                successful_sends=successful_sends,
                failed_sends=failed_sends,
                total_miners=total_miners,
                success_rate=f"{successful_sends / total_miners * 100:.1f}%" if total_miners > 0 else "0%"
            )
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
        stats = {
            "active_connections": len(self.active_connections),
            "active_miners": len(set(self.miner_addresses.values())),
            "subscriptions": len(self.subscriptions),
            "total_subscriptions": sum(len(jobs) for jobs in self.subscriptions.values()),
            "uptime_seconds": (datetime.now(UTC) - self.start_time).total_seconds()
        }

        logger.debug(
            "Получение статистики WebSocket сервера",
            event="stratum_stats_requested",
            stats=stats
        )

        return stats

    def cleanup_all(self):
        """Полная очистка всех данных"""
        connections_before = len(self.active_connections)
        miners_before = len(self.miner_addresses)
        subscriptions_before = len(self.subscriptions)

        self.active_connections.clear()
        self.miner_addresses.clear()
        self.subscriptions.clear()

        logger.info(
            "WebSocket сервер очищен",
            event="stratum_cleanup_all",
            connections_before=connections_before,
            miners_before=miners_before,
            subscriptions_before=subscriptions_before
        )