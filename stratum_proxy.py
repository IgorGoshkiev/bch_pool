#!/usr/bin/env python3
"""
Stratum Proxy для отладки взаимодействия ASIC ↔ Пул.
Перехватывает все сообщения и логирует их в структурированном виде.

Как использовать:
1. Настройте ASIC подключаться к IP_вашего_сервера:4444
2. Прокси подключится к Molehole (или другому пулу)
3. Все сообщения логируются в proxy.log

Пример:
  ASIC (порт 3333) → ПРОКСИ (порт 4444) → Molehole (stratum+tcp://stratum.molepool.com:3333)
"""

import asyncio
import json
from datetime import datetime, UTC
from typing import Optional, Any
import traceback

# ========== КОНФИГУРАЦИЯ ==========
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 4444  # Порт, на котором прокси ждет ASIC

# НАСТРОЙКИ РЕАЛЬНОГО ПУЛА (Molehole)
POOL_HOST = "eu.molepool.com"  # Замените на реальный адрес
POOL_PORT = 5566  # Стандартный порт для stratum+tcp


# ========== НАСТРОЙКИ ЛОГИРОВАНИЯ ==========
LOG_FILE = "proxy.log"
LOG_ALL_MESSAGES = True  # Логировать все сообщения
LOG_SHARE_DETAILS = True  # Логировать детали шаров (хеши, сложность)
SHOW_ASIC_TO_POOL = True  # Показывать сообщения ASIC → Пул
SHOW_POOL_TO_ASIC = True  # Показывать сообщения Пул → ASIC
SAVE_SHARES_TO_FILE = True  # Сохранять шары в отдельный файл для анализа


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
        self.block_counter = 0
        self.start_time = datetime.now(UTC)

        # Статистика по сложности
        self.difficulty_history: list = []
        self.share_timestamps: list = []
        self.current_difficulty: float = 0.0
        self.last_difficulty_update: Optional[datetime] = None

        # Статус
        self.connected_to_pool = False
        self.asic_authorized = False
        self.miner_address = "unknown"

        # Лог-файл
        self.log_file = None

        print(f"\n{'=' * 70}")
        print(f"🔍 STRATUM PROXY STARTED")
        print(f"📡 ASIC ←→ PROXY ←→ POOL")
        print(f"   Прокси слушает на:    {self.proxy_host}:{self.proxy_port}")
        print(f"   Подключение к пулу:   {self.pool_host}:{self.pool_port}")
        print(f"   Логи в файл:          {LOG_FILE}")
        print(f"   Время старта:         {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 70}\n")

    async def open_log_file(self):
        """Открыть лог-файл для записи"""
        self.log_file = open(LOG_FILE, 'a', encoding='utf-8')

    async def close_log_file(self):
        """Закрыть лог-файл"""
        if self.log_file:
            self.log_file.close()

    def log(self, message: str, data: Any = None):
        """Запись в лог-файл и консоль"""
        timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        if data is not None:
            log_entry = f"[{timestamp}] {message}\n"
            if isinstance(data, dict) or isinstance(data, list):
                log_entry += json.dumps(data, indent=2, ensure_ascii=False)
            else:
                log_entry += str(data)
            log_entry += "\n"
        else:
            log_entry = f"[{timestamp}] {message}\n"

        # В консоль
        print(log_entry.strip())

        # В файл
        if self.log_file:
            self.log_file.write(log_entry)
            self.log_file.flush()

    def log_message(self, direction: str, message: dict):
        """Логирование Stratum сообщения"""
        if not LOG_ALL_MESSAGES:
            return

        method = message.get("method", "unknown")
        msg_id = message.get("id", "null")
        params = message.get("params", [])
        result = message.get("result", None)
        error = message.get("error", None)

        # Определяем тип сообщения
        msg_type = "UNKNOWN"
        if method:
            msg_type = method
        elif result is not None:
            msg_type = "RESPONSE"
        elif error is not None:
            msg_type = "ERROR"

        # Сокращаем длинные параметры для читаемости
        params_preview = params
        if isinstance(params, list):
            if len(params) > 0:
                # Для mining.notify сокращаем длинные hex строки
                if method == "mining.notify" and len(params) >= 3:
                    params_preview = []
                    for i, p in enumerate(params):
                        if i == 0:  # job_id
                            params_preview.append(p)
                        elif i == 1:  # prevhash
                            params_preview.append(p[:16] + "..." if len(p) > 16 else p)
                        elif i == 2:  # coinb1
                            params_preview.append(p[:30] + "..." if len(p) > 30 else p)
                        elif i == 3:  # coinb2
                            params_preview.append(p[:30] + "..." if len(p) > 30 else p)
                        elif i == 4:  # merkle_branch
                            params_preview.append(f"[{len(p)} items]")
                        else:
                            params_preview.append(p)
                elif method == "mining.submit" and len(params) >= 5:
                    params_preview = [
                        params[0],  # worker
                        params[1],  # job_id
                        params[2][:16] + "..." if len(params[2]) > 16 else params[2],  # extra_nonce2
                        params[3],  # ntime
                        params[4],  # nonce
                    ]
                else:
                    params_preview = params

        # Формируем сообщение
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

        # Подсветка важных сообщений
        emoji = "📨"
        if method == "mining.subscribe":
            emoji = "📡"
        elif method == "mining.authorize":
            emoji = "🔐"
        elif method == "mining.suggest_difficulty":
            emoji = "🎯"
        elif method == "mining.set_difficulty":
            emoji = "📊"
        elif method == "mining.notify":
            emoji = "📤"
        elif method == "mining.submit":
            emoji = "⛏️"
        elif method == "mining.configure":
            emoji = "⚙️"

        # Специальная обработка для submit - показываем кратко в консоли
        if method == "mining.submit" and not LOG_SHARE_DETAILS:
            # Короткий лог в консоль
            worker = params[0] if params else "unknown"
            job_id = params[1] if len(params) > 1 else "unknown"
            self.log(f"{emoji} {direction} SHARE: worker={worker[:20]}..., job={job_id}")
        else:
            # Полный лог в консоль и файл
            self.log(f"{emoji} {direction} {msg_type}", log_entry)

        # Отдельно логируем шары в файл для анализа
        if method == "mining.submit" and SAVE_SHARES_TO_FILE:
            self.log_share_to_file(message, direction)

    def log_share_to_file(self, message: dict, direction: str):
        """Сохранение шаров в отдельный файл для анализа"""
        if direction != "ASIC→POOL":
            return

        params = message.get("params", [])
        if len(params) < 5:
            return

        share_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "worker": params[0],
            "job_id": params[1],
            "extra_nonce2": params[2],
            "ntime": params[3],
            "nonce": params[4],
            "difficulty": self.current_difficulty,
        }

        # Записываем в файл shares.jsonl
        try:
            with open("shares.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(share_data) + "\n")
        except Exception as e:
            self.log(f"⚠️ Ошибка записи шара в файл: {e}")

    async def start(self):
        """Запуск прокси"""
        try:
            await self.open_log_file()

            self.log("🚀 Запуск Stratum Proxy...")

            # 1. Подключаемся к реальному пулу
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

            # 2. Запускаем сервер для ASIC
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

            # 3. Запускаем фоновую задачу для чтения от пула
            asyncio.create_task(self.read_from_pool())

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
        """Обработка подключения ASIC"""
        self.asic_reader = reader
        self.asic_writer = writer

        addr = writer.get_extra_info('peername')
        self.log(f"\n🔌 ASIC подключился: {addr}")

        # ===== ПОДКЛЮЧАЕМСЯ К ПУЛУ ЗАНОВО =====
        self.log("🔄 Переподключение к пулу...")
        try:
            self.pool_reader, self.pool_writer = await asyncio.wait_for(
                asyncio.open_connection(self.pool_host, self.pool_port),
                timeout=10.0
            )
            self.connected_to_pool = True
            self.log("✅ Подключение к пулу восстановлено!")

            # Запускаем слушатель сообщений от пула
            asyncio.create_task(self.read_from_pool())

        except Exception as e:
            self.log(f"❌ Ошибка подключения к пулу: {e}")
            writer.close()
            return

        try:
            # Читаем сообщения от ASIC и пересылаем в пул
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
            # Закрываем соединение с пулом
            if self.pool_writer:
                self.pool_writer.close()
                await self.pool_writer.wait_closed()
                self.connected_to_pool = False

    async def handle_asic_message(self, message: dict):
        """Обработка сообщения от ASIC"""
        self.message_counter += 1
        method = message.get("method")
        _msg_id = message.get("id")

        if SHOW_ASIC_TO_POOL:
            self.log_message("ASIC→POOL", message)

        # ===== ПЕРЕСЫЛАЕМ В ПУЛ =====
        if self.pool_writer and self.connected_to_pool:
            try:
                self.pool_writer.write((json.dumps(message) + "\n").encode())
                await self.pool_writer.drain()
                self.log(f"📤 Переслано в пул: {method}")
            except Exception as e:
                self.log(f"❌ Ошибка отправки в пул: {e}")
                self.connected_to_pool = False
        else:
            self.log(f"⚠️ Нет соединения с пулом, сообщение {method} не отправлено")

        # Логируем важные сообщения
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
            params = message.get("params", [])
            if len(params) >= 5:
                job_id = params[1]
                nonce = params[4]
                self.share_timestamps.append(datetime.now(UTC))
                if len(self.share_timestamps) > 1000:
                    self.share_timestamps = self.share_timestamps[-1000:]

                if len(self.share_timestamps) > 1:
                    last_10 = self.share_timestamps[-10:]
                    if len(last_10) >= 2:
                        intervals = []
                        for i in range(1, len(last_10)):
                            diff = (last_10[i] - last_10[i - 1]).total_seconds()
                            if 0.01 < diff < 60:
                                intervals.append(diff)
                        if intervals:
                            avg_interval = sum(intervals) / len(intervals)
                            self.log(f"⛏️ ШАР #{self.share_counter}: job={job_id}, nonce={nonce}, "
                                     f"средний интервал={avg_interval:.3f}s, сложность={self.current_difficulty}")

        elif method == "mining.subscribe":
            self.log(f"📡 ASIC подписался на уведомления")

    async def read_from_pool(self):
        """Чтение сообщений от пула и пересылка ASIC"""
        self.log("📡 Запущен слушатель сообщений от пула")

        while True:
            try:
                data = await self.pool_reader.readline()
                if not data:
                    self.log("🔌 Пул закрыл соединение (EOF)")
                    # ===== ДОБАВЛЯЕМ =====
                    self.log("⚠️ Возможно пул не поддерживает mining.configure")
                    self.log("⚠️ Попробуйте отключить mining.configure в ASIC")
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
            except Exception as e:
                self.log(f"❌ Ошибка чтения от пула: {e}")
                break

    async def handle_pool_message(self, message: dict):
        """Обработка сообщения от пула"""
        self.message_counter += 1
        method = message.get("method")
        _msg_id = message.get("id")
        result = message.get("result")
        error = message.get("error")

        # Логируем входящее сообщение
        if SHOW_POOL_TO_ASIC:
            self.log_message("POOL→ASIC", message)

        # ===== ДОБАВЛЯЕМ ДИАГНОСТИКУ =====
        # Если пул закрывает соединение - логируем причину
        if error is not None:
            self.log(f"❌ ПУЛ ВЕРНУЛ ОШИБКУ: {error}")
        if result is not None:
            self.log(f"✅ ПУЛ ВЕРНУЛ РЕЗУЛЬТАТ: {result}")

        # ===== ВАЖНО: ЕСЛИ ПУЛ НЕ ОТВЕЧАЕТ НА configure =====
        # Некоторые пулы не поддерживают mining.configure
        # В этом случае нужно ответить ASIC самим
        if method == "mining.configure" and result is None and error is None:
            # Пул не ответил на configure, отправляем стандартный ответ сами
            self.log("⚙️ Пул не ответил на configure, отправляем ответ от прокси")
            response = {
                "id": _msg_id,
                "result": {
                    "version-rolling": True,
                    "version-rolling.mask": "1fffe000",
                    "minimum-difficulty": 1
                },
                "error": None
            }
            if self.asic_writer:
                try:
                    self.asic_writer.write((json.dumps(response) + "\n").encode())
                    await self.asic_writer.drain()
                    self.log("✅ Ответ на configure отправлен ASIC от прокси")
                except Exception as e:
                    self.log(f"❌ Ошибка отправки ответа ASIC: {e}")
            return  # Не пересылаем в пул, так как он уже закрыл соединение

        # Пересылаем ASIC
        if self.asic_writer:
            try:
                self.asic_writer.write((json.dumps(message) + "\n").encode())
                await self.asic_writer.drain()
            except Exception as e:
                self.log(f"❌ Ошибка отправки ASIC: {e}")



    def get_stats(self) -> dict:
        """Получить статистику прокси"""
        uptime = (datetime.now(UTC) - self.start_time).total_seconds()
        return {
            "uptime_seconds": uptime,
            "messages_total": self.message_counter,
            "shares_total": self.share_counter,
            "current_difficulty": self.current_difficulty,
            "asic_authorized": self.asic_authorized,
            "miner_address": self.miner_address,
            "difficulty_updates": len(self.difficulty_history),
            "connected_to_pool": self.connected_to_pool,
        }


# ========== ЗАПУСК ==========
async def main():
    """Главная функция"""
    proxy = StratumProxy(PROXY_HOST, PROXY_PORT, POOL_HOST, POOL_PORT)
    try:
        await proxy.start()
    except KeyboardInterrupt:
        print("\n🛑 Прокси остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        traceback.print_exc()
    finally:
        # Выводим статистику при завершении
        print("\n" + "=" * 70)
        print("📊 СТАТИСТИКА РАБОТЫ ПРОКСИ")
        stats = proxy.get_stats()
        print(f"   Время работы:           {stats['uptime_seconds']:.0f} сек")
        print(f"   Всего сообщений:        {stats['messages_total']}")
        print(f"   Всего шаров:            {stats['shares_total']}")
        print(f"   Текущая сложность:      {stats['current_difficulty']}")
        print(f"   ASIC авторизован:       {stats['asic_authorized']}")
        print(f"   Адрес майнера:          {stats['miner_address'][:30]}...")
        print(f"   Обновлений сложности:   {stats['difficulty_updates']}")
        print(f"   Подключен к пулу:       {stats['connected_to_pool']}")
        print("=" * 70)

        # Сохраняем историю сложности
        if proxy.difficulty_history:
            try:
                with open("difficulty_history.json", "w") as f:
                    json.dump(proxy.difficulty_history, f, indent=2)
                print("   📊 История сложности сохранена в difficulty_history.json")
            except Exception as e:
                print(f"   ⚠️ Ошибка сохранения истории: {e}")

        print("\n📁 Логи сохранены в:")
        print(f"   - {LOG_FILE} (все сообщения)")
        print(f"   - shares.jsonl (только шары)")
        print(f"   - difficulty_history.json (история сложности)")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())