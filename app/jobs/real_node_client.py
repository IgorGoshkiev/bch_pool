import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any, Union
from pathlib import Path

logger = logging.getLogger(__name__)


class RealBCHNodeClient:
    """Реальный клиент для подключения к BCH ноде"""

    def __init__(self, rpc_host: str = "127.0.0.1", rpc_port: int = 28332,
                 rpc_user: Optional[str] = None, rpc_password: Optional[str] = None,
                 use_cookie: bool = True):
        self.rpc_host = rpc_host
        self.rpc_port = rpc_port
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password
        self.use_cookie = use_cookie
        self.rpc_url = f"http://{rpc_host}:{rpc_port}"
        self.session: Optional[aiohttp.ClientSession] = None
        self.request_id = 0
        self.block_height = 0
        self.difficulty = 0.0
        self.blockchain_info: Optional[Dict] = None

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
                if response.status == 200:
                    result_data = await response.json()
                    if "error" in result_data and result_data["error"]:
                        error_msg = result_data["error"]
                        logger.error(f"RPC ошибка при вызове {method}: {error_msg}")
                        return None
                    return result_data.get("result")
                else:
                    error_text = await response.text()
                    logger.error(f"HTTP ошибка {response.status} при вызове {method}: {error_text}")
                    return None

        except aiohttp.ClientConnectionError as e:
            logger.error(f"Ошибка подключения к {self.rpc_url}: {e}")
            return None
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при подключении к {self.rpc_url}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка при вызове {method}: {e}")
            return None

    async def connect(self) -> bool:
        """Подключение к ноде"""
        try:
            self.session = aiohttp.ClientSession()

            # Тестовый вызов для проверки подключения
            self.blockchain_info = await self.get_blockchain_info()
            if self.blockchain_info:
                self.block_height = self.blockchain_info.get('blocks', 0)
                self.difficulty = self.blockchain_info.get('difficulty', 0.0)
                logger.info(f"Подключено к BCH ноде {self.rpc_url}")
                logger.info(f"Высота: {self.block_height}")
                logger.info(f"Сеть: {self.blockchain_info.get('chain', 'unknown')}")
                logger.info(f"Сложность: {self.difficulty}")
                return True

            logger.error(f"Не удалось подключиться к {self.rpc_url}")
            return False

        except Exception as e:
            logger.error(f"Ошибка подключения к ноде: {e}")
            return False

    async def close(self):
        """Закрытие соединения"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_blockchain_info(self) -> Optional[Dict]:
        """Получение информации о блокчейне"""
        result = await self._make_rpc_call("getblockchaininfo")
        if isinstance(result, dict):
            return result
        return None

    async def get_block_template(self, rules: list = None) -> Optional[Dict]:
        """Получение шаблона блока для майнинга"""
        params = []
        if rules:
            params.append({"rules": rules})
        result = await self._make_rpc_call("getblocktemplate", params)
        if isinstance(result, dict):
            self.block_height = result.get('height', self.block_height)
            return result
        return None

    async def submit_block(self, hex_data: str) -> Optional[Dict]:
        """Отправка найденного блока"""
        result = await self._make_rpc_call("submitblock", [hex_data])
        # Bitcoin RPC возвращает None при успехе, строку при ошибке
        if result is None:
            return {"status": "accepted", "message": "Block accepted"}
        else:
            return {"status": "rejected", "message": str(result)}

    async def get_mining_info(self) -> Optional[Dict]:
        """Получение информации о майнинге"""
        result = await self._make_rpc_call("getmininginfo")
        if isinstance(result, dict):
            return result
        return None

    async def get_network_hashps(self, nblocks: int = 120, height: int = -1) -> Optional[float]:
        """Получение хэшрейта сети"""
        result = await self._make_rpc_call("getnetworkhashps", [nblocks, height])
        if isinstance(result, (int, float)):
            return float(result)
        elif isinstance(result, str):
            try:
                return float(result)
            except ValueError:
                return None
        return None

    async def ping(self) -> bool:
        """Проверка доступности ноды"""
        try:
            result = await self._make_rpc_call("getblockcount")
            return isinstance(result, int)
        except Exception:
            return False

    async def validate_address(self, address: str) -> Optional[Dict]:
        """Валидация BCH адреса"""
        result = await self._make_rpc_call("validateaddress", [address])
        if isinstance(result, dict):
            return result
        return None


    async def get_mempool_info(self) -> Optional[Dict]:
        """Информация о mempool"""
        result = await self._make_rpc_call("getmempoolinfo")
        if isinstance(result, dict):
            return result
        return None