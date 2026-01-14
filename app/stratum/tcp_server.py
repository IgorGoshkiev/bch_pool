import asyncio
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class StratumTCPServer:
    """TCP Stratum сервер для ASIC майнеров"""

    def __init__(self, host: str = "0.0.0.0", port: int = 3333):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.connections: Dict[str, asyncio.StreamWriter] = {}
        self.miners: Dict[str, str] = {}  # writer_addr -> bch_address

        # Используем существующие компоненты
        from app.stratum.server import stratum_server as ws_server
        from app.stratum.validator import share_validator

        self.ws_server = ws_server  # Для переиспользования логики
        self.validator = share_validator

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
            logger.info(f'TCP Stratum сервер запущен на {addr[0]}:{addr[1]}')
            logger.info(f'ASIC подключайтесь: stratum+tcp://{self.host}:{self.port}')

            async with self.server:
                await self.server.serve_forever()

        except Exception as e:
            logger.error(f'Ошибка запуска TCP сервера: {e}')
            raise

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обработка подключения майнера"""
        addr = writer.get_extra_info('peername')
        client_id = f"{addr[0]}:{addr[1]}"

        logger.info(f'Новое TCP подключение от {client_id}')

        try:
            # 1. Отправляем приветствие (первое сообщение всегда от сервера)
            await self.send_welcome(writer)

            # 2. Обрабатываем входящие сообщения
            while True:
                try:
                    # Читаем строку (Stratum использует JSON-Line протокол)
                    data = await reader.readline()
                    if not data:
                        logger.info(f'Соединение закрыто клиентом {client_id}')
                        break

                    # Декодируем JSON
                    try:
                        message = json.loads(data.decode().strip())
                        await self.handle_message(message, writer, client_id)
                    except json.JSONDecodeError as e:
                        logger.warning(f'Невалидный JSON от {client_id}: {data[:100]}')
                        await self.send_error(writer, None, f"Invalid JSON: {e}")

                except (ConnectionResetError, BrokenPipeError):
                    logger.info(f'Соединение разорвано {client_id}')
                    break
                except Exception as e:
                    logger.error(f'Ошибка обработки сообщения от {client_id}: {e}')

        except Exception as e:
            logger.error(f'Критическая ошибка с клиентом {client_id}: {e}')
        finally:
            # Очистка
            if client_id in self.connections:
                del self.connections[client_id]
            writer.close()
            await writer.wait_closed()
            logger.info(f'Клиент отключен: {client_id}')

    async def send_welcome(self, writer: asyncio.StreamWriter):
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
        """Обработка Stratum сообщений"""
        method = data.get("method")
        msg_id = data.get("id")
        params = data.get("params", [])

        logger.debug(f'TCP от {client_id}: method={method}, id={msg_id}')

        # Определяем BCH адрес майнера
        miner_address = self.miners.get(client_id)

        if method == "mining.subscribe":
            await self.handle_subscribe(msg_id, writer)
        elif method == "mining.authorize":
            # В TCP протоколе адрес передается в параметрах
            if len(params) >= 1:
                username = params[0]
                # Парсим адрес (формат: address.worker или просто address)
                if '.' in username:
                    bch_address, worker_name = username.split('.', 1)
                else:
                    bch_address = username
                    worker_name = "default"

                self.miners[client_id] = bch_address
                miner_address = bch_address

                # Авторизуем
                success = await self.handle_authorize_tcp(msg_id, bch_address, worker_name, writer)
                if success:
                    # Отправляем первое задание
                    await self.send_new_job_tcp(bch_address, writer)
        elif method == "mining.submit":
            if miner_address:
                await self.handle_submit_tcp(msg_id, params, miner_address, writer)
            else:
                await self.send_error(writer, msg_id, "Not authorized")
        else:
            await self.send_error(writer, msg_id, f"Unknown method: {method}")

    async def handle_subscribe(self, msg_id: int, writer: asyncio.StreamWriter):
        """Обработка подписки"""
        response = {
            "id": msg_id,
            "result": [
                [["mining.set_difficulty", "difficulty"], ["mining.notify", "job_id"]],
                "ae6812eb4cd7735a302a8a9dd95cf71f",  # Extra nonce 1
                4  # Extra nonce 2 size
            ],
            "error": None
        }
        await self._send_json(writer, response)

    async def handle_authorize_tcp(self, msg_id: int, bch_address: str, worker_name: str,
                                   writer: asyncio.StreamWriter) -> bool:
        """Авторизация для TCP"""
        try:
            # Проверяем/регистрируем майнера в БД
            from app.models.database import AsyncSessionLocal
            from app.models.miner import Miner
            from sqlalchemy import select

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Miner).where(Miner.bch_address == bch_address)
                )
                miner = result.scalar_one_or_none()

                if not miner:
                    # Авторегистрация
                    miner = Miner(
                        bch_address=bch_address,
                        worker_name=worker_name[:64],
                        is_active=True
                    )
                    session.add(miner)
                    await session.commit()
                    logger.info(f'Майнер авторегистрация (TCP): {bch_address}')

                response = {
                    "id": msg_id,
                    "result": True,
                    "error": None
                }
                await self._send_json(writer, response)
                return True

        except Exception as e:
            logger.error(f'Ошибка авторизации TCP: {e}')
            await self.send_error(writer, msg_id, f"Authorization error: {e}")
            return False

    async def handle_submit_tcp(self, msg_id: int, params: list, miner_address: str,
                                writer: asyncio.StreamWriter):
        """Обработка шара от TCP клиента"""
        if len(params) < 5:
            await self.send_error(writer, msg_id, "Invalid submit parameters")
            return

        worker_name = params[0]
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(f'Шар TCP от {miner_address}: job={job_id}')

        # Используем общий валидатор
        is_valid, error_msg = self.validator.validate_share(
            job_id=job_id,
            extra_nonce2=extra_nonce2,
            ntime=ntime,
            nonce=nonce,
            miner_address=miner_address
        )

        if not is_valid:
            logger.warning(f'Невалидный шар TCP: {error_msg}')
            await self.send_error(writer, msg_id, f"Invalid share: {error_msg}")
            return

        # Сохраняем в БД (используем метод из ws_server)
        try:
            await self.ws_server.save_share_to_db(
                miner_address=miner_address,
                job_id=job_id,
                extra_nonce2=extra_nonce2,
                ntime=ntime,
                nonce=nonce,
                difficulty=1.0,
                is_valid=True
            )

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await self._send_json(writer, response)
            logger.info(f'Валидный шар принят (TCP): {miner_address}')

        except Exception as e:
            logger.error(f'Ошибка сохранения шара TCP: {e}')
            await self.send_error(writer, msg_id, f"Database error: {e}")

    # ДОБАВЛЯЕМ В КЛАСС StratumTCPServer:

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        """Отправка задания TCP клиенту"""
        try:
            from app.dependencies import job_manager

            # Получаем задание от JobManager
            job_data = await job_manager.create_new_job(miner_address)

            if not job_data:
                logger.warning(f"Не удалось создать задание для {miner_address}, используем fallback")
                job_data = await self._create_fallback_job(miner_address)

            # Отправляем задание
            await self._send_json(writer, job_data)
            logger.info(f"Задание отправлено TCP клиенту: {miner_address}")

        except Exception as e:
            logger.error(f"Ошибка отправки задания TCP: {e}")

    async def _create_fallback_job(self, miner_address: str) -> dict:
        """Создать fallback задание"""
        import time
        from datetime import datetime, UTC

        job_id = f"job_{int(time.time())}_tcp_{miner_address[:8]}"

        job_data = {
            "method": "mining.notify",
            "params": [
                job_id,
                "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9",
                "fdfd0800",
                "",
                [],
                "20000000",
                "1d00ffff",
                format(int(datetime.now(UTC).timestamp()), '08x'),
                True
            ]
        }

        # Сохраняем в валидаторе
        self.validator.add_job(job_id, job_data)
        return job_data

    async def send_error(self, writer: asyncio.StreamWriter, msg_id: int, error_msg: str):
        """Отправка ошибки"""
        response = {
            "id": msg_id,
            "result": None,
            "error": [20, error_msg, None]
        }
        await self._send_json(writer, response)

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict):
        """Отправка JSON с новой строкой"""
        try:
            writer.write((json.dumps(data) + "\n").encode())
            await writer.drain()
        except Exception as e:
            logger.error(f'Ошибка отправки TCP: {e}')

    async def stop(self):
        """Остановка сервера"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info('TCP Stratum сервер остановлен')


# Глобальный экземпляр
tcp_stratum_server = StratumTCPServer()