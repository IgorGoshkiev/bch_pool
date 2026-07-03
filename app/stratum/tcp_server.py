import asyncio
import json
import time
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
                 job_manager=None,
                 job_service=None,
                 difficulty_service=None,
                 share_validator=None):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.connections: Dict[str, asyncio.StreamWriter] = {}
        self.miners: Dict[str, str] = {}  # client_id -> bch_address
        self._connection_times: Dict[str, datetime] = {}
        self.auth_service = auth_service
        self.database_service = database_service
        self.job_service = job_service
        self.job_manager = job_manager
        self.difficulty_service = difficulty_service
        self.share_validator = share_validator
        self.miner_difficulties: Dict[str, float] = {}  # Хранилище сложности
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
        """Обработка Stratum сообщений - ДОБАВЛЯЕМ suggest_difficulty"""
        method = data.get("method")
        msg_id = data.get("id")
        params = data.get("params", [])

        print(f"✅ RECEIVED: method={method}, id={msg_id}", flush=True)

        if method == "mining.subscribe":
            await self._handle_subscribe(msg_id, writer)

        elif method == "mining.configure":
            await self._handle_configure(msg_id, writer, params)

        elif method == "mining.authorize":
            if len(params) >= 1:
                username = params[0]

                # Собираем полный username если он разделен на две части
                if len(params) >= 2 and params[1] and ':' not in username and params[1].startswith('q'):
                    username = f"{params[0]}:{params[1]}"
                print(f"✅ *************  username (combined): {username}", flush=True)

                success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")
                if success:
                    async with self._lock:
                        self.miners[client_id] = authorized_address
                        self.miner_difficulties[authorized_address] = 1.0

                    # 1. Ответ на авторизацию
                    response = {"id": msg_id, "result": True, "error": None}
                    await self._send_json(writer, response)
                    print(f"✅ AUTHORIZED: {username} -> {authorized_address}", flush=True)

                    # 2. ОТПРАВЛЯЕМ СЛОЖНОСТЬ
                    initial_diff = int(getattr(settings, 'default_share_difficulty', 65536))
                    print(f"✅ ОТПРАВЛЯЕМ СЛОЖНОСТЬ initial_diff:  -> {initial_diff}", flush=True)
                    self.share_validator.pool_difficulty = initial_diff

                    difficulty_msg = {
                        "method": "mining.set_difficulty",
                        "params": [initial_diff],
                        "id": None
                    }
                    await self._send_json(writer, difficulty_msg)
                    print(f"📊 SENT INITIAL DIFFICULTY: {initial_diff}", flush=True)

                    # 3. ОТПРАВЛЯЕМ ЗАДАНИЕ
                    await self.send_new_job_tcp(authorized_address, writer)
                    print(f"📤 SENT INITIAL JOB TO: {authorized_address}", flush=True)

                else:
                    await self._send_error(writer, msg_id, error_msg or "Authorization failed")
            else:
                await self._send_error(writer, msg_id, "Invalid authorize parameters")

        elif method == "mining.suggest_difficulty":
            if params and len(params) >= 1:
                suggested = float(params[0])
                print(f"📊 ASIC suggested difficulty: {suggested}", flush=True)

                # ТОЛЬКО ПОДТВЕРЖДАЕМ ЗАПРОС ASIC
                response = {"id": msg_id, "result": True, "error": None}
                await self._send_json(writer, response)
                print(f"📊 Confirmed suggest_difficulty: {suggested}", flush=True)
            else:
                await self._send_error(writer, msg_id, "Invalid suggest_difficulty parameters")

        elif method == "mining.extranonce.subscribe":
            await self._handle_extranonce_subscribe(msg_id, writer)

        elif method == "mining.submit":
            if client_id in self.miners:
                await self.handle_submit_tcp(msg_id, params, self.miners[client_id], writer)
            else:
                await self._send_error(writer, msg_id, "Not authorized")

        else:
            await self._send_error(writer, msg_id, f"Unknown method: {method}")

    async def _handle_subscribe(self, msg_id: int, writer: asyncio.StreamWriter):
        """Обработка подписки"""
        logger.info("=== START _handle_subscribe ===")

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

        # Отправляем extranonce
        extranonce_msg = {
            "method": "mining.set_extranonce",
            "params": [STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE]
        }
        await self._send_json(writer, extranonce_msg)
        logger.info("✅ Extranonce sent")
        print(f"📤 SENT EXTRANONCE: {STRATUM_EXTRA_NONCE1}", flush=True)

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

        miner_address = None
        if client_id and client_id in self.miners:
            miner_address = self.miners[client_id]

        if miner_address is None:
            print(f"⚠️ No authorized miner for client {client_id}, skipping job", flush=True)
            return

        print(f"📤 SENDING JOB TO: {miner_address}", flush=True)

        # 1. Отправляем задание этому майнеру
        await self.send_new_job_tcp(miner_address, writer)

        # 2. Дополнительный broadcast для всех майнеров
        if self.job_manager:
            await self.job_manager.broadcast_new_job_to_all()
            print(f"📤 BROADCAST NEW JOB TO ALL MINERS", flush=True)

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

            # 2. ИЗВЛЕКАЕМ ДАННЫЕ
            worker = params[0]
            job_id = params[1]
            extra_nonce2 = params[2]
            ntime = params[3]
            nonce = params[4]

            print(
                f"📊 PARAMS: job_id={job_id}, extra_nonce2={extra_nonce2}, ntime={ntime}, nonce={nonce}, worker={worker}",
                flush=True)

            # 3. РАСЧЕТ ХЭША И СЛОЖНОСТИ ШАРА
            share_difficulty = None
            try:
                job_data = self.job_service.get_job(job_id)
                if job_data and self.share_validator:
                    hash_result = self.share_validator.calculate_hash(
                        job_data, extra_nonce2, ntime, nonce
                    )
                    print(f"🔥 SHARE HASH: {hash_result}", flush=True)

                    hash_int = int(hash_result, 16)
                    if hash_int > 0:
                        # Берем TARGET из валидатора (динамический)
                        target_for_diff_1 = self.share_validator.TARGET_FOR_DIFFICULTY_1
                        share_difficulty = target_for_diff_1 // hash_int
                        print(f"🔥 SHARE DIFFICULTY: {share_difficulty}", flush=True)
                    else:
                        share_difficulty = 0
                        print(f"🔥 WARNING: hash_int is 0, cannot calculate difficulty", flush=True)
                else:
                    print(f"🔥 JOB NOT FOUND or no validator: {job_id}", flush=True)
            except Exception as e:
                print(f"🔥 ERROR calculating hash: {e}", flush=True)

            # 4. ВАЛИДАЦИЯ
            print(f"🔍 enable_share_validation={settings.enable_share_validation}", flush=True)

            if settings.enable_share_validation:
                print(f"🔍 Calling validate_and_process_share...", flush=True)
                is_valid, error_msg, extra_data = self.job_service.validate_and_process_share(
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
                extra_data = None

            # 5. ЕСЛИ НЕВАЛИДЕН - ОТКЛОНЯЕМ
            if not is_valid:
                print(f"🔴 SHARE REJECTED: {error_msg}", flush=True)
                await self._send_error(writer, msg_id, f"Invalid share: {error_msg}")
                return

            # 6. ОПРЕДЕЛЯЕМ СЛОЖНОСТЬ ДЛЯ СОХРАНЕНИЯ
            # Используем сложность шара, если она рассчитана, иначе fallback
            difficulty_to_save = share_difficulty if share_difficulty is not None else settings.default_share_difficulty
            print(f"💾 SAVING SHARE with difficulty: {difficulty_to_save}", flush=True)

            saved, share_id = await self.database_service.save_share(
                miner_address=miner_address,
                job_id=job_id,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce,
                difficulty=difficulty_to_save,
                is_valid=True
            )

            print(f"💾 SAVE RESULT: saved={saved}, share_id={share_id}", flush=True)

            if not saved:
                print(f"🔴 DATABASE SAVE FAILED", flush=True)
                await self._send_error(writer, msg_id, "Failed to save share to database")
                return

            # 7. ПРОВЕРЯЕМ, НЕ НАЙДЕН ЛИ БЛОК (ИСПОЛЬЗУЕМ extra_data)
            if extra_data and extra_data.get('is_valid_block', False):
                print(f"🎉🎉🎉 BLOCK FOUND! Отправляем в ноду...", flush=True)

                # Отправляем блок через job_service
                block_result = await self.job_service.process_found_block(
                    miner_address=miner_address,
                    job_id=job_id,
                    extra_nonce2=extra_nonce2,
                    ntime=ntime,
                    nonce=nonce,
                    hash_result=extra_data.get('hash_result', '')
                )

                if block_result.get("status") == "accepted":
                    print(f"✅ BLOCK ACCEPTED BY NODE! hash={block_result.get('block_hash', '')[:16]}...",
                          flush=True)
                else:
                    print(f"🔴 BLOCK REJECTED: {block_result.get('message')}", flush=True)

            # 8. ОТПРАВЛЯЕМ УСПЕХ
            response = {"id": msg_id, "result": True, "error": None}
            await self._send_json(writer, response)
            print(f"✅ SHARE ACCEPTED: share_id={share_id}", flush=True)

            # 9. АДАПТИВНАЯ СЛОЖНОСТЬ
            if self.difficulty_service and is_valid:
                # Используем ту же сложность для расчета динамической сложности
                await self.difficulty_service.add_share(miner_address, difficulty_to_save)
                new_difficulty = await self.difficulty_service.calculate_difficulty_for_miner(miner_address)

                current_difficulty = self.miner_difficulties.get(miner_address, 1.0)
                if abs(new_difficulty - current_difficulty) > 0.1:
                    await self.update_miner_difficulty(miner_address, new_difficulty)
                    self.miner_difficulties[miner_address] = new_difficulty
                    print(f"📊 DIFFICULTY UPDATED: {current_difficulty} -> {new_difficulty}", flush=True)

        except Exception as e:
            print(f"🔴🔴🔴 EXCEPTION IN HANDLE_SUBMIT_TCP: {e}", flush=True)
            import traceback
            traceback.print_exc()
            await self._send_error(writer, msg_id, f"Error processing share: {e}")

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        try:
            if self.job_manager is None:
                print("🔴 JOB_MANAGER IS NONE!", flush=True)
                return

            print(f"🔍 SEND_JOB: getting job for {miner_address}", flush=True)
            job_data = await self.job_manager.create_new_job(miner_address)

            if not job_data:
                print("🔴 Failed to create real job from node", flush=True)
                return

            print(f"🔍 SEND_JOB: job_data keys = {job_data.keys()}", flush=True)
            print(f"🔍 SEND_JOB: params length = {len(job_data['params'])}", flush=True)

            def reverse_hash(hash_str: str) -> str:
                if len(hash_str) != 64:
                    return hash_str
                return ''.join(reversed([hash_str[i:i + 2] for i in range(0, 64, 2)]))

            real_prevhash = job_data['params'][1]
            real_prevhash_le = reverse_hash(real_prevhash)
            print(f"🔍 SEND_JOB: real_prevhash (original) = {real_prevhash[:32]}...", flush=True)
            print(f"🔍 SEND_JOB: real_prevhash_le = {real_prevhash_le[:32]}...", flush=True)

            real_coinb1 = job_data['params'][2]
            real_coinb2 = job_data['params'][3]
            real_merkle_branch = job_data['params'][4]
            real_version = job_data['params'][5]
            real_bits = job_data['params'][6]
            real_ntime = job_data['params'][7]

            print(f"🔍 SEND_JOB: coinb1 length = {len(real_coinb1)}", flush=True)
            print(f"🔍 SEND_JOB: coinb2 length = {len(real_coinb2)}", flush=True)
            print(f"🔍 SEND_JOB: merkle_branch length = {len(real_merkle_branch)}", flush=True)
            print(f"🔍 SEND_JOB: bits = {real_bits}, ntime = {real_bits}", flush=True)
            print(f"🔍 SEND_JOB: ntime = {real_ntime}, ntime = {real_ntime}", flush=True)

            job_id = f"{int(time.time()) & 0xFFFF:04x}"

            real_job = {
                "method": "mining.notify",
                "params": [
                    job_id,
                    real_prevhash_le,
                    real_coinb1,
                    real_coinb2,
                    real_merkle_branch,
                    real_version,
                    real_bits,
                    real_ntime,
                    True
                ]
            }

            self.job_service.add_job(job_id, real_job, miner_address)

            job_data_for_send = {
                "method": real_job["method"],
                "params": real_job["params"]
            }
            await self._send_json(writer, job_data_for_send)

            print(f"✅ REAL JOB SENT: id={job_id}, merkle_len={len(real_merkle_branch)}", flush=True)

        except Exception as e:
            print(f"🔴 ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()

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
