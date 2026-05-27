import asyncio
import json
from datetime import datetime, UTC
from typing import Dict, Optional

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE
from app.utils.config import settings

logger = StructuredLogger(__name__)


class StratumTCPServer:
    """TCP Stratum сервер для ASIC майнеров"""

    def __init__(self,
                 host: str = "0.0.0.0",
                 port: int = 3333,
                 auth_service=None,
                 database_service=None,
                 job_service=None):
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
        self._ip_connections: Dict[str, int] = {}
        self.max_per_ip = 10

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
        print(f"🔌 NEW ASIC CONNECTION FROM: {addr}", flush=True)

        if addr is None:
            client_id = f"unknown_{id(writer)}"
        elif isinstance(addr, tuple) and len(addr) >= 2:
            client_id = f"{addr[0]}:{addr[1]}"
        else:
            client_id = f"unknown_{id(writer)}"

        print("=== NEW CLIENT CONNECTED ===", flush=True)
        logger.info("=== NEW CLIENT CONNECTED ===")
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

    async def _send_welcome(self, writer: asyncio.StreamWriter):
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

        await self._send_json(writer, welcome)

    async def handle_message(self, data: dict, writer: asyncio.StreamWriter, client_id: str):
        """Обработка Stratum сообщений - ПОЛНАЯ ВЕРСИЯ"""
        method = data.get("method")
        print(f"🔵 ENTER handle_message: method={method}", flush=True)
        msg_id = data.get("id")
        params = data.get("params", [])

        print(f"✅ RECEIVED: method={method}, id={msg_id}, params={params}", flush=True)
        logger.info(f"✅ RECEIVED: method={method}, id={msg_id}, params={params}")

        if method == "mining.subscribe":
            print(f"🔵 PROCESSING subscribe", flush=True)
            await self._handle_subscribe(msg_id, writer)

        elif method == "mining.authorize":
            print(f"🔵 PROCESSING authorize", flush=True)

            if len(params) >= 1:
                # ⚠️ ИСПРАВЛЕНИЕ: Если ASIC отправил адрес как две части - склеиваем
                username = params[0]
                if len(params) >= 2 and params[1] and ':' not in username and params[1].startswith('q'):
                    username = f"{params[0]}:{params[1]}"
                    print(f"🔵 FIXED USERNAME: {username}", flush=True)

                success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")
                if success:
                    async with self._lock:
                        self.miners[client_id] = authorized_address
                    response = {"id": msg_id, "result": True, "error": None}
                    await self._send_json(writer, response)
                    await self.send_new_job_tcp(authorized_address, writer)
                    logger.info(f"✅ AUTHORIZED: {username}")
                else:
                    await self._send_error(writer, msg_id, error_msg or "Authorization failed")
            else:
                await self._send_error(writer, msg_id, "Invalid authorize parameters")

        elif method == "mining.submit":
            if client_id in self.miners:
                await self.handle_submit_tcp(msg_id, params, self.miners[client_id], writer)
                logger.info(f"✅ SUBMIT RECEIVED: {params}")
            else:
                await self._send_error(writer, msg_id, "Not authorized")

        elif method == "mining.configure":
            await self._handle_configure(msg_id, writer, params)

        elif method == "mining.extranonce.subscribe":
            print(f"🔵 PROCESSING extranonce.subscribe", flush=True)
            await self._handle_extranonce_subscribe(msg_id, writer)

        elif method == "mining.suggest_difficulty":
            response = {"id": msg_id, "result": True, "error": None}
            await self._send_json(writer, response)

        else:
            await self._send_error(writer, msg_id, f"Unknown method: {method}")

    async def _handle_subscribe(self, msg_id: int, writer: asyncio.StreamWriter):
        """Обработка подписки с отправкой фиксированной сложности"""
        logger.info("=== START _handle_subscribe ===")

        # 1. Ответ на подписку
        response = {
            "id": msg_id,
            "result": [
                [["mining.set_difficulty", "difficulty"], ["mining.notify", "job_id"]],
                STRATUM_EXTRA_NONCE1,
                EXTRA_NONCE2_SIZE
            ],
            "error": None
        }
        await self._send_json(writer, response)
        logger.info("=== SUBSCRIBE RESPONSE SENT ===")

        # 2. ⚠️ ОТПРАВЛЯЕМ ФИКСИРОВАННУЮ СЛОЖНОСТЬ (как на molepool)
        difficulty_to_set = 1048576  # 1M - для среднего ASIC
        difficulty_msg = {
            "method": "mining.set_difficulty",
            "params": [difficulty_to_set]
        }
        await self._send_json(writer, difficulty_msg)
        logger.info(f"✅ Set difficulty to {difficulty_to_set}")

        # 3. ОТПРАВЛЯЕМ EXTRANONCE (если нужно)
        extranonce_msg = {
            "method": "mining.set_extranonce",
            "params": [STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE]
        }
        await self._send_json(writer, extranonce_msg)
        logger.info("✅ Extranonce sent")

    async def _handle_configure(self, msg_id: int, writer: asyncio.StreamWriter, params: list):
        """Обработка mining.configure от WhatsMiner"""
        print(f"🔵 ENTER _handle_configure", flush=True)
        logger.info(f"=== CONFIGURE REQUEST: {params} ===")

        response = {
            "id": msg_id,
            "result": {
                "version-rolling": True,
                "version-rolling.mask": "1fffe000",
                "minimum-difficulty": 1
            },
            "error": None
        }
        await self._send_json(writer, response)
        logger.info("=== CONFIGURE RESPONSE SENT ===")

    async def _handle_extranonce_subscribe(self, msg_id: int, writer: asyncio.StreamWriter):
        """Обработка extranonce.subscribe"""
        print(f"🔵 EXTRANONCE SUBSCRIBE - START", flush=True)

        response = {"id": msg_id, "result": True, "error": None}
        await self._send_json(writer, response)
        print(f"🔵 EXTRANONCE RESPONSE SENT", flush=True)

        # Ищем miner_address
        client_id = None
        for cid, w in self.connections.items():
            if w == writer:
                client_id = cid
                break
        print(f"🔵 client_id={client_id}, miners={list(self.miners.keys())}", flush=True)

        miner_address = "qr5zfhsh0cad3nhtc97d3zr29l9afhnl4shdj6dp34"
        if client_id and client_id in self.miners:
            miner_address = self.miners[client_id]

        print(f"📤 SENDING JOB TO: {miner_address}", flush=True)
        await self.send_new_job_tcp(miner_address, writer)

    async def _send_result(self, writer: asyncio.StreamWriter, msg_id: int, result):
        """Отправка простого результата"""
        response = {
            "id": msg_id,
            "result": result,
            "error": None
        }
        await self._send_json(writer, response)

    async def handle_submit_tcp(self, msg_id: int, params: list, miner_address: str,
                                writer: asyncio.StreamWriter):
        """Обработка шара от TCP клиента"""

        print(f"🔵🔵🔵 HANDLE_SUBMIT_TCP CALLED 🔵🔵🔵", flush=True)
        print(f"msg_id={msg_id}, miner={miner_address}, params={params}", flush=True)
        print(f"params length={len(params)}", flush=True)

        try:
            # 1. ПРОВЕРКА ПАРАМЕТРОВ
            if len(params) < 5:
                print(f"🔴 SUBMIT ERROR: not enough params (got {len(params)}, need 5)", flush=True)
                await self._send_error(writer, msg_id, "Invalid submit parameters")
                return

            # 3. ИЗВЛЕКАЕМ ДАННЫЕ
            # NOTE: params[0] - worker name (bitcoincash), params[1] - job_id, params[2] - extra_nonce2, params[3] - ntime, params[4] - nonce
            worker = params[0]
            job_id = params[1]
            extra_nonce2 = params[2]
            ntime = params[3]
            nonce = params[4]

            print(
                f"📊 PARAMS: job_id={job_id}, extra_nonce2={extra_nonce2}, ntime={ntime}, nonce={nonce}, worker={worker}",
                flush=True)

            # 4. ВАЛИДАЦИЯ
            print(f"🔍 enable_share_validation={settings.enable_share_validation}", flush=True)

            if settings.enable_share_validation:
                print(f"🔍 Calling validate_and_process_share...", flush=True)
                is_valid, error_msg, job_data = self.job_service.validate_and_process_share(
                    job_id=job_id,
                    extra_nonce2=extra_nonce2,
                    ntime=ntime,
                    nonce=nonce,
                    miner_address=miner_address
                )
                print(f"🔍 VALIDATION RESULT: is_valid={is_valid}, error_msg={error_msg}", flush=True)
            else:
                print(f"⚠️ VALIDATION DISABLED: accepting all shares", flush=True)
                is_valid = True
                error_msg = None

            # 5. ЕСЛИ НЕВАЛИДЕН - ОТКЛОНЯЕМ
            if not is_valid:
                print(f"🔴 SHARE REJECTED: {error_msg}", flush=True)
                await self._send_error(writer, msg_id, f"Invalid share: {error_msg}")
                return

            # 6. СОХРАНЯЕМ В БД
            print(f"💾 SAVING SHARE to database...", flush=True)
            saved, share_id = await self.database_service.save_share(
                miner_address=miner_address,
                job_id=job_id,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce,
                difficulty=settings.default_share_difficulty,
                is_valid=True
            )

            print(f"💾 SAVE RESULT: saved={saved}, share_id={share_id}", flush=True)

            if not saved:
                print(f"🔴 DATABASE SAVE FAILED", flush=True)
                await self._send_error(writer, msg_id, "Failed to save share to database")
                return

            # 7. ОТПРАВЛЯЕМ УСПЕХ
            response = {"id": msg_id, "result": True, "error": None}
            await self._send_json(writer, response)
            print(f"✅ SHARE ACCEPTED: share_id={share_id}", flush=True)

        except Exception as e:
            print(f"🔴🔴🔴 EXCEPTION IN HANDLE_SUBMIT_TCP: {e}", flush=True)
            import traceback
            traceback.print_exc()
            await self._send_error(writer, msg_id, f"Database error: {e}")

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        """Отправка задания TCP клиенту - УПРОЩЕННЫЙ ФОРМАТ"""
        try:
            job_data = self.job_service.get_job_for_miner(miner_address)

            if not job_data:
                print(f"🔴 NO JOB DATA", flush=True)
                await self._send_error(writer, 0, "No job available")
                return

            # Упрощаем params для ASIC
            params = job_data.get("params", [])
            if len(params) >= 9:
                # Убираем последний параметр (clean_jobs)
                simplified_params = params[:8]
            else:
                simplified_params = params

            simplified_job = {
                "method": "mining.notify",
                "params": simplified_params
            }

            print(f"📦 SIMPLIFIED JOB: {str(simplified_job)[:200]}", flush=True)
            await self._send_json(writer, simplified_job)
            print(f"✅ SIMPLIFIED JOB SENT", flush=True)

        except Exception as e:
            print(f"🔴 ERROR: {e}", flush=True)

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

    async def broadcast_difficulty(self, difficulty: float):
        """Рассылка обновления сложности всем TCP клиентам"""
        if not self.connections:
            logger.debug(
                "Нет активных TCP подключений для рассылки сложности",
                event="tcp_difficulty_broadcast_skipped",
                reason="no_connections"
            )
            return

        successful_sends = 0
        failed_sends = 0
        total_clients = len(self.connections)

        logger.info(
            "Начинаем рассылку обновления сложности TCP клиентам",
            event="tcp_difficulty_broadcast_started",
            total_clients=total_clients,
            difficulty=difficulty
        )

        method_data = {
            "method": "mining.set_difficulty",
            "params": [difficulty],
            "id": None  # Stratum протокол позволяет без ID для notification
        }

        for client_id, writer in self.connections.items():
            miner_address = self.miners.get(client_id, "unauthorized")
            try:
                await self._send_json(writer, method_data)
                successful_sends += 1

                logger.debug(
                    "Сложность отправлена TCP клиенту",
                    event="tcp_difficulty_sent",
                    client_id=client_id,
                    miner_address=miner_address,
                    difficulty=difficulty
                )

            except Exception as e:
                failed_sends += 1
                logger.error(
                    "Ошибка отправки сложности TCP клиенту",
                    event="tcp_difficulty_send_error",
                    client_id=client_id,
                    miner_address=miner_address,
                    error=str(e)
                )

        if successful_sends > 0:
            logger.info(
                "Сложность разослана TCP клиентам",
                event="tcp_difficulty_broadcast_completed",
                successful_sends=successful_sends,
                failed_sends=failed_sends,
                total_clients=total_clients,
                difficulty=difficulty
            )
        else:
            logger.warning(
                "Не удалось разослать сложность ни одному TCP клиенту",
                event="tcp_difficulty_broadcast_failed",
                total_clients=total_clients,
                difficulty=difficulty
            )

    async def update_miner_difficulty(self, miner_address: str, difficulty: float):
        """Обновление сложности для конкретного майнера"""
        client_id = None
        writer = None

        # Находим клиента по адресу майнера
        for cid, addr in self.miners.items():
            if addr == miner_address:
                client_id = cid
                writer = self.connections.get(cid)
                break

        if not writer or not client_id:
            logger.warning(
                "Майнер не найден для обновления сложности",
                event="tcp_miner_not_found_for_difficulty",
                miner_address=miner_address,
                difficulty=difficulty
            )
            return

        try:
            method_data = {
                "method": "mining.set_difficulty",
                "params": [difficulty],
                "id": None
            }

            await self._send_json(writer, method_data)

            logger.info(
                "Персональная сложность отправлена TCP майнеру",
                event="tcp_miner_difficulty_updated",
                client_id=client_id,
                miner_address=miner_address,
                difficulty=difficulty
            )

        except Exception as e:
            logger.error(
                "Ошибка отправки персональной сложности TCP майнеру",
                event="tcp_miner_difficulty_error",
                client_id=client_id,
                miner_address=miner_address,
                difficulty=difficulty,
                error=str(e)
            )

    async def _send_error(self, writer: asyncio.StreamWriter, msg_id: Optional[int], error_msg: str):
        """Отправка ошибки"""
        response = {
            "id": msg_id if msg_id is not None else 0,
            "result": None,
            "error": [20, error_msg, None]
        }
        await self._send_json(writer, response)

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict):
        """Отправка JSON с новой строкой"""
        try:
            msg = json.dumps(data) + "\n"
            # Показываем первые 500 символов отправляемого сообщения
            msg_preview = msg[:500] if len(msg) > 500 else msg
            print(f"📤 SENDING TO ASIC: {msg_preview}", flush=True)
            writer.write(msg.encode())
            await writer.drain()
        except Exception as e:
            print(f"🔴 SEND ERROR: {e}", flush=True)
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
