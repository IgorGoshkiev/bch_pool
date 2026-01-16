import asyncio
import json
import logging
from typing import Dict, Optional

from app.services.auth_service import AuthService
from app.services.database_service import DatabaseService
from app.services.job_service import JobService


logger = logging.getLogger(__name__)


class StratumTCPServer:
    """TCP Stratum сервер для ASIC майнеров"""

    def __init__(self, host: str = "0.0.0.0", port: int = 3333):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.connections: Dict[str, asyncio.StreamWriter] = {}
        self.miners: Dict[str, str] = {}  # client_id -> bch_address
        self.auth_service = AuthService()
        self.database_service = DatabaseService()
        self.job_service = JobService()

        logger.info(f"TCP Stratum сервер инициализирован: {host}:{port}")

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
            # 1. Отправляем приветствие
            await self._send_welcome(writer)

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
                        await self._send_error(writer, None, f"Invalid JSON: {e}")

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

            # Очищаем задания майнера если он был авторизован
            miner_address = self.miners.get(client_id)
            if miner_address:
                self.job_service.cleanup_miner_jobs(miner_address)
                self.miners.pop(client_id, None)

            writer.close()
            await writer.wait_closed()
            logger.info(f'Клиент отключен: {client_id}')

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

        logger.debug(f'TCP от {client_id}: method={method}, id={msg_id}')

        # Определяем BCH адрес майнера
        miner_address = self.miners.get(client_id)

        if method == "mining.subscribe":
            await self._handle_subscribe(msg_id, writer)
        elif method == "mining.authorize":
            # В TCP протоколе адрес передается в параметрах
            if len(params) >= 1:
                username = params[0]

                # Авторизуем через auth_service
                success, authorized_address, error_msg = await self.auth_service.authorize_miner(username, "")

                if not success:
                    await self._send_error(writer, msg_id, error_msg or "Authorization failed")
                    return

                # Сохраняем адрес майнера
                self.miners[client_id] = authorized_address
                miner_address = authorized_address

                # Отправляем успешный ответ
                response = {
                    "id": msg_id,
                    "result": True,
                    "error": None
                }
                await self._send_json(writer, response)

                # Отправляем первое задание
                await self.send_new_job_tcp(miner_address, writer)
        elif method == "mining.submit":
            if miner_address:
                await self.handle_submit_tcp(msg_id, params, miner_address, writer)
            else:
                await self._send_error(writer, msg_id, "Not authorized")
        else:
            await self._send_error(writer, msg_id, f"Unknown method: {method}")

    @staticmethod
    async def _handle_subscribe(msg_id: int, writer: asyncio.StreamWriter):
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
        await StratumTCPServer._send_json(writer, response)

    async def handle_submit_tcp(self, msg_id: int, params: list, miner_address: str,
                                writer: asyncio.StreamWriter):
        """Обработка шара от TCP клиента"""
        if len(params) < 5:
            await self._send_error(writer, msg_id, "Invalid submit parameters")
            return

        # worker_name = params[0]  # Не используется, но оставляем для совместимости
        job_id = params[1]
        extra_nonce2 = params[2]
        ntime = params[3]
        nonce = params[4]

        logger.info(f'Шар TCP от {miner_address}: job={job_id}')

        # Используем job_service для валидации
        is_valid, error_msg, job_data = self.job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2=extra_nonce2,
            ntime=ntime,
            nonce=nonce,
            miner_address=miner_address
        )

        if not is_valid:
            logger.warning(f'Невалидный шар TCP: {error_msg}')
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
                await self._send_error(writer, msg_id, "Failed to save share to database")
                return

            response = {
                "id": msg_id,
                "result": True,
                "error": None
            }
            await self._send_json(writer, response)
            logger.info(f'Валидный шар принят (TCP): {miner_address}, ID={share_id}')

        except Exception as e:
            logger.error(f'Ошибка сохранения шара TCP: {e}')
            await self._send_error(writer, msg_id, f"Database error: {e}")

    async def send_new_job_tcp(self, miner_address: str, writer: asyncio.StreamWriter):
        """Отправка задания TCP клиенту"""
        try:
            # Получаем задание из job_service
            job_data = self.job_service.get_job_for_miner(miner_address)

            if not job_data:
                logger.warning(f"Не удалось получить задание для {miner_address}")
                await self._send_error(writer, 0, "No job available")  # msg_id = 0 для внутренних ошибок
                return

            # Отправляем задание клиенту
            await self._send_json(writer, job_data)
            logger.info(f"Задание отправлено TCP клиенту: {miner_address}")

        except Exception as e:
            logger.error(f"Ошибка отправки задания TCP: {e}")
            await self._send_error(writer, 0, f"Job error: {str(e)}")

    async def broadcast_new_job(self, job_data: dict):
        """Рассылка нового задания всем TCP клиентам"""
        if not self.connections:
            logger.debug("Нет активных TCP подключений для рассылки")
            return

        successful_sends = 0

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
                    logger.error(f"Ошибка рассылки задания TCP клиенту {client_id}: {e}")

        if successful_sends > 0:
            logger.info(f"Задание разослано {successful_sends} TCP клиентам")
        else:
            logger.warning("Не удалось разослать задание ни одному TCP клиенту")

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
            self.server.close()
            await self.server.wait_closed()
            logger.info('TCP Stratum сервер остановлен')

