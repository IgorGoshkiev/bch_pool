import asyncio
import json
import time
import re
from datetime import datetime, UTC
from typing import Dict, Optional

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import EXTRA_NONCE2_SIZE
from app.utils.config import settings
from app.services.miner_stats import miner_stats_service, ShareInfo

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

        # ===== СЛОЖНОСТИ МАЙНЕРОВ =====
        # display_difficulty — то, что мы ОТПРАВЛЯЕМ ASIC через mining.set_difficulty.
        # Управляет частотой шаров и отображением на панели ASIC.
        self.miner_difficulties: Dict[str, float] = {}

        # Индивидуальный МИНИМУМ display_difficulty для каждого ASIC.
        # Берётся из mining.suggest_difficulty при подключении.
        # Если suggest не было — используется settings.start_display_difficulty.
        # Пул НЕ опускает сложность ниже этого значения.
        self.min_asic_difficulties: Dict[str, float] = {}

        # Временное хранилище suggest_difficulty до авторизации.
        # ASIC может прислать suggest до mining.authorize.
        # Ключ — client_id, значение — предложенная сложность.
        self._pending_suggest: Dict[str, float] = {}

        # Время последнего обновления сложности для каждого майнера.
        # Нужно, чтобы не менять сложность на КАЖДОМ шаре.
        self._last_diff_update: Dict[str, float] = {}

        # Отложенная сложность для отправки вместе с notify.
        # ВАЖНО: ASIC (WhatsMiner) применяет set_difficulty ТОЛЬКО
        # когда получает notify сразу после. Поэтому set_difficulty
        # нельзя отправлять отдельно — только вместе с notify.
        self._pending_difficulty: Dict[str, int] = {}

        self.start_time = datetime.now(UTC)
        self._lock = asyncio.Lock()  # Для синхронизации доступа
        self.max_connections = 1000  # Максимальное количество подключений
        self._ip_connections: Dict[str, int] = {}
        self.max_per_ip = 10
        self._client_ips: Dict[str, str] = {}

        logger.info(
            "TCP Stratum сервер инициализирован",
            event="tcp_server_initialized",
            host=host,
            port=port,
            start_time=self.start_time.isoformat()
        )

    @staticmethod
    def _round_to_power_of_two(value: float, current: float = None) -> int:
        """
        Округление до степени двойки С ГАРАНТИЕЙ ИЗМЕНЕНИЯ.

        ВАЖНО: ASIC (WhatsMiner) ожидает сложность в виде степеней двойки:
        16384, 32768, 65536, 131072, 262144, ...

        Molehole использует именно такие значения:
        65536 → 32768 → 16384 → 32768 → 65536 → 262144

        Если отправить произвольное число (42583, 55357, 71964),
        ASIC может ИГНОРИРОВАТЬ set_difficulty и не переключаться
        на новый job_id.

        ПРОБЛЕМА:
        Если просто округлить до ближайшей степени двойки, то:
          42598 -> round(log2(42598)) = round(15.38) = 15 -> 32768
        То есть результат РАВЕН текущей сложности (32768).
        Тогда change_ratio = 0, и update_miner_difficulty не вызывается.

        РЕШЕНИЕ:
        Если результат равен current, и value > current — берём СЛЕДУЮЩУЮ
        степень двойки (вверх). Если value < current — берём ПРЕДЫДУЩУЮ
        (вниз). Это гарантирует, что сложность ИЗМЕНИТСЯ.

        Args:
            value: Произвольное число сложности
            current: Текущая сложность (для проверки, изменилось ли)

        Returns:
            Степень двойки (int), гарантированно отличная от current
        """
        import math
        if value <= 0:
            return 1

        log2 = math.log2(value)
        rounded_log2 = round(log2)
        result = int(2 ** rounded_log2)

        # ===== ГАРАНТИЯ ИЗМЕНЕНИЯ =====
        if current is not None and result == int(current):
            if value > current:
                # Хотим поднять, но округление дало текущую — берём следующую вверх
                result = int(2 ** (rounded_log2 + 1))
                print(f"📊 [ROUND] Forced UP: {value} (current={current}) -> {result}", flush=True)
            elif value < current:
                # Хотим опустить, но округление дало текущую — берём следующую вниз
                result = int(2 ** (rounded_log2 - 1))
                print(f"📊 [ROUND] Forced DOWN: {value} (current={current}) -> {result}", flush=True)
        # =================================

        return result

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

        # ===== ИЗВЛЕКАЕМ IP АДРЕС =====

        if addr is None:
            client_id = f"unknown_{id(writer)}"
            client_ip = "unknown"
        elif isinstance(addr, tuple) and len(addr) >= 2:
            client_id = f"{addr[0]}:{addr[1]}"
            client_ip = addr[0]
        else:
            client_id = f"unknown_{id(writer)}"
            client_ip = "unknown"

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

        # записывает данные клиента
        async with self._lock:
            self._connection_times[client_id] = connect_time
            self.connections[client_id] = writer
            self._client_ips[client_id] = client_ip

        logger.info(
            'Новое TCP подключение',
            event="tcp_client_connected",
            client_id=client_id,
            remote_address=str(addr),
            client_ip=client_ip,
            connect_time=connect_time.isoformat(),
            total_connections=len(self.connections) + 1
        )

        try:
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
                self._client_ips.pop(client_id, None)
                self._pending_suggest.pop(client_id, None)

            # Рассчитываем длительность подключения
            if connect_time:
                connection_duration = (datetime.now(UTC) - connect_time).total_seconds()

            # Очищаем задания майнера если он был авторизован
            if miner_address:
                self.job_service.cleanup_miner_jobs(miner_address)

                # ===== ОЧИЩАЕМ ДАННЫЕ О СЛОЖНОСТИ =====
                # При отключении ASIC удаляем его индивидуальные данные.
                # При повторном подключении он снова пришлёт suggest_difficulty.
                self.miner_difficulties.pop(miner_address, None)
                self.min_asic_difficulties.pop(miner_address, None)
                self._last_diff_update.pop(miner_address, None)
                print(f"🧹 [CLEANUP] Removed difficulty data for {miner_address[:20]}...", flush=True)

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
                remaining_connections=remaining
            )

    async def handle_message(self, data: dict, writer: asyncio.StreamWriter, client_id: str):
        """Обработка Stratum сообщений"""
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
                    # ===== НАЧАЛЬНАЯ DISPLAY DIFFICULTY =====
                    # ВАЖНО: suggest_difficulty от ASIC — это СТАРТОВАЯ точка.
                    # Если ASIC его прислал — начинаем с него.
                    # Если нет — используем start_display_difficulty.
                    #
                    # suggest_difficulty ТАКЖЕ используется как НИЖНЯЯ ГРАНИЦА.
                    # Пул НЕ опускает сложность ниже suggest, потому что при suggest
                    # шары уже идут часто. Опускать ещё ниже — бессмысленно.
                    suggested = self._pending_suggest.get(client_id)
                    if suggested:
                        initial_diff_float = suggested
                        print(f"✅ [AUTH] Using suggested difficulty as start: {suggested}", flush=True)
                    else:
                        initial_diff_float = settings.start_display_difficulty
                        print(f"✅ [AUTH] Using default start_display_difficulty: {initial_diff_float}", flush=True)

                    initial_diff = max(1, int(initial_diff_float))
                    print(f"✅ [AUTH] Initial display_difficulty: {initial_diff_float} -> {initial_diff}", flush=True)

                    async with self._lock:
                        self.miners[client_id] = authorized_address
                        # Храним как float для расчетов
                        self.miner_difficulties[authorized_address] = float(initial_diff)
                        print(f"✅ СЛОЖНОСТЬ сохранена: {initial_diff}", flush=True)

                        # ===== ИНИЦИАЛИЗИРУЕМ МИНИМУМ ДЛЯ ЭТОГО ASIC =====
                        # suggest_difficulty — это НИЖНЯЯ ГРАНИЦА для этого ASIC.
                        # Пул НЕ опускает сложность ниже suggest.
                        # Если suggest не было — используем min_display_difficulty.
                        if authorized_address not in self.min_asic_difficulties:
                            if suggested:
                                self.min_asic_difficulties[authorized_address] = max(
                                    settings.min_display_difficulty,
                                    float(suggested)
                                )
                                print(f"🎯 [AUTH] min_asic_difficulty from suggest: {suggested}", flush=True)
                            else:
                                self.min_asic_difficulties[authorized_address] = settings.min_display_difficulty
                                print(f"🎯 [AUTH] min_asic_difficulty default: {settings.min_display_difficulty}", flush=True)

                    # 1. Ответ на авторизацию
                    response = {"id": msg_id, "result": True, "error": None}
                    await self._send_json(writer, response)
                    print(f"✅ AUTHORIZED: {username} -> {authorized_address}", flush=True)

                    # 2. Обновляем валидатор (для совместимости)
                    if self.share_validator:
                        self.share_validator.pool_difficulty = initial_diff

                    # 3. ОТПРАВЛЯЕМ ЗАДАНИЕ
                    # ВАЖНО: set_difficulty НЕ отправляем здесь — он уйдёт
                    # в send_new_job_tcp ПЕРЕД mining.notify (как у Molehole).
                    await self.send_new_job_tcp(authorized_address, writer)
                    print(f"📤 SENT INITIAL JOB TO: {authorized_address}", flush=True)

                else:
                    await self._send_error(writer, msg_id, error_msg or "Authorization failed")
            else:
                await self._send_error(writer, msg_id, "Invalid authorize parameters")

        elif method == "mining.suggest_difficulty":
            # ============================================================
            # ASIC ПРИСЫЛАЕТ СВОЮ ЖЕЛАЕМУЮ СЛОЖНОСТЬ.
            #
            # ВАЖНО: это СТАРТОВАЯ точка и НИЖНЯЯ ГРАНИЦА.
            # - Пул НАЧИНАЕТ с этого значения.
            # - Пул НЕ опускает сложность ниже этого значения,
            #   потому что при suggest шары уже идут часто.
            #   Опускать ещё ниже — бессмысленно.
            # - Пул МОЖЕТ повысить сложность выше suggest,
            #   если шары идут слишком часто.
            # ============================================================
            if params and len(params) >= 1:
                suggested = float(params[0])
                print(f"📊 [SUGGEST_DIFF] ASIC suggested: {suggested}", flush=True)

                # Сохраняем во временное хранилище (до authorize)
                self._pending_suggest[client_id] = suggested

                if client_id in self.miners:
                    miner_address = self.miners[client_id]

                    # ===== СОХРАНЯЕМ КАК НИЖНЮЮ ГРАНИЦУ ДЛЯ ЭТОГО ASIC =====
                    # Пул не будет опускать сложность ниже этого значения.
                    self.min_asic_difficulties[miner_address] = max(
                        settings.min_display_difficulty,
                        suggested
                    )
                    print(f"🎯 [SUGGEST_DIFF] min_asic_difficulty set: {self.min_asic_difficulties[miner_address]}", flush=True)

                    # ===== ТАКЖЕ ПЕРЕДАЁМ В DIFFICULTY_SERVICE =====
                    # Это ориентир для адаптации и нижняя граница.
                    if self.difficulty_service:
                        self.difficulty_service.set_target_difficulty(miner_address, suggested)
                        # Синхронизируем нижнюю границу с difficulty_service
                        self.difficulty_service.min_asic_difficulties[miner_address] = self.min_asic_difficulties[miner_address]
                        print(f"📊 [SUGGEST_DIFF] Synced to difficulty_service", flush=True)
                else:
                    print(f"📊 [SUGGEST_DIFF] Saved to pending (waiting for authorize)", flush=True)
            else:
                print(f"⚠️ [SUGGEST_DIFF] Invalid params: {params}", flush=True)

            response = {"id": msg_id, "result": True, "error": None}
            await self._send_json(writer, response)
            print(f"📊 [SUGGEST_DIFF] Confirmed", flush=True)

        elif method == "mining.extranonce.subscribe":
            await self._handle_extranonce_subscribe(msg_id, writer)

        elif method == "mining.submit":
            if client_id in self.miners:
                await self.handle_submit_tcp(msg_id, params, self.miners[client_id], writer)
            else:
                # ===== АВТОМАТИЧЕСКАЯ АВТОРИЗАЦИЯ ПО IP =====
                client_ip = self._client_ips.get(client_id, "")
                if not client_ip:
                    client_ip = re.sub(r':\d+$', '', client_id)

                found = False
                for cid, addr in self.miners.items():
                    if cid.startswith(client_ip):
                        self.miners[client_id] = addr
                        if cid in self.miner_difficulties:
                            self.miner_difficulties[client_id] = self.miner_difficulties[cid]
                        if cid in self.min_asic_difficulties:
                            self.min_asic_difficulties[client_id] = self.min_asic_difficulties[cid]
                        del self.miners[cid]
                        if cid in self.miner_difficulties:
                            del self.miner_difficulties[cid]
                        if cid in self.min_asic_difficulties:
                            del self.min_asic_difficulties[cid]

                        print(f"🔁 AUTO-AUTHORIZED by IP: {client_ip} -> {addr} (new client: {client_id})", flush=True)

                        await self.handle_submit_tcp(msg_id, params, addr, writer)

                        found = True
                        break

                if not found:
                    await self._send_error(writer, msg_id, "Not authorized")

        else:
            await self._send_error(writer, msg_id, f"Unknown method: {method}")

    async def _handle_subscribe(self, msg_id: int, writer: asyncio.StreamWriter):
        """Обработка подписки"""
        logger.info("=== START _handle_subscribe ===")

        # Используем extra_nonce1 из пула (генерируется при старте)
        extra_nonce1 = None

        if self.job_manager:
            extra_nonce1 = self.job_manager.get_pool_extra_nonce1()
            if extra_nonce1:
                print(f"✅ extra_nonce1 из пула: {extra_nonce1}", flush=True)
            else:
                print(f"❌ extra_nonce1 не получен из пула", flush=True)

        # Если по какой-то причине нет - генерируем временный
        if not extra_nonce1:
            import secrets
            extra_nonce1 = secrets.token_hex(20)
            print(f"⚠️ Генерируем временный extra_nonce1: {extra_nonce1}", flush=True)

        response = {
            "id": msg_id,
            "result": [
                # Как у Molehole: mining.notify ПЕРВЫМ, mining.set_difficulty ВТОРЫМ
                [["mining.notify", "job_id"], ["mining.set_difficulty", "difficulty"]],
                extra_nonce1,
                EXTRA_NONCE2_SIZE
            ],
            "error": None
        }
        await self._send_json(writer, response)
        logger.info("=== SUBSCRIBE RESPONSE SENT ===")

        # Отправляем extranonce
        extranonce_msg = {
            "method": "mining.set_extranonce",
            "params": [extra_nonce1, EXTRA_NONCE2_SIZE]
        }
        await self._send_json(writer, extranonce_msg)
        logger.info("✅ Extranonce sent")
        print(f"📤 SENT EXTRANONCE: {extra_nonce1}", flush=True)

    async def _handle_configure(self, msg_id: int, writer: asyncio.StreamWriter, params: list):
        """Обработка mining.configure от WhatsMiner (как Molehole!)"""
        print(f"🔵 ENTER _handle_configure", flush=True)
        logger.info(f"=== CONFIGURE REQUEST: {params} ===")

        # ===== ОТВЕТ КАК У MOLEHOLE =====
        # Molehole НЕ отправляет "minimum-difficulty" в ответе.
        # Оставляем только "version-rolling" и "version-rolling.mask".
        response = {
            "id": msg_id,
            "result": {
                "version-rolling": True,
                "version-rolling.mask": "1fffe000"
            },
            "error": None
        }
        await self._send_json(writer, response)
        print(f"🔵 CONFIGURE RESPONSE (as Molehole): {response['result']}", flush=True)
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

        print(f"📤 JOB will be sent via broadcast to: {miner_address}", flush=True)

        # ===== НЕ ВЫЗЫВАЕМ send_new_job_tcp =====
        # ВАЖНО: если вызвать send_new_job_tcp, будет двойной notify.
        # broadcast_new_job_to_all() и так шлёт notify каждые 30 секунд.
        # Кроме того, send_new_job_tcp отправляет set_difficulty + notify,
        # что может привести к двойному set_difficulty.
        #
        # Поэтому: просто логируем. notify придёт через broadcast
        # в течение 30 секунд, с set_difficulty (парой).
        print(f"📤 [EXTRANONCE] notify will be sent via broadcast (up to 30s)", flush=True)

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

        # ===== ПРОФАЙЛИНГ =====
        profiler = {}
        start_total = time.time()
        print(f"⏱️ START HANDLE_SUBMIT_TCP", flush=True)

        # ===== ИНИЦИАЛИЗАЦИЯ ВСЕХ ПЕРЕМЕННЫХ =====
        hash_result = None
        job_data = None
        share_difficulty = None
        extra_data = None  # noqa: F841
        is_valid = False  # noqa: F841
        error_msg = None  # noqa: F841

        try:
            # 1. ПРОВЕРКА ПАРАМЕТРОВ
            if len(params) < 5:
                await self._send_error(writer, msg_id, "Invalid submit parameters")
                return

            # 2. ИЗВЛЕКАЕМ ДАННЫЕ
            job_id = params[1]
            extra_nonce2 = params[2]
            ntime = params[3]
            nonce = params[4]
            version_from_asic = params[5] if len(params) > 5 else None

            # 3. ПОЛУЧАЕМ ЗАДАНИЕ  РАСЧЕТ ХЭША И СЛОЖНОСТИ ШАРА
            try:
                t0 = time.time()
                job_data = self.job_service.get_job(job_id)
                profiler['get_job'] = (time.time() - t0) * 1000
                print(f"⏱️ get_job: {profiler['get_job']:.1f}ms", flush=True)
            except Exception as e:
                print(f"🔥 ERROR getting job: {e}", flush=True)

            if job_data and self.share_validator:
                try:
                    t0 = time.time()
                    hash_result = self.share_validator.calculate_hash(
                        job_data, extra_nonce2, ntime, nonce, version_from_asic
                    )
                    profiler['calculate_hash'] = (time.time() - t0) * 1000
                    print(f"⏱️ calculate_hash: {profiler['calculate_hash']:.1f}ms", flush=True)
                    print(f"🔥 SHARE HASH: {hash_result}", flush=True)

                    hash_int = int(hash_result, 16)
                    if hash_int > 0:
                        # Берем TARGET из валидатора (динамический)
                        target_for_diff_1 = self.share_validator.TARGET_FOR_DIFFICULTY_1
                        # Вместо целочисленного деления используем float
                        share_difficulty = target_for_diff_1 / hash_int
                        print(f"🔥 SHARE DIFFICULTY: {share_difficulty}", flush=True)

                    else:
                        share_difficulty = 0
                        print(f"🔥 WARNING: hash_int is 0, cannot calculate difficulty", flush=True)
                except Exception as e:
                    print(f"🔥 ERROR calculating hash: {e}", flush=True)
                    hash_result = None
            else:
                print(f"🔥 JOB NOT FOUND or no validator: {job_id}", flush=True)

            # 4. ПРОВЕРЯЕМ, ЧТО ХЭШ РАССЧИТАН
            if hash_result is None:
                print(f"🔴 SHARE REJECTED: hash calculation failed", flush=True)
                await self._send_error(writer, msg_id, "Failed to calculate hash")
                return

            # 5. СЛОЖНОСТЬ ДЛЯ ПРОВЕРКИ
            # ============================================================
            # ВАЖНО: РАЗДЕЛЯЕМ ДВЕ СЛОЖНОСТИ!
            #
            # display_difficulty (miner_difficulties) — то, что МЫ ОТПРАВЛЯЕМ ASIC
            #   через mining.set_difficulty. Управляет частотой шаров ASIC
            #   и отображением на его панели.
            #
            # validation_difficulty (default_validation_difficulty) — то, с чем МЫ ВАЛИДИРУЕМ
            #   входящие шары. Это ОЧЕНЬ НИЗКАЯ сложность (1e-10), потому что ASIC
            #   присылает шары со своей внутренней сложностью (~1e-9),
            #   которую мы не знаем и не контролируем.
            #
            # РАЗДЕЛЕНИЕ ПОЗВОЛЯЕТ:
            #   1. Управлять частотой шаров ASIC (через display_difficulty).
            #   2. Принимать ВСЕ шары ASIC (через validation_difficulty).
            # ============================================================
            display_difficulty = self.miner_difficulties.get(
                miner_address,
                settings.start_display_difficulty
            )
            # Для ВАЛИДАЦИИ всегда используем default_validation_difficulty (1e-10)
            validation_difficulty = settings.default_validation_difficulty
            print(f"🔍 [SPLIT] display_difficulty (для ASIC):   {display_difficulty:.10f}", flush=True)
            print(f"🔍 [SPLIT] validation_difficulty (для нас): {validation_difficulty:.10f}", flush=True)
            # ============================================================

            # 6. ВАЛИДАЦИЯ
            print(f"🔍 [TCP] Перед вызовом validate_and_process_share: version_from_asic = {version_from_asic}",
                  flush=True)
            print(f"🔍 enable_share_validation={settings.enable_share_validation}", flush=True)

            if settings.enable_share_validation:
                try:
                    # ===== ДИАГНОСТИКА: проверяем version_from_asic перед вызовом =====
                    print(f"🔍 [TCP] Перед validate_and_process_share: version_from_asic = {version_from_asic}",
                          flush=True)
                    print(f"🔍 [TCP] len(params) = {len(params)}, params = {params}", flush=True)
                    # ================================================================
                    t0 = time.time()
                    is_valid, error_msg, extra_data = self.job_service.validate_and_process_share(
                        job_id=job_id,
                        extra_nonce2=extra_nonce2,
                        ntime=ntime,
                        nonce=nonce,
                        miner_address=miner_address,
                        version=version_from_asic,
                        pool_difficulty=validation_difficulty
                    )

                    profiler['validate'] = (time.time() - t0) * 1000
                    print(f"⏱️ validate: {profiler['validate']:.1f}ms", flush=True)
                except Exception as e:
                    print(f"🔥 ERROR validating: {e}", flush=True)
                    is_valid = False
            else:
                print(f"⚠️ VALIDATION DISABLED: accepting all shares", flush=True)
                is_valid = True
                error_msg = None
                extra_data = None

            # 7. ЕСЛИ НЕВАЛИДЕН - ОТКЛОНЯЕМ
            if not is_valid:
                print(f"🔴 SHARE REJECTED: {error_msg}", flush=True)
                await self._send_error(writer, msg_id, f"Invalid share: {error_msg}")
                return

            # 8. ОПРЕДЕЛЯЕМ СЛОЖНОСТЬ ДЛЯ СТАТИСТИКИ
            # Используем сложность шара, если она рассчитана, иначе fallback
            difficulty_to_save = share_difficulty if share_difficulty is not None else settings.default_validation_difficulty
            print(f"📊 SHARE difficulty: {difficulty_to_save:.10e}", flush=True)

            # 9. ОБНОВЛЯЕМ СТАТИСТИКУ В ПАМЯТИ
            # Создаем информацию о шаре
            share_info = ShareInfo(
                hash=hash_result,
                difficulty=difficulty_to_save,
                is_valid=is_valid,
                timestamp=datetime.now(UTC),
                job_id=job_id,
                nonce=nonce,
                ntime=ntime
            )
            try:
                t0 = time.time()
                # Добавляем в статистику (мгновенно, без БД)
                await miner_stats_service.add_share(miner_address, share_info)
                profiler['stats'] = (time.time() - t0) * 1000
                print(f"⏱️ stats: {profiler['stats']:.1f}ms", flush=True)
                print(f"📊 STATS UPDATED IN MEMORY", flush=True)
            except Exception as e:
                print(f"🔥 ERROR adding stats: {e}", flush=True)

            # 10. ПРОВЕРЯЕМ БЛОК (ИСПОЛЬЗУЕМ extra_data)
            if extra_data and extra_data.get('is_valid_block', False):
                print(f"🎉🎉🎉 BLOCK FOUND! Отправляем в ноду...", flush=True)

                # Получаем высоту блока из job_data
                block_height = 0
                if job_data and 'template' in job_data:
                    block_height = job_data['template'].get('height', 0)

                try:
                    t0 = time.time()
                    # Сохраняем ТОЛЬКО блок в БД!
                    await self.database_service.save_block(
                        height=block_height,
                        block_hash=hash_result,
                        miner_address=miner_address,
                        confirmed=False
                    )
                    profiler['save_block'] = (time.time() - t0) * 1000
                    print(f"⏱️ save_block: {profiler['save_block']:.1f}ms", flush=True)
                    print(f"💾 BLOCK SAVED TO DB! height={block_height}", flush=True)
                except Exception as e:
                    print(f"🔥 ERROR saving block: {e}", flush=True)

                try:
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
                        print(f"✅ BLOCK ACCEPTED BY NODE! hash={hash_result[:16]}...", flush=True)
                    else:
                        print(f"🔴 BLOCK REJECTED: {block_result.get('message')}", flush=True)
                except Exception as e:
                    print(f"🔥 ERROR submitting block: {e}", flush=True)

            # 11. ОТПРАВЛЯЕМ УСПЕХ
            try:
                t0 = time.time()
                response = {"id": msg_id, "result": True, "error": None}
                await self._send_json(writer, response)
                profiler['send_json'] = (time.time() - t0) * 1000
                print(f"⏱️ send_json: {profiler['send_json']:.1f}ms", flush=True)
                print(f"✅ SHARE ACCEPTED (stats in memory)", flush=True)
            except BrokenPipeError:
                print(f"🔴 ASIC DISCONNECTED during send", flush=True)
                return
            except Exception as e:
                print(f"🔥 ERROR sending response: {e}", flush=True)

            # 12. АДАПТИВНАЯ СЛОЖНОСТЬ
            # ============================================================
            # КЛЮЧЕВОЙ МОМЕНТ: здесь мы динамически адаптируем сложность
            # для каждого майнера на основе частоты поступления шаров.
            #
            # ВАЖНО (после правок):
            # - Проверка частоты — ЗДЕСЬ, в handle_submit_tcp.
            # - DifficultyService.calculate_difficulty_for_miner вызывается
            #   ТОЛЬКО если проверка пройдена.
            # - DifficultyService НЕ сохраняет last_update_time.
            # - handle_submit_tcp сохраняет _last_diff_update и
            #   difficulty_service.last_update_time ТОЛЬКО после
            #   УСПЕШНОЙ отправки set_difficulty.
            # - При смене сложности сбрасываем share_timestamps.
            # ============================================================
            if self.difficulty_service and is_valid:  # ← is_valid = True только для принятых шаров!
                try:
                    t0 = time.time()

                    # ===== 1. Добавляем шар в статистику для расчета сложности =====
                    await self.difficulty_service.add_share(miner_address, difficulty_to_save)
                    print(f"📊 [DIFF] Share added to difficulty_service", flush=True)

                    # ===== 2. ПРОВЕРКА ЧАСТОТЫ ОБНОВЛЕНИЯ — ЗДЕСЬ =====
                    last_update = self._last_diff_update.get(miner_address, 0)
                    time_since_update = time.time() - last_update

                    # Если это первый шар — используем быстрый интервал
                    if last_update == 0:
                        min_interval = settings.difficulty_first_update_interval
                        print(f"📊 [DIFF] FIRST update, min_interval: {min_interval}s", flush=True)
                    else:
                        min_interval = settings.difficulty_min_update_interval
                        print(f"📊 [DIFF] Regular update, min_interval: {min_interval}s", flush=True)

                    print(f"📊 [DIFF] time_since_update: {time_since_update:.1f}s (min: {min_interval}s)", flush=True)

                    if time_since_update < min_interval:
                        print(f"📊 [DIFF] ⏸️ Too early to update (need {min_interval - time_since_update:.1f}s more)", flush=True)
                        profiler['difficulty'] = (time.time() - t0) * 1000
                        print(f"⏱️ difficulty: {profiler['difficulty']:.1f}ms (skipped)", flush=True)
                    else:
                        # ===== 3. ТЕПЕРЬ вызываем DifficultyService =====
                        new_difficulty = await self.difficulty_service.calculate_difficulty_for_miner(miner_address)
                        print(f"📊 [DIFF] New difficulty calculated: {new_difficulty:.10f}", flush=True)

                        # ===== 4. Получаем текущую сложность майнера =====
                        current_difficulty = self.miner_difficulties.get(
                            miner_address,
                            settings.start_display_difficulty
                        )
                        print(f"📊 [DIFF] Current difficulty: {current_difficulty:.10f}", flush=True)

                        # ===== 5. Применяем нижнюю границу — min_asic_difficulty =====
                        # ВАЖНО: Molehole опускает сложность, если ASIC не справляется.
                        # Мы тоже опускаем, но НЕ ниже min_asic_difficulty ДЛЯ ЭТОГО ASIC.
                        #
                        # min_asic_difficulty берётся из mining.suggest_difficulty,
                        # которое ASIC прислал при подключении.
                        # Если suggest не было — используется settings.min_display_difficulty.
                        #
                        # Это защищает от ситуации, когда пул опускает сложность
                        # ниже той, на которой ASIC может найти шар.
                        min_allowed = self.min_asic_difficulties.get(
                            miner_address,
                            settings.min_display_difficulty
                        )
                        print(f"📊 [DIFF] min_allowed (min_asic_difficulty for this ASIC): {min_allowed}", flush=True)
                        if new_difficulty < min_allowed:
                            print(f"📊 [DIFF] Capped at min_asic_difficulty: {min_allowed}", flush=True)
                            new_difficulty = min_allowed

                        # ===== 6. Проверяем, изменилась ли сложность =====
                        if current_difficulty > 0:
                            change_ratio = abs(new_difficulty - current_difficulty) / current_difficulty
                        else:
                            change_ratio = 1.0

                        min_change = settings.difficulty_min_change

                        print(f"📊 [DIFF_DEBUG] ========================================", flush=True)
                        print(f"📊 [DIFF_DEBUG] miner: {miner_address[:20]}...", flush=True)
                        print(f"📊 [DIFF_DEBUG] current_difficulty: {current_difficulty:.10f}", flush=True)
                        print(f"📊 [DIFF_DEBUG] new_difficulty:     {new_difficulty:.10f}", flush=True)
                        print(f"📊 [DIFF_DEBUG] min_allowed:        {min_allowed:.10f}", flush=True)
                        print(f"📊 [DIFF_DEBUG] change_ratio:       {change_ratio:.6f}", flush=True)
                        print(f"📊 [DIFF_DEBUG] min_change:         {min_change}", flush=True)

                        if change_ratio > min_change:
                            print(f"📊 [DIFF_DEBUG] ✅ UPDATE! Sending new difficulty to ASIC...", flush=True)

                            # Округляем до целого
                            rounded_diff = max(1.0, float(int(new_difficulty)))
                            print(f"📊 [DIFF_DEBUG] Rounded difficulty: {rounded_diff:.0f}", flush=True)

                            # Отправляем ASIC (возвращает True/False)
                            sent_ok = await self.update_miner_difficulty(miner_address, rounded_diff)

                            if sent_ok:
                                # ===== СОХРАНЯЕМ ТОЛЬКО ПОСЛЕ УСПЕШНОЙ ОТПРАВКИ =====
                                self.miner_difficulties[miner_address] = rounded_diff
                                self._last_diff_update[miner_address] = time.time()

                                # ===== СИНХРОНИЗИРУЕМ С DIFFICULTY_SERVICE =====
                                if self.difficulty_service:
                                    self.difficulty_service.miner_difficulties[miner_address] = rounded_diff
                                    self.difficulty_service.last_update_time[miner_address] = time.time()

                                    # ===== СБРАСЫВАЕМ ВРЕМЕННЫЕ МЕТКИ =====
                                    # Это нужно, чтобы median_interval считался
                                    # ТОЛЬКО по шарам с НОВОЙ сложностью.
                                    self.difficulty_service.reset_share_timestamps(miner_address)

                                print(f"📊 [DIFF_DEBUG] miner_difficulties updated: {rounded_diff:.0f}", flush=True)

                                if current_difficulty > 0:
                                    change_pct = ((rounded_diff / current_difficulty - 1) * 100)
                                    print(f"📊 DIFFICULTY UPDATED: {current_difficulty:.10f} -> {rounded_diff:.0f} (change: {change_pct:+.1f}%)", flush=True)
                                else:
                                    print(f"📊 DIFFICULTY UPDATED: 0.0000000000 -> {rounded_diff:.0f} (INITIAL)", flush=True)
                            else:
                                print(f"🔴 [DIFF_DEBUG] ❌ update_miner_difficulty FAILED, not updating timestamp", flush=True)
                        else:
                            print(f"📊 [DIFF_DEBUG] ⏸️  SKIP: change_ratio {change_ratio:.6f} <= min_change {min_change}", flush=True)

                        profiler['difficulty'] = (time.time() - t0) * 1000
                        print(f"⏱️ difficulty: {profiler['difficulty']:.1f}ms", flush=True)

                except Exception as e:
                    print(f"🔥 ERROR updating difficulty: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    logger.error(
                        "Ошибка обновления сложности",
                        event="difficulty_update_error",
                        miner_address=miner_address[:20] + "...",
                        error=str(e)
                    )

            # ===== ВЫВОД ПРОФАЙЛИНГА =====
            total_ms = (time.time() - start_total) * 1000
            print(f"⏱️ TOTAL: {total_ms:.1f}ms", flush=True)
            if total_ms > 50:
                print(f"⚠️ SLOW SHARE: {total_ms:.1f}ms | " +
                      f"get_job={profiler.get('get_job', 0):.1f}ms | " +
                      f"hash={profiler.get('calculate_hash', 0):.1f}ms | " +
                      f"validate={profiler.get('validate', 0):.1f}ms | " +
                      f"stats={profiler.get('stats', 0):.1f}ms | " +
                      f"send={profiler.get('send_json', 0):.1f}ms | " +
                      f"diff={profiler.get('difficulty', 0):.1f}ms | " +
                      f"block={profiler.get('save_block', 0):.1f}ms", flush=True)
        except Exception as e:
            print(f"🔴🔴🔴 EXCEPTION IN HANDLE_SUBMIT_TCP: {e}", flush=True)
            import traceback
            traceback.print_exc()
            await self._send_error(writer, msg_id, f"Error processing share: {e}")

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        try:
            # ===== ПРОВЕРКА: writer еще жив? =====
            if writer is None:
                print(f"🔴 [SEND_JOB] Writer is None for {miner_address[:20]}...", flush=True)
                return

            if writer.is_closing():
                print(f"🔴 [SEND_JOB] Writer is closing for {miner_address[:20]}...", flush=True)
                return

            # Пробуем получить peername, чтобы проверить соединение
            try:
                peername = writer.get_extra_info('peername')
                if peername is None:
                    print(f"🔴 [SEND_JOB] No peername (connection dead?) for {miner_address[:20]}...", flush=True)
                    return
            except Exception as e:
                print(f"🔴 [SEND_JOB] Failed to get peername for {miner_address[:20]}...: {e}", flush=True)
                return
            # =============================================

            if self.job_manager is None:
                print("🔴 JOB_MANAGER IS NONE!", flush=True)
                return

            # ===== ✅ ИСПОЛЬЗУЕМ СУЩЕСТВУЮЩЕЕ ЗАДАНИЕ, ЕСЛИ ОНО ЕСТЬ =====
            job_data = None

            # Проверяем, есть ли текущее задание в job_manager
            if self.job_manager and self.job_manager.current_job:
                # Берем существующее задание
                job_data = self.job_manager.current_job.get('stratum_data')
                if job_data:
                    print(f"🔍 SEND_JOB: using existing job from job_manager.current_job", flush=True)
                else:
                    print(f"🔍 SEND_JOB: current_job exists but stratum_data is None", flush=True)

            # Если нет задания - создаем новое
            if not job_data:
                print(f"🔍 SEND_JOB: no existing job, creating new one for {miner_address}", flush=True)
                job_data = await self.job_manager.create_new_job(miner_address)

            if not job_data:
                print("🔴 Failed to get job data", flush=True)
                return

            print(f"🔍 SEND_JOB: job_data keys = {job_data.keys()}", flush=True)
            print(f"🔍 SEND_JOB: params length = {len(job_data['params'])}", flush=True)

            real_prevhash = job_data['params'][1]  # big-endian
            # ===== ПРАВИЛЬНЫЙ РЕВЕРС БАЙТ (BE -> LE) =====
            # real_prevhash[::-1] — реверс СИМВОЛОВ, а не байт.
            # Для prevhash это НЕПРАВИЛЬНЫЙ little-endian.
            # Нужно: bytes.fromhex(real_prevhash)[::-1].hex()
            real_prevhash_le = bytes.fromhex(real_prevhash)[::-1].hex() if real_prevhash else ""
            print(f"🔍 SEND_JOB: real_prevhash (BE): {real_prevhash[:32]}...", flush=True)
            print(f"🔍 SEND_JOB: real_prevhash_le (LE, correct): {real_prevhash_le[:32]}...", flush=True)
            # =============================================

            real_coinb1 = job_data['params'][2]
            real_coinb2 = job_data['params'][3]
            real_merkle_branch = job_data['params'][4]
            real_version = job_data['params'][5]
            real_bits = job_data['params'][6]
            real_ntime = job_data['params'][7]

            print(f"🔍 SEND_JOB: coinb1 length = {len(real_coinb1)}", flush=True)
            print(f"🔍 SEND_JOB: coinb2 length = {len(real_coinb2)}", flush=True)
            print(f"🔍 SEND_JOB: merkle_branch length = {len(real_merkle_branch)}", flush=True)
            print(f"🔍 SEND_JOB: bits = {real_bits}", flush=True)
            print(f"🔍 SEND_JOB: ntime = {real_ntime}", flush=True)

            # ===== ГЕНЕРИРУЕМ job_id КАК У MOLEHOLE =====
            # Molehole использует 8-символьный hex job_id (f63e4a5c).
            # Это уменьшает размер notify в 4 раза, прокси легче читает.
            #
            # ВАЖНО: короткий job_id НЕ ДОЛЖЕН удаляться cleanup_old_jobs.
            # Поэтому мы добавляем его в специальный список "живых" job_id
            # в JobService.cleanup_old_jobs (см. правку 2).
            #
            # Формат: 8 hex-символов = 4 байта timestamp + 2 байта counter.
            # Гарантирует уникальность в пределах ~18 часов.
            # =====================================================
            # Короткий hex job_id, как у Molehole
            timestamp_low = int(time.time()) & 0xFFFF
            counter_low = self.job_service.job_counter & 0xFFFF if self.job_service else 0
            job_id = f"{timestamp_low:04x}{counter_low:04x}"
            print(f"🔑 [SEND_JOB] Generated short job_id (Molehole-style): {job_id}", flush=True)
            # =====================================================

            # === ДЛЯ ВАЛИДАТОРА (сохраняем big-endian) ===
            real_job = {
                "method": "mining.notify",
                "params": [
                    job_id,
                    real_prevhash,  # ← BE! НЕ LE!
                    real_coinb1,
                    real_coinb2,
                    real_merkle_branch,
                    real_version,
                    real_bits,
                    real_ntime,
                    True
                ],
                "extra_nonce1": job_data.get('extra_nonce1'),
                "merkle_root": job_data.get('merkle_root')
            }

            # Добавляем в job_service
            self.job_service.add_job(
                job_id,
                real_job,
                miner_address,
                extra_nonce1=job_data.get('extra_nonce1')
            )

            # Для отправки ASIC
            job_data_for_send = {
                "method": real_job["method"],
                "params": [
                    job_id,
                    real_prevhash_le,  # ← little-endian!
                    real_coinb1,
                    real_coinb2,
                    real_merkle_branch,
                    real_version,
                    real_bits,
                    real_ntime,
                    True  # ← clean_jobs=True (как Molehole)
                ]
            }

            # ===== ПЕРЕД ОТПРАВКОЙ ЕЩЕ РАЗ ПРОВЕРЯЕМ WRITER =====
            if writer.is_closing():
                print(f"🔴 [SEND_JOB] Writer closed before send for {miner_address[:20]}...", flush=True)
                return

            # ===== ОТПРАВЛЯЕМ СЛОЖНОСТЬ ПЕРЕД ЗАДАНИЕМ (как Molehole!) =====
            # Некоторые ASIC "забывают" сложность после получения нового задания.
            # Поэтому отправляем mining.set_difficulty ПЕРЕД mining.notify.
            #
            # ВАЖНО: это НАЧАЛЬНАЯ сложность. Адаптация — в handle_submit_tcp.
            current_diff = self.miner_difficulties.get(
                miner_address,
                settings.start_display_difficulty
            )
            current_diff_int = max(1, int(current_diff))

            # ===== ОКРУГЛЯЕМ ДО СТЕПЕНИ ДВОЙКИ С ГАРАНТИЕЙ ИЗМЕНЕНИЯ =====
            # При первом подключении current_difficulty = 0 (нет в словаре),
            # поэтому округление просто даст ближайшую степень двойки.
            rounded_diff = self._round_to_power_of_two(
                current_diff_int,
                current=None  # первый раз — не с чем сравнивать
            )
            print(f"📊 [SEND_JOB] Initial difficulty rounded: {current_diff_int} -> {rounded_diff}", flush=True)
            current_diff_int = rounded_diff
            # ============================================================


            difficulty_msg = {
                "method": "mining.set_difficulty",
                "params": [current_diff_int],  # ← ЦЕЛОЕ ЧИСЛО
                "id": None
            }
            ok_diff = await self._send_json(writer, difficulty_msg)
            if not ok_diff:
                print(f"🔴 [SEND_JOB] Failed to send set_difficulty", flush=True)
                return
            print(f"📊 SENT DIFFICULTY BEFORE JOB: {current_diff_int}", flush=True)
            # ================================================================

            try:
                ok_notify = await self._send_json(writer, job_data_for_send)
                if not ok_notify:
                    print(f"🔴 [SEND_JOB] Failed to send notify", flush=True)
                    return
                print(f"✅ REAL JOB SENT: id={job_id}, merkle_len={len(real_merkle_branch)}", flush=True)

            except (BrokenPipeError, ConnectionResetError) as e:
                print(f"🔴 [SEND_JOB] Connection lost while sending to {miner_address[:20]}...: {e}", flush=True)
                # Удаляем этого майнера из списка, так как соединение потеряно
                for cid, addr in self.miners.items():
                    if addr == miner_address:
                        self.connections.pop(cid, None)
                        self.miners.pop(cid, None)
                        break
            except Exception as e:
                print(f"🔴 [SEND_JOB] Failed to send to {miner_address[:20]}...: {e}", flush=True)

        except Exception as e:
            print(f"🔴 [SEND_JOB] ERROR in send_new_job_tcp: {e}", flush=True)
            import traceback
            traceback.print_exc()

    async def broadcast_new_job(self, job_data: dict, clean_jobs: bool = False):
        """Рассылка нового задания всем TCP клиентам.

        ВАЖНО: отправляем set_difficulty ПЕРЕД notify (парой, как Molehole).
        ASIC применяет set_difficulty ТОЛЬКО когда получает notify сразу после.
        """
        # ===== РАСШИРЕННАЯ ОТЛАДКА =====
        print(f"\n{'=' * 60}", flush=True)
        print(f"📤 [BROADCAST] ===== START =====", flush=True)
        print(f"📤 [BROADCAST] Time: {datetime.now(UTC).strftime('%H:%M:%S')}", flush=True)
        print(f"📤 [BROADCAST] clean_jobs: {clean_jobs}", flush=True)
        print(f"📤 [BROADCAST] job_data keys: {list(job_data.keys()) if job_data else 'None'}", flush=True)
        print(f"📤 [BROADCAST] connections count: {len(self.connections)}", flush=True)
        print(f"📤 [BROADCAST] connections: {list(self.connections.keys())}", flush=True)
        print(f"📤 [BROADCAST] miners: {self.miners}", flush=True)
        print(f"📤 [BROADCAST] miners count: {len(self.miners)}", flush=True)
        print(f"📤 [BROADCAST] miner_difficulties: {self.miner_difficulties}", flush=True)
        print(f"📤 [BROADCAST] min_asic_difficulties: {self.min_asic_difficulties}", flush=True)
        print(f"📤 [BROADCAST] _pending_difficulty: {self._pending_difficulty}", flush=True)

        if not self.connections:
            print(f"📤 [BROADCAST] NO CONNECTIONS, skipping", flush=True)
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
            print(f"\n📤 [BROADCAST] Processing client: {client_id}", flush=True)
            miner_address = self.miners.get(client_id)
            print(f"📤 [BROADCAST] miner_address from miners: {miner_address}", flush=True)

            # ===== ПРОВЕРКА WRITER =====
            if writer is None:
                print(f"🔴 [BROADCAST] Writer is None for {client_id}", flush=True)
                failed_sends += 1
                continue

            print(f"📤 [BROADCAST] writer.is_closing(): {writer.is_closing()}", flush=True)
            if writer.is_closing():
                print(f"🔴 [BROADCAST] Writer is closing for {client_id}", flush=True)
                self.connections.pop(client_id, None)
                self.miners.pop(client_id, None)
                failed_sends += 1
                continue

            # Проверяем, живо ли соединение
            try:
                peername = writer.get_extra_info('peername')
                print(f"📤 [BROADCAST] peername: {peername}", flush=True)
                if peername is None:
                    print(f"🔴 [BROADCAST] No peername (dead connection) for {client_id}", flush=True)
                    self.connections.pop(client_id, None)
                    self.miners.pop(client_id, None)
                    failed_sends += 1
                    continue
            except Exception as e:
                print(f"🔴 [BROADCAST] Failed to get peername for {client_id}: {e}", flush=True)
                self.connections.pop(client_id, None)
                self.miners.pop(client_id, None)
                failed_sends += 1
                continue

            if miner_address:
                try:
                    print(f"📤 [BROADCAST] Creating job for {miner_address[:20]}...", flush=True)

                    # ===== ГЕНЕРИРУЕМ КОРОТКИЙ job_id (как Molehole!) =====
                    timestamp_low = int(time.time()) & 0xFFFF
                    counter_low = self.job_service.job_counter & 0xFFFF if self.job_service else 0
                    job_id = f"{timestamp_low:04x}{counter_low:04x}"
                    print(f"🔑 [BROADCAST] Сгенерирован КОРОТКИЙ job_id: {job_id}", flush=True)

                    # ===== ГЛУБОКАЯ КОПИЯ =====
                    # job_data.copy() — поверхностная копия, params — тот же список.
                    # Изменение params[0] портит оригинал (active_jobs, last_broadcast_job).
                    import copy
                    job_data_copy = copy.deepcopy(job_data)
                    job_data_copy["params"][0] = job_id
                    print(f"📤 [BROADCAST] job_id: {job_id}", flush=True)

                    # ===== ПРАВИЛЬНЫЙ РЕВЕРС БАЙТ (BE -> LE) ДЛЯ prevhash =====
                    # template.get('previousblockhash') — в BE.
                    # ASIC ожидает prevhash в LE.
                    # Без этого ASIC видит prevhash, начинающийся с нулей (BE),
                    # и ИГНОРИРУЕТ notify (как в твоём логе: notify #2 prevhash=0000000000000000...).
                    prevhash_be = job_data_copy["params"][1]
                    prevhash_le = bytes.fromhex(prevhash_be)[::-1].hex() if prevhash_be else ""
                    job_data_copy["params"][1] = prevhash_le
                    print(f"📤 [BROADCAST] prevhash BE: {prevhash_be[:32]}...", flush=True)
                    print(f"📤 [BROADCAST] prevhash LE: {prevhash_le[:32]}...", flush=True)
                    # =====================================================

                    # ===== ПОЛУЧАЕМ extra_nonce1 =====
                    extra_nonce1 = job_data.get('extra_nonce1')
                    print(f"📤 [BROADCAST] get extra_nonce1: {extra_nonce1}", flush=True)

                    # ===== ПОЛУЧАЕМ template =====
                    template = job_data.get('template')
                    if template:
                        print(f"📤 [BROADCAST] template найден, будет сохранён ОТДЕЛЬНО", flush=True)
                    else:
                        print(f"⚠️ [BROADCAST] template НЕ найден в job_data!", flush=True)

                    # Сохраняем в job_service
                    self.job_service.add_job(
                        job_id,
                        job_data_copy,
                        miner_address,
                        extra_nonce1=extra_nonce1,
                        template=template
                    )
                    print(f"📤 [BROADCAST] Job added to job_service", flush=True)

                    if writer.is_closing():
                        print(f"🔴 [BROADCAST] Writer closed before send for {client_id}", flush=True)
                        failed_sends += 1
                        continue

                    print(f"📤 [BROADCAST] Sending job to {miner_address[:20]}...", flush=True)

                    # ===== ДИАГНОСТИКА: _pending_difficulty ДО =====
                    print(f"📊 [BROADCAST] _pending_difficulty BEFORE pop: {self._pending_difficulty}", flush=True)

                    pending_diff = self._pending_difficulty.pop(miner_address, None)

                    # ===== ДИАГНОСТИКА: _pending_difficulty ПОСЛЕ =====
                    print(f"📊 [BROADCAST] _pending_difficulty AFTER pop: {self._pending_difficulty}, pending_diff={pending_diff}", flush=True)

                    if pending_diff is not None:
                        # Сложность ИЗМЕНИЛАСЬ — clean_jobs=True
                        clean_jobs_for_this_send = True
                        current_diff_int = pending_diff

                        # ===== ОКРУГЛЯЕМ ДО СТЕПЕНИ ДВОЙКИ С ГАРАНТИЕЙ ИЗМЕНЕНИЯ =====
                        # pending_diff уже прошёл округление в update_miner_difficulty,
                        # но на всякий случай округляем ещё раз с текущей сложностью.
                        old_diff = self.miner_difficulties.get(miner_address, 0)
                        rounded_diff = self._round_to_power_of_two(
                            current_diff_int,
                            current=old_diff if old_diff > 0 else None
                        )
                        print(f"📊 [BROADCAST] Rounded to power of 2: {current_diff_int} -> {rounded_diff} (old={old_diff})", flush=True)
                        current_diff_int = rounded_diff
                        # ==================================================================

                        self.miner_difficulties[miner_address] = float(current_diff_int)
                        print(f"📊 [BROADCAST] PENDING difficulty: {current_diff_int}, clean_jobs=True (СМЕНА СЛОЖНОСТИ)", flush=True)
                    else:
                        # Сложность НЕ изменилась — clean_jobs=False
                        clean_jobs_for_this_send = False
                        current_diff = self.miner_difficulties.get(
                            miner_address,
                            settings.start_display_difficulty
                        )
                        current_diff_int = max(1, int(current_diff))
                        print(f"📊 [BROADCAST] CURRENT difficulty: {current_diff_int}, clean_jobs=False", flush=True)

                    # ===== УСТАНАВЛИВАЕМ clean_jobs =====
                    if len(job_data_copy["params"]) >= 9:
                        job_data_copy["params"][8] = clean_jobs_for_this_send
                        print(f"📤 [BROADCAST] set clean_jobs to: {clean_jobs_for_this_send}", flush=True)

                    # ===== ОТПРАВЛЯЕМ set_difficulty ТОЛЬКО ПРИ СМЕНЕ СЛОЖНОСТИ =====
                    if pending_diff is not None:
                        difficulty_msg = {
                            "method": "mining.set_difficulty",
                            "params": [current_diff_int],
                            "id": None
                        }
                        ok_diff = await self._send_json(writer, difficulty_msg)
                        if not ok_diff:
                            print(f"🔴 [BROADCAST] Failed to send set_difficulty", flush=True)
                            failed_sends += 1
                            continue
                        print(f"📊 [BROADCAST] SENT set_difficulty BEFORE notify: {current_diff_int}", flush=True)
                    else:
                        print(f"📊 [BROADCAST] set_difficulty NOT SENT (сложность не изменилась)", flush=True)

                    # ===== ОТПРАВЛЯЕМ notify БЕЗ template =====
                    job_data_for_send = {
                        "method": "mining.notify",
                        "params": job_data_copy["params"]
                    }
                    print(f"📤 [BROADCAST] Отправляем ASIC notify БЕЗ template (только params)", flush=True)

                    ok_notify = await self._send_json(writer, job_data_for_send)
                    if not ok_notify:
                        print(f"🔴 [BROADCAST] Failed to send notify", flush=True)
                        failed_sends += 1
                        continue

                    successful_sends += 1
                    print(f"✅ [BROADCAST] Successfully sent to {miner_address[:20]}...", flush=True)

                except Exception as e:
                    failed_sends += 1
                    print(f"🔴 [BROADCAST] Failed to send to {client_id}: {e}", flush=True)
                    import traceback
                    traceback.print_exc()

                    self.connections.pop(client_id, None)
                    self.miners.pop(client_id, None)

                    logger.error(
                        "Ошибка рассылки задания TCP клиенту",
                        event="tcp_broadcast_error",
                        client_id=client_id,
                        miner_address=miner_address,
                        error=str(e)
                    )
            else:
                print(f"⚠️ [BROADCAST] No miner_address for {client_id}", flush=True)
                print(f"⚠️ [BROADCAST] self.miners content: {self.miners}", flush=True)
                failed_sends += 1

        print(f"\n📤 [BROADCAST] ===== DONE =====", flush=True)
        print(f"📤 [BROADCAST] Results: {successful_sends}/{total_clients} success, {failed_sends} failed", flush=True)
        print(f"📤 [BROADCAST] clean_jobs (параметр): {clean_jobs}", flush=True)
        print(f"📤 [BROADCAST] _pending_difficulty after: {self._pending_difficulty}", flush=True)
        print(f"{'=' * 60}\n", flush=True)

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

    async def update_miner_difficulty(self, miner_address: str, difficulty: float) -> bool:
        """
        Обновление сложности для конкретного майнера.

        ВАЖНО: НЕ отправляем set_difficulty сразу!

        ПОЧЕМУ:
        - Molehole отправляет set_difficulty ТОЛЬКО вместе с notify.
        - ASIC (WhatsMiner) применяет set_difficulty ТОЛЬКО когда
          получает notify сразу после.
        - Если отправить set_difficulty без notify — ASIC встаёт:
          он ищет шар на новой сложности со старым job и не находит.

        Поэтому: сохраняем сложность в _pending_difficulty,
        а set_difficulty отправим в broadcast_new_job перед notify.

        Returns:
            True — сложность сохранена (отправка будет позже, в broadcast).
            False — ошибка.
        """
        print(f"\n{'=' * 60}", flush=True)
        print(f"🔍 [UPDATE_DIFF] ===== START (deferred) =====", flush=True)
        print(f"🔍 [UPDATE_DIFF] miner_address: {miner_address}", flush=True)
        print(f"🔍 [UPDATE_DIFF] difficulty (raw): {difficulty}", flush=True)
        print(f"🔍 [UPDATE_DIFF] difficulty type: {type(difficulty).__name__}", flush=True)

        # ===== НЕ ОТПРАВЛЯЕМ СРАЗУ! СОХРАНЯЕМ. =====
        # set_difficulty будет отправлен в broadcast_new_job ПЕРЕД notify.
        display_difficulty = max(1, int(difficulty))

        # ===== ОКРУГЛЯЕМ ДО СТЕПЕНИ ДВОЙКИ С ГАРАНТИЕЙ ИЗМЕНЕНИЯ =====
        # Передаём текущую сложность, чтобы _round_to_power_of_two
        # гарантировал, что новая сложность ОТЛИЧАЕТСЯ от текущей.
        # Иначе 42598 -> 32768, и set_difficulty не отправится.
        current_difficulty = self.miner_difficulties.get(miner_address, 0)
        original_difficulty = display_difficulty
        display_difficulty = self._round_to_power_of_two(
            display_difficulty,
            current=current_difficulty if current_difficulty > 0 else None
        )

        print(
            f"📊 [UPDATE_DIFF] Rounded to power of 2: {original_difficulty} -> {display_difficulty} (current={current_difficulty})",
            flush=True)
        # ============================================================

        # ===== ПРОВЕРКА: изменилась ли сложность? =====
        # Если новая сложность равна текущей — НЕ добавляем в _pending_difficulty.
        # Иначе ASIC получит set_difficulty с тем же значением + clean_jobs=true,
        # и это может его сбить (он ждёт РЕАЛЬНОЙ смены сложности).
        #
        # ВАЖНО: это должно быть ПОСЛЕ округления до степени двойки,
        # потому что именно округлённое значение отправляется ASIC.
        #
        # ВАЖНО: это НЕ влияет на broadcast_new_job — там pending_diff
        # уже проверен здесь, и если он None, set_difficulty не отправится.
        current = self.miner_difficulties.get(miner_address, 0)
        if current > 0 and display_difficulty == int(current):
            print(
                f"📊 [UPDATE_DIFF] Difficulty NOT CHANGED ({display_difficulty} == {int(current)}), skipping",
                flush=True)
            print(f"🔍 [UPDATE_DIFF] ===== END (skipped) =====\n", flush=True)
            return False
        # =============================================

        # ===== ДИАГНОСТИКА: _pending_difficulty ДО =====
        print(f"📊 [UPDATE_DIFF] _pending_difficulty BEFORE: {self._pending_difficulty}", flush=True)
        # =============================================

        self._pending_difficulty[miner_address] = display_difficulty

        # ===== ДИАГНОСТИКА: _pending_difficulty ПОСЛЕ =====
        print(f"📊 [UPDATE_DIFF] _pending_difficulty AFTER: {self._pending_difficulty}", flush=True)
        # =================================================

        print(f"📊 [UPDATE_DIFF] DEFERRED set_difficulty = {display_difficulty}", flush=True)
        print(f"📊 [UPDATE_DIFF] Will send with next notify (in broadcast_new_job)", flush=True)

        # ===== СИНХРОНИЗИРУЕМ С DIFFICULTY_SERVICE =====
        # Чтобы DifficultyService знал реальную сложность ASIC.
        if self.difficulty_service:
            self.difficulty_service.miner_difficulties[miner_address] = float(display_difficulty)
            print(f"📊 [UPDATE_DIFF] Synced to difficulty_service: {display_difficulty}", flush=True)
        else:
            print(f"⚠️ [UPDATE_DIFF] self.difficulty_service is None, no sync", flush=True)

        logger.info(
            "Сложность отложена для отправки с notify",
            event="tcp_miner_difficulty_deferred",
            miner_address=miner_address,
            difficulty=difficulty,
            display_difficulty=display_difficulty
        )

        print(f"🔍 [UPDATE_DIFF] ===== END (deferred) =====\n", flush=True)
        return True

    async def _send_error(self, writer: asyncio.StreamWriter, msg_id: Optional[int], error_msg: str):
        """Отправка ошибки"""
        response = {
            "id": msg_id if msg_id is not None else 0,
            "result": None,
            "error": [20, error_msg, None]
        }
        await self._send_json(writer, response)

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict) -> bool:
        """
        Отправка JSON с новой строкой.

        ВАЖНО: НЕ используем таймаут на drain().
        Прокси (stratum_proxy.py) работает без таймаута, и это единственная
        причина, почему с прокси всё работает, а без — нет.

        С таймаутом: если ASIC медленно читает, drain() таймаутится
        и сообщение ТЕРЯЕТСЯ. ASIC получает только первое из пары
        set_difficulty+notify, потом встаёт.

        Без таймаута: drain() ждёт, пока весь буфер уйдёт в сокет.
        Это может занять время, но сообщение гарантированно доставлено.

        Returns:
            True — отправлено.
            False — ошибка.
        """
        try:
            msg = json.dumps(data) + "\n"
            # Показываем первые 500 символов отправляемого сообщения
            msg_preview = msg[:500] if len(msg) > 500 else msg
            print(f"📤 SENDING TO ASIC: {msg_preview}", flush=True)

            print(f"📏 [SEND_JSON] Message size: {len(msg)} bytes, method: {data.get('method', 'response')}", flush=True)
            writer.write(msg.encode())
            # ===== БЕЗ ТАЙМАУТА (как в прокси) =====
            await writer.drain()
            return True
        except Exception as e:
            print(f"🔴 SEND ERROR: {e}", flush=True)
            logger.error(f'Ошибка отправки TCP: {e}')
            return False

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
            "uptime_seconds": (datetime.now(UTC) - self.start_time).total_seconds(),
            "min_asic_difficulties": {addr[:20] + "...": diff for addr, diff in list(self.min_asic_difficulties.items())[:10]}
        }

        logger.debug(
            "Получение статистики TCP сервера",
            event="tcp_stats_requested",
            stats=stats
        )

        return stats