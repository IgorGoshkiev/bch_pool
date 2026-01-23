import asyncio
import json
from datetime import datetime, UTC
from typing import Dict, Optional

from app.utils.logging_config import StructuredLogger
from app.dependencies import auth_service, database_service, job_service
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE

logger = StructuredLogger(__name__)


class StratumTCPServer:
    """TCP Stratum сервер для ASIC майнеров"""

    def __init__(self, host: str = "0.0.0.0", port: int = 3333):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.connections: Dict[str, asyncio.StreamWriter] = {}
        self.miners: Dict[str, str] = {}  # client_id -> bch_address
        self._connection_times: Dict[str, datetime] = {}
        self.auth_service = auth_service
        self.database_service = database_service
        self.job_service = job_service
        self.start_time = datetime.now(UTC)
        self._lock = asyncio.Lock()  # Для синхронизации доступа
        self.max_connections = 1000  # Максимальное количество подключений

        logger.info(
            "TCP Stratum сервер инициализирован",
            event="tcp_server_initialized",
            host=host,
            port=port,
            start_time=self.start_time.isoformat()
        )

    async def start(self):
        """Запуск TCP сервера"""
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                self.host,
                self.port,
                reuse_port=True
            )

            addr = self.server.sockets[0].getsockname()
            logger.info(
                'TCP Stratum сервер запущен',
                event="tcp_server_started",
                host=addr[0],
                port=addr[1],
                address=f"{self.host}:{self.port}"
            )

            logger.info(
                'ASIC подключайтесь',
                event="tcp_server_ready",
                connection_string=f"stratum+tcp://{self.host}:{self.port}",
                protocol="stratum+tcp"
            )

            async with self.server:
                await self.server.serve_forever()

        except Exception as e:
            logger.error(
                'Ошибка запуска TCP сервера',
                event="tcp_server_start_failed",
                error=str(e),
                error_type=type(e).__name__,
                host=self.host,
                port=self.port
            )
            raise

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обработка подключения майнера"""
        addr = writer.get_extra_info('peername')
        client_id = f"{addr[0]}:{addr[1]}"

        # Проверка максимального количества подключений:
        async with self._lock:
            if len(self.connections) >= self.max_connections:
                logger.warning(
                    "Превышено максимальное количество подключений",
                    event="tcp_max_connections_reached",
                    client_id=client_id,
                    current_connections=len(self.connections),
                    max_connections=self.max_connections
                )
                writer.close()
                await writer.wait_closed()
                return


        # Записываем время подключения
        connect_time = datetime.now(UTC)

        async with self._lock:
            self._connection_times[client_id] = connect_time
            self.connections[client_id] = writer

        logger.info(
            'Новое TCP подключение',
            event="tcp_client_connected",
            client_id=client_id,
            remote_address=str(addr),
            connect_time=connect_time.isoformat(),
            total_connections=len(self.connections) + 1
        )

        try:
            # 1. Отправляем приветствие
            await self._send_welcome(writer)

            # 2. Обрабатываем входящие сообщения
            while True:
                try:
                    # Читаем строку (Stratum использует JSON-Line протокол)
                    data = await reader.readline()
                    if not data:
                        logger.info(
                            'Соединение закрыто клиентом',
                            event="tcp_client_disconnected",
                            client_id=client_id,
                            reason="client_closed"
                        )
                        break

                    # Декодируем JSON
                    try:
                        message = json.loads(data.decode().strip())
                        await self.handle_message(message, writer, client_id)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            'Невалидный JSON от клиента',
                            event="tcp_invalid_json",
                            client_id=client_id,
                            data_preview=data[:100].decode(errors='ignore'),
                            error=str(e)
                        )
                        await self._send_error(writer, None, f"Invalid JSON: {e}")

                except (ConnectionResetError, BrokenPipeError):
                    logger.info(
                        'Соединение разорвано',
                        event="tcp_connection_reset",
                        client_id=client_id,
                        reason="connection_reset"
                    )
                    break
                except Exception as e:
                    logger.error(
                        'Ошибка обработки сообщения',
                        event="tcp_message_error",
                        client_id=client_id,
                        error=str(e),
                        error_type=type(e).__name__
                    )

        except Exception as e:
            logger.error(
                'Критическая ошибка с клиентом',
                event="tcp_client_error",
                client_id=client_id,
                error=str(e),
                error_type=type(e).__name__
            )
        finally:
            # Получаем данные до очистки
            miner_address = None
            connection_duration = None

            async with self._lock:
                # Получаем информацию о майнере и времени подключения
                miner_address = self.miners.get(client_id)
                connect_time = self._connection_times.get(client_id)

                # Получаем количество оставшихся подключений ДО очистки
                remaining = len(self.connections) - 1 if client_id in self.connections else len(self.connections)

                # Очищаем все данные клиента
                self.miners.pop(client_id, None)
                self.connections.pop(client_id, None)
                self._connection_times.pop(client_id, None)

            # Рассчитываем длительность подключения
            if connect_time:
                connection_duration = (datetime.now(UTC) - connect_time).total_seconds()

            # Очищаем задания майнера если он был авторизован
            if miner_address:
                self.job_service.cleanup_miner_jobs(miner_address)

            # Закрываем соединение
            try:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
            except Exception as e:
                logger.warning(
                    'Ошибка при закрытии соединения',
                    event="tcp_close_error",
                    client_id=client_id,
                    error=str(e)
                )

            logger.info(
                'Клиент отключен',
                event="tcp_client_disconnected",
                client_id=client_id,
                miner_address=miner_address or "unauthorized",
                connection_duration_seconds=connection_duration,
                remaining_connections=remaining  # Используем предварительно рассчитанное значение
            )

    @staticmethod
    async def _send_welcome(writer: asyncio.StreamWriter):
        """Отправка приветственного сообщения"""
        welcome = {
            "id": 1,
            "result": {
                "version": "1.0.0",
                "protocol": "stratum",
                "motd": "Welcome to BCH Solo Pool (TCP)",
                "extensions": ["mining.set_difficulty", "mining.notify"],
                "difficulty": 1.0
            },
            "error": None
        }

        await StratumTCPServer._send_json(writer, welcome)

    async def handle_message(self, data: dict, writer: asyncio.StreamWriter, client_id: str):
        """Обработка Stratum сообщений"""
        method = data.get("method")
        msg_id = data.get("id")
        params = data.get("params", [])

        # Определяем BCH адрес майнера
        miner_address = self.miners.get(client_id)

        logger.debug(
            'TCP сообщение от клиента',
            event="tcp_message_received",
            client_id=client_id,
            miner_address=miner_address or "unauthorized",
            method=method,
            msg_id=msg_id,
            params_length=len(params)
        )

        if method == "mining.subscribe":
            await self._handle_subscribe(msg_id, writer)
            logger.debug(
                "Обработана TCP подписка",
                event="tcp_subscribe_handled",
                client_id=client_id
            )

        elif method == "mining.authorize":
            # В TCP протоколе адрес передается в параметрах
            if len(params) >= 1:
                username = params[0]

                # Авторизуем через auth_service
                success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")

                if not success:
                    logger.warning(
                        "Ошибка авторизации TCP клиента",
                        event="tcp_auth_failed",
                        client_id=client_id,
                        username=username,
                        error_message=error_msg
                    )
                    await self._send_error(writer, msg_id, error_msg or "Authorization failed")
                    return

                # Сохраняем адрес майнера
                async with self._lock:
                    self.miners[client_id] = authorized_address
                    miner_address = authorized_address

                # Отправляем успешный ответ
                response = {
                    "id": msg_id,
                    "result": True,
                    "error": None
                }
                await self._send_json(writer, response)

                logger.info(
                    "TCP клиент авторизован",
                    event="tcp_auth_success",
                    client_id=client_id,
                    miner_address=authorized_address,
                    username=username,
                    total_authorized_miners=len(self.miners)
                )

                # Отправляем первое задание
                await self.send_new_job_tcp(miner_address, writer)
            else:
                await self._send_error(writer, msg_id, "Invalid authorize parameters")

        elif method == "mining.submit":
            if miner_address:
                await self.handle_submit_tcp(msg_id, params, miner_address, writer, client_id)
            else:
                logger.warning(
                    "Неавторизованный submit",
                    event="tcp_submit_unauthorized",
                    client_id=client_id
                )
                await self._send_error(writer, msg_id, "Not authorized")
        else:
            logger.warning(
                "Неизвестный метод TCP",
                event="tcp_unknown_method",
                client_id=client_id,
                method=method
            )
            await self._send_error(writer, msg_id, f"Unknown method: {method}")

    @staticmethod
    async def _handle_subscribe(msg_id: int, writer: asyncio.StreamWriter):
        """Обработка подписки"""
        response = {
            "id": msg_id,
            "result": [
                [["mining.set_difficulty", "difficulty"], ["mining.notify", "job_id"]],
                STRATUM_EXTRA_NONCE1,  # Extra nonce 1
                EXTRA_NONCE2_SIZE  # Extra nonce 2 size
            ],
            "error": None
        }
        await StratumTCPServer._send_json(writer, response)

    async def handle_submit_tcp(self, msg_id: int, params: list, miner_address: str,
                                writer: asyncio.StreamWriter, client_id: str):
        """Обработка шара от TCP клиента"""
        if len(params) < 5:
            logger.warning(
                "Недостаточно параметров для TCP submit",
                event="tcp_submit_invalid_params",
                client_id=client_id,
                miner_address=miner_address,
                params_length=len(params)
            )
            await self._send_error(writer, msg_id, "Invalid submit parameters")
            return

        # worker_name = params[0]  # Не используется, но оставляем для совместимости
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(
            'Шар TCP от майнера',
            event="tcp_share_received",
            client_id=client_id,
            miner_address=miner_address,
            job_id=job_id,
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
                'Невалидный шар TCP',
                event="tcp_share_invalid",
                client_id=client_id,
                miner_address=miner_address,
                job_id=job_id,
                error_message=error_msg
            )
            await self._send_error(writer, msg_id, f"Invalid share: {error_msg}")
            return

        # Сохраняем в БД через database_service
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
                    'Ошибка сохранения TCP шара в БД',
                    event="tcp_share_save_failed",
                    client_id=client_id,
                    miner_address=miner_address,
                    job_id=job_id
                )
                await self._send_error(writer, msg_id, "Failed to save share to database")
                return

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await self._send_json(writer, response)

            logger.info(
                'Валидный шар принят (TCP)',
                event="tcp_share_accepted",
                client_id=client_id,
                miner_address=miner_address,
                share_id=share_id,
                job_id=job_id
            )

        except Exception as e:
            logger.error(
                'Ошибка сохранения шара TCP',
                event="tcp_share_save_error",
                client_id=client_id,
                miner_address=miner_address,
                error=str(e)
            )
            await self._send_error(writer, msg_id, f"Database error: {e}")

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        """Отправка задания TCP клиенту"""
        try:
            # Получаем задание из job_service
            job_data = self.job_service.get_job_for_miner(miner_address)

            if not job_data:
                logger.warning(
                    "Не удалось получить задание для TCP клиента",
                    event="tcp_job_not_found",
                    miner_address=miner_address
                )
                await self._send_error(writer, 0, "No job available")
                return

            # Отправляем задание клиенту
            await self._send_json(writer, job_data)

            logger.info(
                "Задание отправлено TCP клиенту",
                event="tcp_job_sent",
                miner_address=miner_address,
                job_id=job_data["params"][0] if "params" in job_data else "unknown"
            )

        except Exception as e:
            logger.error(
                "Ошибка отправки задания TCP",
                event="tcp_job_send_error",
                miner_address=miner_address,
                error=str(e)
            )
            await self._send_error(writer, 0, f"Job error: {str(e)}")

    async def broadcast_new_job(self, job_data: dict):
        """Рассылка нового задания всем TCP клиентам"""
        if not self.connections:
            logger.debug(
                "Нет активных TCP подключений для рассылки",
                event="tcp_broadcast_skipped",
                reason="no_connections"
            )
            return

        successful_sends = 0
        failed_sends = 0
        total_clients = len(self.connections)

        logger.info(
            "Начинаем рассылку задания TCP клиентам",
            event="tcp_broadcast_started",
            total_clients=total_clients
        )

        for client_id, writer in self.connections.items():
            miner_address = self.miners.get(client_id)
            if miner_address:
                try:
                    # Создаем персональную копию задания
                    job_data_copy = job_data.copy()
                    job_id = self.job_service.create_job_id(miner_address)
                    job_data_copy["params"][0] = job_id

                    # Сохраняем в job_service
                    self.job_service.add_job(job_id, job_data_copy, miner_address)

                    # Отправляем клиенту
                    await self._send_json(writer, job_data_copy)

                    successful_sends += 1

                except Exception as e:
                    failed_sends += 1
                    logger.error(
                        "Ошибка рассылки задания TCP клиенту",
                        event="tcp_broadcast_error",
                        client_id=client_id,
                        miner_address=miner_address,
                        error=str(e)
                    )

        if successful_sends > 0:
            logger.info(
                "Задание разослано TCP клиентам",
                event="tcp_broadcast_completed",
                successful_sends=successful_sends,
                failed_sends=failed_sends,
                total_clients=total_clients
            )
        else:
            logger.warning(
                "Не удалось разослать задание ни одному TCP клиенту",
                event="tcp_broadcast_failed",
                total_clients=total_clients
            )

    @staticmethod
    async def _send_error(writer: asyncio.StreamWriter, msg_id: Optional[int], error_msg: str):
        """Отправка ошибки"""
        response = {
            "id": msg_id if msg_id is not None else 0,
            "result": None,
            "error": [20, error_msg, None]
        }
        await StratumTCPServer._send_json(writer, response)

    @staticmethod
    async def _send_json(writer: asyncio.StreamWriter, data: dict):
        """Отправка JSON с новой строкой"""
        try:
            writer.write((json.dumps(data) + "\n").encode())
            await writer.drain()
        except Exception as e:
            logger.error(f'Ошибка отправки TCP: {e}')

    async def stop(self):
        """Остановка сервера"""
        if self.server:
            connections_before = len(self.connections)

            logger.info(
                'Остановка TCP Stratum сервера',
                event="tcp_server_stopping",
                active_connections=connections_before
            )

            self.server.close()
            await self.server.wait_closed()

            logger.info(
                'TCP Stratum сервер остановлен',
                event="tcp_server_stopped",
                was_running=True,
                connections_before=connections_before,
                uptime_seconds=(datetime.now(UTC) - self.start_time).total_seconds()
            )

    def get_stats(self) -> Dict:
        """Получение статистики сервера"""
        stats = {
            "host": self.host,
            "port": self.port,
            "active_connections": len(self.connections),
            "active_miners": len(self.miners),
            "protocol": "stratum+tcp",
            "uptime_seconds": (datetime.now(UTC) - self.start_time).total_seconds()
        }

        logger.debug(
            "Получение статистики TCP сервера",
            event="tcp_stats_requested",
            stats=stats
        )

        return stats

