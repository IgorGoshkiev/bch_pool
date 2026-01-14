import time
import logging
from typing import Optional, Dict
from datetime import datetime, UTC
import hashlib

from app.utils.config import settings
from app.jobs.real_node_client import RealBCHNodeClient
from app.stratum.block_builder import BlockBuilder

logger = logging.getLogger(__name__)


class JobManager:
    """Менеджер заданий для майнинг пула - только реальная нода"""

    def __init__(self):
        # Используем настройки из config.py
        self.node_client = RealBCHNodeClient(
            rpc_host=settings.bch_rpc_host,
            rpc_port=settings.bch_rpc_port,
            rpc_user=settings.bch_rpc_user,
            rpc_password=settings.bch_rpc_password,
            use_cookie=settings.bch_rpc_use_cookie
        )
        self.current_job = None
        self.job_history = []  # История заданий
        self.job_counter = 0
        self.stratum_server = None  # Будет установлено через set_stratum_server
        self.block_height = 0
        self.difficulty = 0.0

    def set_stratum_server(self, stratum_server):
        """Установить ссылку на Stratum сервер"""
        self.stratum_server = stratum_server

    async def initialize(self) -> bool:
        """Инициализация менеджера с реальной нодой"""
        try:
            logger.info(f"Подключение к BCH ноде: {settings.bch_rpc_host}:{settings.bch_rpc_port}")

            if await self.node_client.connect():
                # Обновляем локальные переменные из клиента
                self.block_height = self.node_client.block_height
                self.difficulty = self.node_client.difficulty

                # Логируем успешное подключение
                logger.info(f"JobManager инициализирован. Высота блокчейна: {self.block_height}")
                logger.info(
                    f"   Цепочка: {self.node_client.blockchain_info.get('chain', 'unknown') if hasattr(self.node_client, 'blockchain_info') else 'unknown'}")
                logger.info(f"   Сложность: {self.difficulty}")
                return True
            else:
                logger.error("Не удалось подключиться к BCH ноде")
                logger.error("Проверьте:")
                logger.error("1. Запущена ли нода на сервере")
                logger.error("2. Настройки RPC в bitcoin.conf")
                logger.error("3. Доступность порта 28332")
                if settings.bch_rpc_use_cookie:
                    logger.error("4. Существование .cookie файла")
                return False

        except Exception as e:
            logger.error(f"Ошибка инициализации JobManager: {e}")
            return False

    async def create_new_job(self, miner_address: str = None) -> Optional[Dict]:
        """Создать новое задание для майнера"""
        try:
            # Получаем шаблон блока от реальной ноды
            template = await self.node_client.get_block_template()
            if not template:
                logger.warning("Не удалось получить шаблон блока от ноды")
                return None

            # Обновляем высоту блока
            if 'height' in template:
                self.block_height = template['height']
                self.node_client.block_height = template['height']

            # Создаем уникальный ID задания
            self.job_counter += 1
            timestamp = int(time.time())

            if miner_address:
                job_id = f"job_{timestamp}_{self.job_counter:08x}_{miner_address[:8]}"
            else:
                job_id = f"job_{timestamp}_{self.job_counter:08x}"

            # Конвертируем в Stratum формат
            stratum_job = self._convert_to_stratum_job(template, job_id)

            # Сохраняем задание
            self.current_job = {
                "id": job_id,
                "template": template,
                "stratum_data": stratum_job,
                "created_at": datetime.now(UTC),
                "miner_address": miner_address
            }

            # Добавляем в историю (ограничиваем размер)
            self.job_history.append(self.current_job)
            if len(self.job_history) > 100:
                self.job_history = self.job_history[-100:]

            logger.info(f"Создано задание {job_id} для майнера {miner_address or 'broadcast'}")
            logger.debug(f"Высота: {template.get('height', 'unknown')}")
            logger.debug(f"Предыдущий хэш: {template.get('previousblockhash', '')[:16]}...")
            logger.debug(f"Время: {template.get('curtime', 'unknown')}")
            logger.debug(f"Coinbase: {template.get('coinbasevalue', 0)} сатоши")

            return stratum_job

        except Exception as e:
            logger.error(f"Ошибка при создании задания: {e}")
            return None

    def _convert_to_stratum_job(self, template: Dict, job_id: str) -> Dict:
        """Конвертировать шаблон блока в Stratum задание"""
        curtime = template.get("curtime", int(time.time()))
        ntime_hex = format(curtime, '08x')

        # Формируем Stratum сообщение mining.notify
        job_data = {
            "method": "mining.notify",
            "params": [
                job_id,  # Job ID
                template.get("previousblockhash", "0" * 64),  # prevhash
                "fdfd0800",  # coinb1 (часть coinbase транзакции)
                "",  # coinb2 (остальная часть coinbase)
                [],  # merkle_branch
                format(template.get("version", 0x20000000), '08x'),  # version
                template.get("bits", "1d00ffff"),  # nbits
                ntime_hex,  # ntime
                True  # clean_jobs
            ]
        }

        return job_data

    async def broadcast_new_job_to_all(self):
        """Рассылать новое задание всем подключенным майнерам"""
        if not self.stratum_server:
            logger.warning("Stratum сервер не установлен в JobManager")
            return

        # Создаем общее задание для всех майнеров
        job_data = await self.create_new_job()
        if not job_data:
            logger.warning("Не удалось создать задание для рассылки")
            return

        # Рассылаем через Stratum сервер
        await self.stratum_server.broadcast_new_job(job_data)

        active_miners = len(set(self.stratum_server.miner_addresses.values()))
        logger.info(f"Задание разослано {active_miners} активным майнерам")

    async def send_job_to_miner(self, miner_address: str) -> bool:
        """Отправить персональное задание конкретному майнеру"""
        if not self.stratum_server:
            return False

        # Находим соединение майнера
        connection_id = None
        for conn_id, addr in self.stratum_server.miner_addresses.items():
            if addr == miner_address:
                connection_id = conn_id
                break

        if not connection_id or connection_id not in self.stratum_server.active_connections:
            logger.warning(f"Майнер {miner_address} не найден среди активных соединений")
            return False

        # Создаем задание для этого майнера
        job_data = await self.create_new_job(miner_address)
        if not job_data:
            logger.warning(f"Не удалось создать задание для майнера {miner_address}")
            return False

        # Отправляем задание
        websocket = self.stratum_server.active_connections[connection_id]
        await websocket.send_json(job_data)

        logger.info(f"Персональное задание отправлено майнеру {miner_address}")
        return True

    async def validate_and_save_share(self, miner_address: str, share_data: Dict) -> Dict:
        """Валидация и сохранение шара"""
        logger.info(f"Шар от майнера {miner_address}: job={share_data.get('job_id')}")

        # Проверяем существование задания
        job_id = share_data.get('job_id')
        if not job_id or job_id not in self.stratum_server.jobs:
            return {
                "status": "rejected",
                "message": f"Job {job_id} not found",
                "difficulty": 0.0,
                "job_id": job_id,
                "timestamp": datetime.now(UTC).isoformat()
            }

        # Получаем данные задания
        job_data = self.stratum_server.jobs[job_id]

        # Проверяем валидность данных
        try:
            extra_nonce2 = share_data.get('extra_nonce2', '')
            ntime = share_data.get('ntime', '')
            nonce = share_data.get('nonce', '')

            # Проверяем формат данных
            if not all([extra_nonce2, ntime, nonce]):
                return {
                    "status": "rejected",
                    "message": "Incomplete share data",
                    "difficulty": 0.0,
                    "job_id": job_id,
                    "timestamp": datetime.now(UTC).isoformat()
                }

            # Проверяем hex формат
            for field, value in [('extra_nonce2', extra_nonce2), ('ntime', ntime), ('nonce', nonce)]:
                try:
                    int(value, 16)
                except ValueError:
                    return {
                        "status": "rejected",
                        "message": f"Invalid hex format for {field}: {value}",
                        "difficulty": 0.0,
                        "job_id": job_id,
                        "timestamp": datetime.now(UTC).isoformat()
                    }

            # Проверяем ntime (время должно быть в разумных пределах)
            try:
                share_time = int(ntime, 16)
                current_time = int(time.time())
                if abs(share_time - current_time) > 7200:  # ±2 часа
                    logger.warning(f"Подозрительное время шара: {share_time} (текущее: {current_time})")
            except:
                pass

            # TODO: В будущем добавить реальную проверку хэша
            # Сейчас просто принимаем шар

            logger.info(f"Шар принят: майнер={miner_address}, job={job_id}")

            return {
                "status": "accepted",
                "message": "Share accepted",
                "difficulty": 1.0,
                "job_id": job_id,
                "timestamp": datetime.now(UTC).isoformat()
            }

        except Exception as e:
            logger.error(f"Ошибка валидации шара: {e}")
            return {
                "status": "rejected",
                "message": f"Validation error: {str(e)}",
                "difficulty": 0.0,
                "job_id": job_id,
                "timestamp": datetime.now(UTC).isoformat()
            }

    async def _save_block_to_db(self, miner_address: str, height: int):
        """Сохранить блок в базу данных"""
        try:
            from app.models.database import AsyncSessionLocal
            from app.models.block import Block
            from datetime import datetime, UTC

            async with AsyncSessionLocal() as session:
                # Создаем запись о блоке
                block = Block(
                    height=height,
                    hash=f"pending_{datetime.now(UTC).timestamp()}",  # Временный хэш
                    miner_address=miner_address,
                    confirmed=False,
                    found_at=datetime.now(UTC)
                )
                session.add(block)
                await session.commit()

                logger.info(f"Блок сохранен в БД: высота={height}, майнер={miner_address}")
                return True
        except Exception as e:
            logger.error(f"Ошибка сохранения блока в БД: {e}")
            return False

    async def submit_block_solution(self, miner_address: str, block_data: Dict) -> Dict:
        """Обработка найденного блока"""
        logger.info(f"БЛОК НАЙДЕН! Майнер: {miner_address}")

        # Получаем данные из шара
        job_id = block_data.get('job_id')
        extra_nonce2 = block_data.get('extra_nonce2', '')
        ntime = block_data.get('ntime', '')
        nonce = block_data.get('nonce', '')

        if not all([job_id, extra_nonce2, ntime, nonce]):
            logger.error("Неполные данные блока")
            return {
                "status": "rejected",
                "message": "Incomplete block data",
                "miner": miner_address
            }

        # Получаем задание из кэша
        if job_id not in self.stratum_server.jobs:
            logger.error(f"Задание {job_id} не найдено в кэше")
            return {
                "status": "rejected",
                "message": f"Job {job_id} not found",
                "miner": miner_address
            }

        job_data = self.stratum_server.jobs[job_id]

        try:
            # Для реальной ноды нам нужно сгенерировать полный блок в hex
            # Используем getblocktemplate для получения шаблона
            template = await self.node_client.get_block_template()

            if not template:
                logger.error("Не удалось получить шаблон блока для сборки")
                return {
                    "status": "rejected",
                    "message": "Cannot get block template",
                    "miner": miner_address
                }

            # Используем BlockBuilder для сборки
            # 1. Получаем extra_nonce1 из параметров задания
            extra_nonce1 = job_data.get('extra_nonce1', 'ae6812eb4cd7735a302a8a9dd95cf71f')

            # 2. Собираем coinbase транзакцию
            coinbase_hex = BlockBuilder.build_coinbase_transaction(
                template=template,
                miner_address=miner_address,
                extra_nonce1=extra_nonce1,
                extra_nonce2=extra_nonce2
            )

            if not coinbase_hex:
                logger.error("Не удалось собрать coinbase транзакцию")
                return {
                    "status": "rejected",
                    "message": "Coinbase transaction error",
                    "miner": miner_address
                }

            # 3. Вычисляем Merkle root
            # Начинаем с хэша coinbase
            coinbase_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_hex)).digest()).digest()
            tx_hashes = [coinbase_hash.hex()]

            # Добавляем хэши остальных транзакций из шаблона
            if 'transactions' in template:
                for tx in template['transactions']:
                    if 'hash' in tx:
                        tx_hashes.append(tx['hash'])

            merkle_root = BlockBuilder.calculate_merkle_root(tx_hashes)

            # 4. Собираем заголовок блока
            header = BlockBuilder.build_block_header(
                template=template,
                merkle_root=merkle_root,
                ntime=ntime,
                nonce=nonce
            )

            if not header or len(header) != 80:
                logger.error(f"Ошибка сборки заголовка, длина: {len(header) if header else 0}")
                return {
                    "status": "rejected",
                    "message": "Block header error",
                    "miner": miner_address
                }

            # 5. Собираем полный блок
            transactions_list = [tx['data'] for tx in template.get('transactions', []) if 'data' in tx]
            full_block_hex = BlockBuilder.assemble_full_block(
                template=template,
                header=header,
                coinbase_tx=coinbase_hex,
                transactions=transactions_list
            )

            if not full_block_hex:
                logger.error("Не удалось собрать полный блок")
                return {
                    "status": "rejected",
                    "message": "Block assembly error",
                    "miner": miner_address
                }

            # 6. Проверяем хэш блока (дополнительная валидация)
            first_hash = hashlib.sha256(header).digest()
            block_hash = hashlib.sha256(first_hash).digest()
            hash_hex = block_hash[::-1].hex()

            # Проверяем соответствует ли хэш сложности
            # TODO: Реализовать проверку сложности

            logger.info(f"Хэш блока: {hash_hex}")
            logger.info(f"Отправка блока в сеть: высота={template.get('height')}")

            # 7. Отправляем блок в сеть
            result = await self.node_client.submit_block(full_block_hex)

            if result and result.get("status") == "accepted":
                logger.info(f"Блок принят сетью! Награда: {template.get('coinbasevalue', 0) / 1e8:.8f} BCH")

                # Обновляем высоту блока
                self.block_height = template.get('height', self.block_height + 1)
                self.node_client.block_height = self.block_height

                # Сохраняем блок в БД
                await self._save_block_to_db(miner_address, template.get('height', self.block_height))

                return {
                    "status": "accepted",
                    "message": "Block accepted by network",
                    "miner": miner_address,
                    "reward": template.get('coinbasevalue', 0) / 1e8,  # BCH награда
                    "height": self.block_height,
                    "hash": hash_hex
                }
            else:
                error_msg = result.get("message", "Unknown error") if result else "RPC error"
                logger.error(f"Блок отклонен: {error_msg}")
                return {
                    "status": "rejected",
                    "message": f"Block rejected: {error_msg}",
                    "miner": miner_address
                }

        except Exception as e:
            logger.error(f"Ошибка при обработке блока: {e}")
            return {
                "status": "rejected",
                "message": f"Block processing error: {str(e)}",
                "miner": miner_address
            }

    def _generate_test_block(self, job_data: dict, extra_nonce2: str, ntime: str, nonce: str) -> str:
        """Генерация тестового блока (временная реализация)"""
        # TODO: Реализовать реальную сборку блока
        try:
            # Для тестов создаем простой блок
            # В реальной реализации нужно собрать правильный блок с транзакциями

            # Пример простого блока (невалидного, только для тестов)
            version = "20000000"
            prevhash = job_data["params"][1] if len(job_data["params"]) > 1 else "0" * 64
            merkleroot = "0" * 64  # Упрощенный Merkle root
            timestamp = ntime
            bits = job_data["params"][6] if len(job_data["params"]) > 6 else "1d00ffff"

            # Собираем заголовок блока
            header = (
                    bytes.fromhex(version)[::-1] +  # version (little-endian)
                    bytes.fromhex(prevhash)[::-1] +  # previous block hash
                    bytes.fromhex(merkleroot)[::-1] +  # merkle root
                    bytes.fromhex(timestamp)[::-1] +  # timestamp
                    bytes.fromhex(bits)[::-1] +  # bits
                    bytes.fromhex(nonce)[::-1]  # nonce
            )

            # Двойной SHA256
            first_hash = hashlib.sha256(header).digest()
            block_hash = hashlib.sha256(first_hash).digest()

            # Для тестов возвращаем хэш заголовка
            # В реальности нужно вернуть полный блок в hex
            return block_hash.hex()

        except Exception as e:
            logger.error(f"Ошибка генерации тестового блока: {e}")
            return "0000000000000000000000000000000000000000000000000000000000000000"

    def get_stats(self) -> Dict:
        """Получить статистику JobManager"""
        return {
            "status": "connected" if self.block_height > 0 else "disconnected",
            "current_job": self.current_job["id"] if self.current_job else None,
            "total_jobs_created": self.job_counter,
            "job_history_size": len(self.job_history),
            "node_info": {
                "block_height": self.block_height,
                "difficulty": self.difficulty,
                "connection": f"{settings.bch_rpc_host}:{settings.bch_rpc_port}",
                "auth_method": "cookie" if settings.bch_rpc_use_cookie else "user/pass"
            }
        }

    def _calculate_hashrate(self, total_shares: int, time_period: int = 3600) -> float:
        """Расчет хэшрейта на основе шаров"""
        if time_period <= 0:
            return 0.0
        return total_shares * self.difficulty / time_period

    async def get_current_difficulty(self) -> float:
        """Получить текущую сложность сети"""
        try:
            mining_info = await self.node_client.get_mining_info()
            if mining_info and 'difficulty' in mining_info:
                return float(mining_info['difficulty'])
            return self.difficulty
        except Exception as e:
            logger.error(f"Ошибка получения сложности: {e}")
            return self.difficulty