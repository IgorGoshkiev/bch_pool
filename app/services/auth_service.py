"""
Сервис для авторизации и регистрации майнеров
"""
import logging
from typing import Optional, Tuple

from app.utils.protocol_helpers import parse_stratum_username, validate_bch_address
from app.services.database_service import database_service
from app.utils.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """Сервис для авторизации майнеров"""

    @staticmethod
    def parse_username(username: str) -> Tuple[str, str]:
        """Парсинг username в формате address.worker или просто address"""
        return parse_stratum_username(username)

    @staticmethod
    async def authorize_miner(username: str, password: str = "") -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Авторизация майнера

        Args:
            username: Имя пользователя в формате address.worker или address
            password: Пароль (не используется в текущей реализации, но оставляем для совместимости)

        Returns: (success, bch_address, error_message)
        """
        try:
            # Парсим username
            bch_address, worker_name = AuthService.parse_username(username)

            if not bch_address:
                return False, None, "Empty BCH address"

            # Проверяем/регистрируем майнера
            miner = await database_service.register_miner(bch_address, worker_name)

            if not miner:
                return False, None, "Failed to register miner"

            if not AuthService.validate_bch_address(bch_address):
                return False, None, f"Invalid BCH address format: {bch_address}"

            # Проверяем активность
            if not miner.is_active:
                return False, bch_address, f"Miner {bch_address} is deactivated"

            logger.info(f"Майнер авторизован: {bch_address} (worker: {worker_name})")
            return True, bch_address, None

        except Exception as e:
            logger.error(f"Ошибка авторизации майнера: {e}")
            return False, None, f"Authorization error: {str(e)}"

    @staticmethod
    async def check_miner_registration(bch_address: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка регистрации майнера

        Returns: (is_registered, worker_name)
        """
        try:
            miner = await database_service.get_miner_by_address(bch_address)
            if miner:
                return True, miner.worker_name
            return False, None

        except Exception as e:
            logger.error(f"Ошибка проверки регистрации: {e}")
            return False, None

    @staticmethod
    async def auto_register_if_enabled(bch_address: str, worker_name: str = "default") -> bool:
        """Автоматическая регистрация если включена в настройках"""
        if not settings.auto_register_miners:
            return False

        try:
            miner = await database_service.register_miner(bch_address, worker_name)
            return miner is not None
        except Exception as e:
            logger.error(f"Ошибка авторегистрации: {e}")
            return False

    @staticmethod
    def validate_bch_address(address: str) -> bool:
        """валидация BCH адреса"""
        return validate_bch_address(address)
