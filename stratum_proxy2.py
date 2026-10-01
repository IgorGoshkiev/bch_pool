#!/usr/bin/env python3
"""
Stratum Proxy v2 для отладки взаимодействия ASIC ↔ Пул.

ИСПРАВЛЕНО:
1. log_message НЕ обрезает mining.notify и mining.submit.
2. log_notify сохраняет ВСЕ поля notify (coinb1, coinb2, merkle_branch, version, nbits, ntime, prevhash).
3. Добавлен subscribe_history_v2.jsonl (extranonce1, extranonce2_size).
4. shares_v2.jsonl содержит job_data (полные данные notify для пересчёта хэша).
5. _calculate_share_hash: merkle_root берётся в LE.
6. В лог best share добавлено network_difficulty и share_diff × network_diff.
"""

import asyncio
import json
import hashlib
from datetime import datetime, UTC
from typing import Optional, Any, Dict
import traceback

# ========== КОНФИГУРАЦИЯ ==========
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 4444

POOL_HOST = "eu.molepool.com"
POOL_PORT = 5566

# ========== НАСТРОЙКИ ЛОГИРОВАНИЯ ==========
LOG_FILE = "proxy_v2.log"
NOTIFY_LOG_FILE = "notify_history_v2.jsonl"
SETDIFF_LOG_FILE = "setdiff_history_v2.jsonl"
SHARES_LOG_FILE = "shares_v2.jsonl"
SUBSCRIBE_LOG_FILE = "subscribe_history_v2.jsonl"
LOG_ALL_MESSAGES = True
LOG_SHARE_DETAILS = True
SHOW_ASIC_TO_POOL = True
SHOW_POOL_TO_ASIC = True
SAVE_SHARES_TO_FILE = True

# ========== НАСТРОЙКИ ДЕТЕКТОРА МОЛЧАНИЯ ==========
SILENCE_THRESHOLD_SEC = 20.0
SILENCE_CHECK_INTERVAL_SEC = 5.0

# ========== КОНСТАНТА ДЛЯ СЛОЖНОСТИ 1 ==========
TARGET_FOR_DIFFICULTY_1 = 0x00000000FFFF0000000000000000000000000000000000000000000000000000


# ========== ФУНКЦИИ ДЛЯ РАСЧЁТА TARGET ==========

def nbits_to_target(nbits_hex: str) -> int:
    """Convert nbits (compact target) to full 256-bit target."""
    nbits = int(nbits_hex, 16)
    exponent = nbits >> 24
    mantissa = nbits & 0x007fffff
    target = mantissa * (2 ** (8 * (exponent - 3)))
    return target


def target_to_difficulty(target: int) -> float:
    """Convert target to network difficulty."""
    if target <= 0:
        return 0.0
    return TARGET_FOR_DIFFICULTY_1 / target


# ================================================

class StratumProxy:
    """Прокси-сервер для перехвата и логирования Stratum трафика"""

    def __init__(self, proxy_host: str, proxy_port: int, pool_host: str, pool_port: int):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.pool_host = pool_host
        self.pool_port = pool_port

        # Подключения
        self.asic_reader: Optional[asyncio.StreamReader] = None
        self.asic_writer: Optional[asyncio.StreamWriter] = None
        self.pool_reader: Optional[asyncio.StreamReader] = None
        self.pool_writer: Optional[asyncio.StreamWriter] = None

        # Счетчики
        self.message_counter = 0
        self.share_counter = 0
        self.notify_counter = 0
        self.setdiff_counter = 0
        self.start_time = datetime.now(UTC)

        # Статистика по сложности
        self.current_difficulty: float = 0.0
        self.last_share_time: Optional[datetime] = None
        self.last_message_time: Optional[datetime] = None
        self.last_notify_time: Optional[datetime] = None
        self.last_setdiff_time: Optional[datetime] = None

        # Статус
        self.connected_to_pool = False
        self.asic_authorized = False
        self.miner_address = "unknown"

        # Детектор молчания
        self.silence_warning_sent = False

        # Лог-файлы
        self.log_file = None
        self.notify_file = None
        self.setdiff_file = None
        self.subscribe_file = None

        # ===== КЭШ NOTIFY =====
        self.job_cache: Dict[str, dict] = {}

        # ===== NETWORK TARGET =====
        self.network_target: Optional[int] = None
        self.network_difficulty: Optional[float] = None

        # ===== EXTRANONCE1 =====
        self.extranonce1: str = ""
        self.extranonce2_size: int = 4

        # ===== BEST SHARE =====
        self.best_share_difficulty: float = 0.0
        self.best_share_hash: str = ""
        self.best_share_nonce: str = ""
        self.best_share_time: Optional[datetime] = None

        print(f"\n{'=' * 70}")
        print(f"🔍 STRATUM PROXY v2 STARTED")
        print(f"📡 ASIC ←→ PROXY ←→ POOL")
        print(f"   Прокси слушает на:    {self.proxy_host}:{self.proxy_port}")
        print(f"   Подключение к пулу:   {self.pool_host}:{self.pool_port}")
        print(f"   Логи в файл:          {LOG_FILE}")
        print(f"   Время старта:         {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 70}\n")

    async def open_log_file(self):
        self.log_file = open(LOG_FILE, 'a', encoding='utf-8')
        self.notify_file = open(NOTIFY_LOG_FILE, 'a', encoding='utf-8')
        self.setdiff_file = open(SETDIFF_LOG_FILE, 'a', encoding='utf-8')
        self.subscribe_file = open(SUBSCRIBE_LOG_FILE, 'a', encoding='utf-8')

    async def close_log_file(self):
        for f in [self.log_file, self.notify_file, self.setdiff_file, self.subscribe_file]:
            if f:
                f.close()

    def _now_str(self) -> str:
        return datetime.now(UTC).strftime('%H:%M:%S.%f')[:-3]

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def log(self, message: str, data: Any = None):
        timestamp = self._now_str()
        prefix = f"[{timestamp}] {message}"

        if data is not None:
            if isinstance(data, (dict, list)):
                log_entry = prefix + "\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            else:
                log_entry = prefix + " " + str(data) + "\n"
        else:
            log_entry = prefix + "\n"

        print(log_entry.strip())

        if self.log_file and not self.log_file.closed:
            try:
                self.log_file.write(log_entry)
                self.log_file.flush()
            except ValueError as e:
                print(f"⚠️ Не удалось записать в лог: {e}", flush=True)

    def log_notify(self, direction: str, message: dict):
        """Сохранить mining.notify в отдельный файл — ВСЕ ПОЛЯ."""
        if not self.notify_file:
            return
        try:
            params = message.get("params", [])
            record = {
                "ts": self._now_iso(),
                "direction": direction,
                "job_id": params[0] if len(params) > 0 else None,
                "prevhash": params[1] if len(params) > 1 else None,
                "coinb1": params[2] if len(params) > 2 else None,
                "coinb2": params[3] if len(params) > 3 else None,
                "merkle_branch": params[4] if len(params) > 4 else None,
                "version": params[5] if len(params) > 5 else None,
                "nbits": params[6] if len(params) > 6 else None,
                "ntime": params[7] if len(params) > 7 else None,
                "clean_jobs": params[8] if len(params) > 8 else None,
            }
            self.notify_file.write(json.dumps(record) + "\n")
            self.notify_file.flush()
        except Exception as e:
            self.log(f"⚠️ Ошибка записи notify: {e}")

    def log_setdiff(self, direction: str, message: dict):
        if not self.setdiff_file:
            return
        try:
            params = message.get("params", [])
            record = {
                "ts": self._now_iso(),
                "direction": direction,
                "difficulty": params[0] if params else None,
            }
            self.setdiff_file.write(json.dumps(record) + "\n")
            self.setdiff_file.flush()
        except Exception as e:
            self.log(f"⚠️ Ошибка записи setdiff: {e}")

    def log_subscribe(self, result: list):
        """Сохранить ответ на mining.subscribe."""
        if not self.subscribe_file:
            return
        try:
            record = {
                "ts": self._now_iso(),
                "direction": "POOL→ASIC",
                "extranonce1": self.extranonce1,
                "extranonce2_size": self.extranonce2_size,
                "raw_result": result,
            }
            self.subscribe_file.write(json.dumps(record) + "\n")
            self.subscribe_file.flush()
        except Exception as e:
            self.log(f"⚠️ Ошибка записи subscribe: {e}")

    def log_message(self, direction: str, message: dict):
        if not LOG_ALL_MESSAGES:
            return

        method = message.get("method", "unknown")
        msg_id = message.get("id", "null")
        params = message.get("params", [])
        result = message.get("result", None)
        error = message.get("error", None)

        msg_type = method if method else ("RESPONSE" if result is not None else ("ERROR" if error is not None else "UNKNOWN"))

        # ===== НЕ ОБРЕЗАЕМ notify и submit =====
        # params_preview = params (полностью)
        params_preview = params

        log_entry = {
            "direction": direction,
            "type": msg_type,
            "id": msg_id,
            "params": params_preview,
        }
        if result is not None:
            log_entry["result"] = result
        if error is not None:
            log_entry["error"] = error

        emoji = "📨"
        if method == "mining.subscribe": emoji = "📡"
        elif method == "mining.authorize": emoji = "🔐"
        elif method == "mining.suggest_difficulty": emoji = "🎯"
        elif method == "mining.set_difficulty": emoji = "📊"
        elif method == "mining.notify": emoji = "📤"
        elif method == "mining.submit": emoji = "⛏️"
        elif method == "mining.configure": emoji = "⚙️"

        # Отдельные файлы
        if method == "mining.notify":
            self.log_notify(direction, message)
        elif method == "mining.set_difficulty":
            self.log_setdiff(direction, message)

        if method == "mining.submit" and not LOG_SHARE_DETAILS:
            worker = params[0] if params else "unknown"
            job_id = params[1] if len(params) > 1 else "unknown"
            self.log(f"{emoji} {direction} SHARE: worker={worker[:20]}..., job={job_id}")
        else:
            self.log(f"{emoji} {direction} {msg_type}", log_entry)

    # ====================================================================
    # ===== РАСЧЁТ ХЭША ШАРА И СЛОЖНОСТИ =====
    # ====================================================================

    def _calculate_share_hash(self, job_id: str, extra_nonce2: str, ntime: str, nonce: str) -> Optional[str]:
        """
        Расчёт хэша шара.

        coinbase = coinb1 + extranonce1 + extranonce2 + coinb2
        """
        job = self.job_cache.get(job_id)
        if not job:
            self.log(f"⚠️ job_id={job_id} не найден в кэше — хэш не посчитать")
            return None

        if not self.extranonce1:
            self.log("⚠️ extranonce1 пуст — хэш будет неверным!")

        try:
            coinb1 = job["coinb1"]
            coinb2 = job["coinb2"]
            merkle_branch = job["merkle_branch"]
            version = job["version"]
            prevhash = job["prevhash"]
            nbits = job["nbits"]

            # 1. Coinbase
            coinbase_hex = coinb1 + self.extranonce1 + extra_nonce2 + coinb2
            coinbase_bytes = bytes.fromhex(coinbase_hex)

            # 2. Merkle root
            coinbase_hash = hashlib.sha256(hashlib.sha256(coinbase_bytes).digest()).digest()
            current_hash = coinbase_hash  # LE

            for branch_hex in merkle_branch:
                branch_bytes = bytes.fromhex(branch_hex)[::-1]  # BE -> LE
                concat = current_hash + branch_bytes
                current_hash = hashlib.sha256(hashlib.sha256(concat).digest()).digest()

            merkle_root_le = current_hash

            # 3. Header (все поля в LE)
            version_bytes = bytes.fromhex(version)[::-1]
            prevhash_bytes = bytes.fromhex(prevhash)[::-1]
            ntime_bytes = bytes.fromhex(ntime)[::-1]
            nbits_bytes = bytes.fromhex(nbits)[::-1]
            nonce_bytes = bytes.fromhex(nonce)[::-1]

            header = (
                version_bytes +
                prevhash_bytes +
                merkle_root_le +   # ← LE, а не BE
                ntime_bytes +
                nbits_bytes +
                nonce_bytes
            )

            if len(header) != 80:
                self.log(f"⚠️ header len = {len(header)}, ожидалось 80")
                return None

            # 4. Hash
            block_hash = hashlib.sha256(hashlib.sha256(header).digest()).digest()
            return block_hash[::-1].hex()  # BE hex

        except Exception as e:
            self.log(f"⚠️ Ошибка расчёта хэша шара: {e}")
            traceback.print_exc()
            return None

    def _calculate_share_difficulty(self, hash_hex: str) -> float:
        """share_difficulty = network_target / hash_int"""
        if self.network_target is None:
            return 0.0
        try:
            hash_int = int(hash_hex, 16)
            if hash_int == 0:
                return 0.0
            return self.network_target / hash_int
        except Exception:
            return 0.0

    # ====================================================================

    async def start(self):
        try:
            await self.open_log_file()

            self.log("🚀 Запуск Stratum Proxy v2...")

            self.log(f"🔌 Подключение к пулу {self.pool_host}:{self.pool_port}...")
            try:
                self.pool_reader, self.pool_writer = await asyncio.wait_for(
                    asyncio.open_connection(self.pool_host, self.pool_port),
                    timeout=10.0
                )
                self.connected_to_pool = True
                self.log("✅ Подключение к пулу установлено!")
            except asyncio.TimeoutError:
                self.log("❌ Таймаут подключения к пулу!")
                return
            except Exception as e:
                self.log(f"❌ Ошибка подключения к пулу: {e}")
                return

            self.log(f"📡 Запуск прокси-сервера на {self.proxy_host}:{self.proxy_port}...")
            server = await asyncio.start_server(
                self.handle_asic,
                self.proxy_host,
                self.proxy_port
            )

            self.log(f"✅ Прокси-сервер запущен! ASIC подключайтесь к порту {self.proxy_port}\n")
            self.log("=" * 70)
            self.log("ОЖИДАНИЕ ПОДКЛЮЧЕНИЯ ASIC...")
            self.log("=" * 70)

            asyncio.create_task(self.read_from_pool())
            asyncio.create_task(self.monitor_asic_silence())
            asyncio.create_task(self.monitor_notify_gap())

            async with server:
                await server.serve_forever()

        except KeyboardInterrupt:
            self.log("\n🛑 Остановка прокси по Ctrl+C...")
        except Exception as e:
            self.log(f"❌ Критическая ошибка: {e}")
            traceback.print_exc()
        finally:
            await self.close_log_file()
            if self.pool_writer:
                self.pool_writer.close()
                await self.pool_writer.wait_closed()

    async def handle_asic(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.asic_reader = reader
        self.asic_writer = writer

        addr = writer.get_extra_info('peername')
        self.log(f"\n🔌 ASIC подключился: {addr}")

        self.log("🔄 Переподключение к пулу...")
        try:
            self.pool_reader, self.pool_writer = await asyncio.wait_for(
                asyncio.open_connection(self.pool_host, self.pool_port),
                timeout=10.0
            )
            self.connected_to_pool = True
            self.log("✅ Подключение к пулу восстановлено!")
            asyncio.create_task(self.read_from_pool())
        except Exception as e:
            self.log(f"❌ Ошибка подключения к пулу: {e}")
            writer.close()
            return

        try:
            while True:
                try:
                    data = await reader.readline()
                    if not data:
                        self.log("🔌 ASIC отключился (нет данных)")
                        break

                    try:
                        message = json.loads(data.decode().strip())
                        await self.handle_asic_message(message)
                    except json.JSONDecodeError as e:
                        self.log(f"⚠️ Невалидный JSON от ASIC: {data[:100]}, error: {e}")

                except ConnectionResetError:
                    self.log("🔌 ASIC разорвал соединение")
                    break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.log(f"❌ Ошибка чтения от ASIC: {e}")
                    break

        except Exception as e:
            self.log(f"❌ Ошибка в handle_asic: {e}")
        finally:
            self.log("🔌 ASIC отключен")
            if self.pool_writer:
                self.pool_writer.close()
                await self.pool_writer.wait_closed()
                self.connected_to_pool = False

    async def handle_asic_message(self, message: dict):
        self.message_counter += 1
        self.last_message_time = datetime.now(UTC)

        method = message.get("method")
        _msg_id = message.get("id")

        if SHOW_ASIC_TO_POOL:
            self.log_message("ASIC→POOL", message)

        if self.pool_writer and self.connected_to_pool:
            try:
                self.pool_writer.write((json.dumps(message) + "\n").encode())
                await self.pool_writer.drain()
            except Exception as e:
                self.log(f"❌ Ошибка отправки в пул: {e}")
                self.connected_to_pool = False
        else:
            self.log(f"⚠️ Нет соединения с пулом, сообщение {method} не отправлено")

        if method == "mining.authorize":
            if message.get("params"):
                username = message["params"][0] if message["params"] else "unknown"
                self.miner_address = username
                self.asic_authorized = True
                self.log(f"🔐 ASIC авторизован: {username}")

        elif method == "mining.suggest_difficulty":
            if message.get("params"):
                suggested = message["params"][0]
                self.log(f"🎯 ASIC предложил сложность: {suggested}")

        elif method == "mining.submit":
            self.share_counter += 1
            self.last_share_time = datetime.now(UTC)
            self.silence_warning_sent = False

            params = message.get("params", [])
            if len(params) >= 5:
                job_id = params[1]
                nonce = params[4]
                self.log(f"⛏️ SHARE #{self.share_counter} @ {self._now_str()}: job={job_id}, nonce={nonce}")

            if SAVE_SHARES_TO_FILE:
                self.log_share_to_file(message, "ASIC→POOL")

        elif method == "mining.subscribe":
            self.log(f"📡 ASIC подписался на уведомления")

    def log_share_to_file(self, message: dict, direction: str):
        if direction != "ASIC→POOL":
            return
        params = message.get("params", [])
        if len(params) < 5:
            return

        worker = params[0]
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        share_hash = self._calculate_share_hash(job_id, extra_nonce2, ntime, nonce)
        share_difficulty = 0.0
        hash_int = 0
        best_share_molehole = 0.0

        if share_hash:
            hash_int = int(share_hash, 16)
            share_difficulty = self._calculate_share_difficulty(share_hash)

            if self.network_difficulty:
                best_share_molehole = share_difficulty * self.network_difficulty

            if share_difficulty > self.best_share_difficulty:
                self.best_share_difficulty = share_difficulty
                self.best_share_hash = share_hash
                self.best_share_nonce = nonce
                self.best_share_time = datetime.now(UTC)
                self.log(
                    f"🏆 NEW BEST SHARE! "
                    f"share_difficulty={share_difficulty:.6e}, "
                    f"hash_int={hash_int:.6e}, "
                    f"network_target={self.network_target:.6e}, "
                    f"network_difficulty={self.network_difficulty:.2f}, "
                    f"share_diff_x_net_diff={best_share_molehole:.6e}, "
                    f"hash={share_hash[:16]}..., nonce={nonce}"
                )

        job = self.job_cache.get(job_id)
        share_data = {
            "ts": self._now_iso(),
            "worker": worker,
            "job_id": job_id,
            "extra_nonce2": extra_nonce2,
            "ntime": ntime,
            "nonce": nonce,
            "extranonce1": self.extranonce1,
            "current_difficulty": self.current_difficulty,
            "network_target": self.network_target,
            "network_target_hex": hex(self.network_target) if self.network_target else None,
            "network_difficulty": self.network_difficulty,
            "hash": share_hash,
            "hash_int": hash_int,
            "share_difficulty": share_difficulty,
            "share_difficulty_x_network_difficulty": best_share_molehole,
            "best_share_difficulty": self.best_share_difficulty,
            # ===== ДАННЫЕ NOTIFY ДЛЯ ПЕРЕСЧЁТА =====
            "job_data": {
                "prevhash": job["prevhash"] if job else None,
                "coinb1": job["coinb1"] if job else None,
                "coinb2": job["coinb2"] if job else None,
                "merkle_branch": job["merkle_branch"] if job else None,
                "version": job["version"] if job else None,
                "nbits": job["nbits"] if job else None,
            } if job else None,
        }

        try:
            with open(SHARES_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(share_data) + "\n")
        except Exception as e:
            self.log(f"⚠️ Ошибка записи шара в файл: {e}")

    async def read_from_pool(self):
        self.log("📡 Запущен слушатель сообщений от пула")

        while True:
            try:
                data = await self.pool_reader.readline()
                if not data:
                    self.log("🔌 Пул закрыл соединение (EOF)")
                    break

                try:
                    message = json.loads(data.decode().strip())
                    await self.handle_pool_message(message)
                except json.JSONDecodeError as e:
                    self.log(f"⚠️ Невалидный JSON от пула: {data[:100]}, error: {e}")

            except ConnectionResetError:
                self.log("🔌 Пул разорвал соединение")
                break
            except asyncio.CancelledError:
                break
            except asyncio.LimitOverrunError as e:
                self.log(f"⚠️ LimitOverrunError: {e} — пропускаем сообщение, продолжаем")
                await asyncio.sleep(0.1)
                continue
            except Exception as e:
                self.log(f"❌ Ошибка чтения от пула: {e}")
                await asyncio.sleep(0.1)
                continue

    async def handle_pool_message(self, message: dict):
        self.message_counter += 1
        method = message.get("method")
        _msg_id = message.get("id")
        result = message.get("result")
        error = message.get("error")

        if SHOW_POOL_TO_ASIC:
            self.log_message("POOL→ASIC", message)

        if error is not None:
            self.log(f"❌ ПУЛ ВЕРНУЛ ОШИБКУ: {error}")

        # ===== Ответ на mining.subscribe =====
        if result is not None and method is None:
            if isinstance(result, list) and len(result) >= 3:
                if isinstance(result[0], list) and isinstance(result[1], str):
                    self.extranonce1 = result[1]
                    self.extranonce2_size = result[2]
                    self.log(f"📡 ПОЛУЧЕН extranonce1={self.extranonce1}, extranonce2_size={self.extranonce2_size}")
                    self.log_subscribe(result)

        # ===== set_difficulty =====
        if method == "mining.set_difficulty":
            params = message.get("params", [])
            if params:
                self.current_difficulty = params[0]
                self.last_setdiff_time = datetime.now(UTC)
                self.setdiff_counter += 1
                self.log(f"📊 POOL→ASIC set_difficulty #{self.setdiff_counter}: {params[0]}")

        # ===== notify =====
        elif method == "mining.notify":
            self.last_notify_time = datetime.now(UTC)
            self.notify_counter += 1
            params = message.get("params", [])
            if params and len(params) >= 9:
                job_id = params[0]
                nbits = params[6]

                try:
                    new_network_target = nbits_to_target(nbits)
                    new_network_difficulty = target_to_difficulty(new_network_target)

                    if self.network_target != new_network_target:
                        self.network_target = new_network_target
                        self.network_difficulty = new_network_difficulty
                        self.log(
                            f"🎯 NETWORK UPDATED: nbits={nbits}, "
                            f"target={new_network_target:#066x}, "
                            f"difficulty={new_network_difficulty:.2f}"
                        )
                except Exception as e:
                    self.log(f"⚠️ Ошибка вычисления network_target из nbits={nbits}: {e}")

                self.job_cache[job_id] = {
                    "prevhash": params[1],
                    "coinb1": params[2],
                    "coinb2": params[3],
                    "merkle_branch": params[4],
                    "version": params[5],
                    "nbits": params[6],
                    "ntime": params[7],
                }
                if len(self.job_cache) > 100:
                    keys = list(self.job_cache.keys())
                    for k in keys[:50]:
                        self.job_cache.pop(k, None)

                self.log(f"📤 POOL→ASIC notify #{self.notify_counter}: job_id={job_id}")

        if self.asic_writer:
            try:
                self.asic_writer.write((json.dumps(message) + "\n").encode())
                await self.asic_writer.drain()
            except Exception as e:
                self.log(f"❌ Ошибка отправки ASIC: {e}")

    async def monitor_asic_silence(self):
        self.log("👁️ Запущен монитор молчания ASIC")

        while True:
            try:
                await asyncio.sleep(SILENCE_CHECK_INTERVAL_SEC)
                now = datetime.now(UTC)

                if self.last_share_time is not None:
                    reference = self.last_share_time
                    ref_name = "last_share"
                elif self.last_message_time is not None:
                    reference = self.last_message_time
                    ref_name = "last_message"
                else:
                    continue

                time_since = (now - reference).total_seconds()

                if time_since > SILENCE_THRESHOLD_SEC:
                    if not self.silence_warning_sent:
                        self.log("=" * 70)
                        self.log(f"⚠️⚠️⚠️ ASIC МОЛЧИТ УЖЕ {time_since:.1f}с (по {ref_name}) ⚠️⚠️⚠️")
                        self.log(f"   Всего шаров:        {self.share_counter}")
                        self.log(f"   Последний шар:      {self.last_share_time}")
                        self.log(f"   Последнее сообщение:{self.last_message_time}")
                        self.log(f"   Последний notify:   {self.last_notify_time}")
                        self.log(f"   Последний setdiff:  {self.last_setdiff_time}")
                        self.log(f"   Текущая сложность:  {self.current_difficulty}")
                        self.log(f"   ASIC closing:       {self.asic_writer.is_closing() if self.asic_writer else 'N/A'}")
                        self.log(f"   Пул connected:      {self.connected_to_pool}")
                        self.log("=" * 70)
                        self.silence_warning_sent = True
                else:
                    if self.silence_warning_sent:
                        self.log(f"✅ ASIC снова активен! (молчал {time_since:.1f}с)")
                        self.silence_warning_sent = False

            except asyncio.CancelledError:
                self.log("👁️ Монитор молчания ASIC остановлен")
                break
            except Exception as e:
                self.log(f"❌ Ошибка в мониторе молчания: {e}")

    async def monitor_notify_gap(self):
        self.log("👁️ Запущен монитор notify gap")
        NOTIFY_GAP_THRESHOLD = 60.0

        while True:
            try:
                await asyncio.sleep(10)
                if self.last_notify_time is None:
                    continue
                now = datetime.now(UTC)
                gap = (now - self.last_notify_time).total_seconds()
                if gap > NOTIFY_GAP_THRESHOLD:
                    self.log(f"⚠️ Пул не шлёт notify уже {gap:.1f}с!")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log(f"❌ Ошибка в мониторе notify gap: {e}")

    def get_stats(self) -> dict:
        uptime = (datetime.now(UTC) - self.start_time).total_seconds()
        return {
            "uptime_seconds": uptime,
            "messages_total": self.message_counter,
            "shares_total": self.share_counter,
            "notify_total": self.notify_counter,
            "setdiff_total": self.setdiff_counter,
            "current_difficulty": self.current_difficulty,
            "asic_authorized": self.asic_authorized,
            "miner_address": self.miner_address,
            "connected_to_pool": self.connected_to_pool,
            "extranonce1": self.extranonce1,
            "extranonce2_size": self.extranonce2_size,
            "network_target": hex(self.network_target) if self.network_target else None,
            "network_target_int": self.network_target,
            "network_difficulty": self.network_difficulty,
            "best_share_difficulty": self.best_share_difficulty,
            "best_share_hash": self.best_share_hash[:16] + "..." if self.best_share_hash else None,
            "best_share_nonce": self.best_share_nonce,
            "best_share_time": self.best_share_time.isoformat() if self.best_share_time else None,
        }


async def main():
    proxy = StratumProxy(PROXY_HOST, PROXY_PORT, POOL_HOST, POOL_PORT)
    try:
        await proxy.start()
    except KeyboardInterrupt:
        print("\n🛑 Прокси остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        traceback.print_exc()
    finally:
        print("\n" + "=" * 70)
        print("📊 СТАТИСТИКА РАБОТЫ ПРОКСИ v2")
        stats = proxy.get_stats()
        print(f"   Время работы:           {stats['uptime_seconds']:.0f} сек")
        print(f"   Всего сообщений:        {stats['messages_total']}")
        print(f"   Всего шаров:            {stats['shares_total']}")
        print(f"   Всего notify от пула:   {stats['notify_total']}")
        print(f"   Всего setdiff от пула:  {stats['setdiff_total']}")
        print(f"   Текущая сложность:      {stats['current_difficulty']}")
        print(f"   ASIC авторизован:       {stats['asic_authorized']}")
        print(f"   Адрес майнера:          {stats['miner_address'][:30]}...")
        print(f"   Подключен к пулу:       {stats['connected_to_pool']}")
        print("=" * 70)
        print(f"   📡 EXTRANONCE1:         {stats['extranonce1']}")
        print(f"   📡 EXTRANONCE2_SIZE:    {stats['extranonce2_size']}")
        print("=" * 70)
        print(f"   🎯 NETWORK TARGET:      {stats['network_target']}")
        print(f"   🎯 NETWORK DIFFICULTY:  {stats['network_difficulty']}")
        print("=" * 70)
        print(f"   🏆 BEST SHARE:          {stats['best_share_difficulty']:.6e}")
        print(f"      hash:                 {stats['best_share_hash']}")
        print(f"      nonce:                {stats['best_share_nonce']}")
        print(f"      time:                 {stats['best_share_time']}")
        print("=" * 70)
        print("\n📁 Логи сохранены в:")
        print(f"   - {LOG_FILE} (все сообщения)")
        print(f"   - {SHARES_LOG_FILE} (только шары + hash + difficulty + job_data)")
        print(f"   - {NOTIFY_LOG_FILE} (все notify от пула, полные)")
        print(f"   - {SETDIFF_LOG_FILE} (все set_difficulty от пула)")
        print(f"   - {SUBSCRIBE_LOG_FILE} (extranonce1, extranonce2_size)")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())