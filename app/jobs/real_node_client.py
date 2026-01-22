import aiohttp
import asyncio

from typing import Optional, Dict, Union
from pathlib import Path
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.utils.config import settings

logger = StructuredLogger(__name__)


class RealBCHNodeClient:
    """Реальный клиент для подключения к BCH ноде"""
    #TODO нужно переделать на bch_rpc_port: int = 18332
    def __init__(self,
                 rpc_host: str = settings.bch_rpc_host,
                 rpc_port: int = settings.bch_rpc_port,
                 rpc_user: Optional[str] = settings.bch_rpc_user,
                 rpc_password: Optional[str] = settings.bch_rpc_password,
                 use_cookie: bool = settings.bch_rpc_use_cookie):
        self.rpc_host = rpc_host
        self.rpc_port = rpc_port
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password
        self.use_cookie = use_cookie
        self.rpc_url = f"http://{rpc_host}:{rpc_port}/"
        self.session: Optional[aiohttp.ClientSession] = None
        self.request_id = 0
        self.block_height = 0
        self.difficulty = 0.0
        self.blockchain_info: Optional[Dict] = None
        self.start_time = datetime.now(UTC)
        self.total_requests = 0
        self.failed_requests = 0

        logger.info(
            "Инициализация BCH Node клиента",
            event="bch_node_client_init",
            rpc_host=rpc_host,
            rpc_port=rpc_port,
            use_cookie=use_cookie,
            has_credentials=rpc_user is not None
        )

    async def _get_auth(self) -> Optional[aiohttp.BasicAuth]:
        """Получение объекта аутентификации"""
        # Если указаны явно user/password и не используем cookie
        if not self.use_cookie and self.rpc_user and self.rpc_password:
            logger.debug(f"Использую user/pass аутентификацию: {self.rpc_user}")
            return aiohttp.BasicAuth(self.rpc_user, self.rpc_password)

        # Ищем cookie файл если включено
        if self.use_cookie:
            # Для Windows
            windows_paths = [
                Path.home() / "AppData" / "Roaming" / "Bitcoin" / "testnet4" / ".cookie",
                Path.home() / "AppData" / "Roaming" / "Bitcoin" / ".cookie",
                Path("C:/Users/administrator/AppData/Roaming/Bitcoin/testnet4/.cookie"),
                Path("C:/Users/administrator/AppData/Roaming/Bitcoin/.cookie"),
            ]

            # Для Linux (сервер)
            linux_paths = [
                Path.home() / ".bitcoin" / "testnet4" / ".cookie",
                Path.home() / ".bitcoin" / ".cookie",
                Path("/home/vncuser/.bitcoin/testnet4/.cookie"),
                Path("/home/vncuser/.bitcoin/.cookie"),
            ]

            cookie_paths = windows_paths + linux_paths

            for path in cookie_paths:
                if path.exists():
                    try:
                        with open(path, 'r') as f:
                            cookie = f.read().strip()
                        logger.info(f"Найден cookie файл: {path}")
                        if ':' in cookie:
                            user_pass = cookie.split(':', 1)
                            return aiohttp.BasicAuth(user_pass[0], user_pass[1])
                    except Exception as e:
                        logger.warning(f"Ошибка чтения cookie файла {path}: {e}")

        # Если дошли сюда и есть user/password, используем их
        if self.rpc_user and self.rpc_password:
            logger.debug(f"Использую user/pass (fallback): {self.rpc_user}")
            return aiohttp.BasicAuth(self.rpc_user, self.rpc_password)

        logger.warning("Не найдены данные для аутентификации RPC")
        return None

    async def _make_rpc_call(self, method: str, params: list = None) -> Optional[
        Union[Dict, str, int, float, bool, list]]:
        """Выполнение RPC вызова к ноде"""
        if params is None:
            params = []

        self.request_id += 1
        self.total_requests += 1
        request_start = datetime.now(UTC)

        payload = {
            "jsonrpc": "1.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BCH-Pool/1.0"
        }

        try:
            auth = await self._get_auth()
            timeout = aiohttp.ClientTimeout(total=30)

            async with self.session.post(
                    self.rpc_url,
                    json=payload,
                    headers=headers,
                    auth=auth,
                    timeout=timeout,
                    ssl=False
            ) as response:

                response_time = (datetime.now(UTC) - request_start).total_seconds() * 1000

                if response.status == 200:
                    result_data = await response.json()
                    if "error" in result_data and result_data["error"]:
                        error_msg = result_data["error"]
                        logger.error(
                            "RPC ошибка от ноды",
                            event="bch_node_rpc_error",
                            method=method,
                            error=error_msg,
                            response_time_ms=response_time,
                            request_id=self.request_id
                        )
                        return None
                    return result_data.get("result")
                else:
                    self.failed_requests += 1
                    error_text = await response.text()
                    logger.error(
                        "HTTP ошибка при вызове ноды",
                        event="bch_node_http_error",
                        method=method,
                        status_code=response.status,
                        error=error_text[:200],
                        response_time_ms=response_time,
                        request_id=self.request_id
                    )
                    return None

        except aiohttp.ClientConnectionError as e:
            self.failed_requests += 1
            logger.error(
                "Ошибка подключения к BCH ноде",
                event="bch_node_connection_error",
                method=method,
                error=str(e),
                rpc_url=self.rpc_url,
                request_id=self.request_id
            )
            return None
        except asyncio.TimeoutError:
            self.failed_requests += 1
            logger.error(
                "Таймаут подключения к BCH ноде",
                event="bch_node_timeout",
                method=method,
                rpc_url=self.rpc_url,
                timeout_seconds=30,
                request_id=self.request_id
            )
            return None
        except Exception as e:
            self.failed_requests += 1
            logger.error(
                "Неожиданная ошибка при вызове BCH ноды",
                event="bch_node_unexpected_error",
                method=method,
                error=str(e),
                error_type=type(e).__name__,
                request_id=self.request_id
            )
            return None

    async def connect(self) -> bool:
        """Подключение к ноде"""
        connect_start = datetime.now(UTC)

        try:
            logger.info(
                "Подключение к BCH ноде...",
                event="bch_node_connecting",
                rpc_url=self.rpc_url
            )

            self.session = aiohttp.ClientSession()

            # Тестовый вызов для проверки подключения
            self.blockchain_info = await self.get_blockchain_info()
            if self.blockchain_info:
                self.block_height = self.blockchain_info.get('blocks', 0)
                self.difficulty = self.blockchain_info.get('difficulty', 0.0)

                connect_time = (datetime.now(UTC) - connect_start).total_seconds() * 1000
                logger.info(
                    "Успешно подключено к BCH ноде",
                    event="bch_node_connected",
                    rpc_url=self.rpc_url,
                    block_height=self.block_height,
                    network=self.blockchain_info.get('chain', 'unknown'),
                    difficulty=self.difficulty,
                    connect_time_ms=connect_time
                )

                return True

            logger.error(
                "Не удалось подключиться к BCH ноде",
                event="bch_node_connect_failed",
                rpc_url=self.rpc_url,
                connect_time_ms=(datetime.now(UTC) - connect_start).total_seconds() * 1000
            )

            return False

        except Exception as e:
            logger.error(
                "Ошибка подключения к BCH ноде",
                event="bch_node_connect_error",
                rpc_url=self.rpc_url,
                error=str(e),
                error_type=type(e).__name__,
                connect_time_ms=(datetime.now(UTC) - connect_start).total_seconds() * 1000
            )
            return False

    async def close(self):
        """Закрытие соединения"""
        if self.session and not self.session.closed:
            uptime = (datetime.now(UTC) - self.start_time).total_seconds()
            success_rate = (
                                       self.total_requests - self.failed_requests) / self.total_requests if self.total_requests > 0 else 0

            logger.info(
                "Закрытие соединения с BCH нодой",
                event="bch_node_client_closing",
                total_requests=self.total_requests,
                failed_requests=self.failed_requests,
                success_rate=f"{success_rate:.2%}",
                uptime_seconds=uptime
            )

            await self.session.close()

            logger.info(
                "Соединение с BCH нодой закрыто",
                event="bch_node_client_closed"
            )

    async def get_blockchain_info(self) -> Optional[Dict]:
        """Получение информации о блокчейне"""
        logger.debug(
            "Запрос информации о блокчейне",
            event="bch_node_get_blockchain_info"
        )

        result = await self._make_rpc_call("getblockchaininfo")

        if isinstance(result, dict):
            chain = result.get('chain', 'unknown')
            blocks = result.get('blocks', 0)
            difficulty = result.get('difficulty', 0.0)

            logger.info(
                "Получена информация о блокчейне",
                event="bch_node_blockchain_info_received",
                chain=chain,
                blocks=blocks,
                difficulty=difficulty,
                verification_progress=result.get('verificationprogress', 0),
                size_on_disk=result.get('size_on_disk', 0)
            )
            return result

        logger.error(
            "Не удалось получить информацию о блокчейне",
            event="bch_node_blockchain_info_failed"
        )
        return None

    async def get_block_template(self, rules: list = None) -> Optional[Dict]:
        """Получение шаблона блока для майнинга"""
        request_start = datetime.now(UTC)

        params = []
        if rules:
            params.append({"rules": rules})

        logger.debug(
            "Запрос шаблона блока от ноды",
            event="bch_node_get_block_template",
            rules=rules
        )

        result = await self._make_rpc_call("getblocktemplate", params)

        if isinstance(result, dict):
            self.block_height = result.get('height', self.block_height)
            response_time = (datetime.now(UTC) - request_start).total_seconds() * 1000

            logger.info(
                "Получен шаблон блока от ноды",
                event="bch_node_block_template_received",
                height=result.get('height'),
                transactions=len(result.get('transactions', [])),
                coinbase_value=result.get('coinbasevalue', 0),
                response_time_ms=response_time
            )

            return result

        logger.warning(
            "Не удалось получить шаблон блока от ноды",
            event="bch_node_block_template_failed",
            response_time_ms=(datetime.now(UTC) - request_start).total_seconds() * 1000
        )
        return None

    async def submit_block(self, hex_data: str) -> Optional[Dict]:
        """Отправка найденного блока"""
        logger.info(
            "Отправка блока в BCH ноду",
            event="bch_node_submit_block",
            hex_data_length=len(hex_data),
            hex_data_prefix=hex_data[:64] + "..."
        )

        result = await self._make_rpc_call("submitblock", [hex_data])

        # Bitcoin RPC возвращает None при успехе, строку при ошибке
        if result is None:
            logger.info(
                "Блок принят BCH нодой",
                event="bch_node_block_accepted"
            )
            return {"status": "accepted", "message": "Block accepted"}
        else:
            logger.error(
                "Блок отклонен BCH нодой",
                event="bch_node_block_rejected",
                rejection_reason=str(result)
            )
            return {"status": "rejected", "message": str(result)}

    async def get_mining_info(self) -> Optional[Dict]:
        """Получение информации о майнинге"""
        logger.debug(
            "Запрос информации о майнинге от ноды",
            event="bch_node_get_mining_info"
        )

        result = await self._make_rpc_call("getmininginfo")

        if isinstance(result, dict):
            logger.debug(
                "Получена информация о майнинге",
                event="bch_node_mining_info_received",
                has_networkhashps='networkhashps' in result,
                has_difficulty='difficulty' in result
            )
            return result

        logger.warning(
            "Не удалось получить информацию о майнинге",
            event="bch_node_mining_info_failed"
        )
        return None

    async def get_network_hashps(self, nblocks: int = 120, height: int = -1) -> Optional[float]:
        """Получение хэшрейта сети"""
        logger.debug(
            "Запрос хэшрейта сети от ноды",
            event="bch_node_get_network_hashps",
            nblocks=nblocks,
            height=height
        )

        result = await self._make_rpc_call("getnetworkhashps", [nblocks, height])

        if isinstance(result, (int, float)):
            logger.info(
                "Получен хэшрейт сети от ноды",
                event="bch_node_network_hashps_received",
                network_hashps=float(result),
                nblocks=nblocks
            )
            return float(result)
        elif isinstance(result, str):
            try:
                hashps = float(result)
                logger.info(
                    "Получен хэшрейт сети от ноды (строковый)",
                    event="bch_node_network_hashps_received_string",
                    network_hashps=hashps
                )
                return hashps
            except ValueError:
                logger.warning(
                    "Не удалось преобразовать хэшрейт сети в число",
                    event="bch_node_network_hashps_conversion_error",
                    result=result
                )
                return None

        logger.warning(
            "Не удалось получить хэшрейт сети",
            event="bch_node_network_hashps_failed"
        )
        return None

    async def ping(self) -> bool:
        """Проверка доступности ноды"""
        ping_start = datetime.now(UTC)

        try:
            result = await self._make_rpc_call("getblockcount")
            success = isinstance(result, int)

            ping_time = (datetime.now(UTC) - ping_start).total_seconds() * 1000

            logger.debug(
                "Проверка доступности BCH ноды",
                event="bch_node_ping",
                success=success,
                ping_time_ms=ping_time,
                block_height=result if success else None
            )
            return success

        except Exception as e:
            ping_time = (datetime.now(UTC) - ping_start).total_seconds() * 1000
            logger.debug(
                "Ошибка проверки доступности ноды",
                event="bch_node_ping_error",
                error=str(e),
                ping_time_ms=ping_time
            )
            return False

    async def validate_address(self, address: str) -> Optional[Dict]:
        """Валидация BCH адреса"""
        logger.debug(
            "Валидация BCH адреса через ноду",
            event="bch_node_validate_address",
            address=address[:20] + "..."  # Логируем только часть
        )

        result = await self._make_rpc_call("validateaddress", [address])

        if isinstance(result, dict):
            is_valid = result.get('isvalid', False)

            logger.info(
                "Проверен BCH адрес через ноду",
                event="bch_node_address_validated",
                address=address[:20] + "...",
                is_valid=is_valid,
                is_mine=result.get('ismine', False),
                is_script=result.get('isscript', False)
            )
            return result

        logger.warning(
            "Не удалось проверить BCH адрес",
            event="bch_node_validate_address_failed",
            address=address[:20] + "..."
        )
        return None


    async def get_mempool_info(self) -> Optional[Dict]:
        """Информация о mempool"""
        logger.debug(
            "Запрос информации о mempool",
            event="bch_node_get_mempool_info"
        )

        result = await self._make_rpc_call("getmempoolinfo")

        if isinstance(result, dict):
            logger.debug(
                "Получена информация о mempool",
                event="bch_node_mempool_info_received",
                size=result.get('size', 0),
                bytes=result.get('bytes', 0),
                usage=result.get('usage', 0)
            )
            return result

        logger.warning(
            "Не удалось получить информацию о mempool",
            event="bch_node_mempool_info_failed"
        )
        return None

    def get_stats(self) -> Dict:
        """Получение статистики клиента"""
        uptime = (datetime.now(UTC) - self.start_time).total_seconds()
        success_rate = (
                                   self.total_requests - self.failed_requests) / self.total_requests if self.total_requests > 0 else 0

        stats = {
            "rpc_url": self.rpc_url,
            "block_height": self.block_height,
            "difficulty": self.difficulty,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "success_rate": f"{success_rate:.2%}",
            "uptime_seconds": uptime,
            "use_cookie": self.use_cookie,
            "connected": self.block_height > 0
        }

        logger.debug(
            "Получение статистики BCH Node клиента",
            event="bch_node_stats",
            stats=stats
        )

        return stats