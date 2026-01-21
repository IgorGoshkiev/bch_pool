"""
Сервис для авторизации и регистрации майнеров
"""
from typing import Optional, Tuple
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.dependencies import database_service
from app.utils.config import settings
from app.utils.protocol_helpers import (
    parse_stratum_username,
    validate_bch_address
)

logger = StructuredLogger("auth")


class AuthService:
    """Сервис для авторизации майнеров"""

    def __init__(self):
        self.database_service = database_service

        logger.info(
            "AuthService инициализирован",
            event="auth_service_initialized",
            has_database_service=self.database_service is not None
        )

    @staticmethod
    def parse_username(username: str) -> Tuple[str, str]:
        """Парсинг username в формате address.worker или просто address"""
        try:
            result = parse_stratum_username(username)
            logger.debug(
                "Парсинг username",
                event="auth_parse_username",
                username=username,
                bch_address=result[0],
                worker_name=result[1]
            )
            return result
        except Exception as e:
            logger.error(
                "Ошибка парсинга username",
                event="auth_parse_username_error",
                username=username,
                error=str(e)
            )
            raise

    async def authorize_miner(self, username: str, password: str = "") -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Авторизация майнера

        Args:
            username: Имя пользователя в формате address.worker или address
            password: Пароль (не используется в текущей реализации, но оставляем для совместимости)

        Returns: (success, bch_address, error_message)
        """

        auth_start = datetime.now(UTC)

        try:
            # Парсим username
            bch_address, worker_name = AuthService.parse_username(username)

            if not bch_address:
                logger.warning(
                    "Пустой BCH адрес",
                    event="auth_empty_address",
                    username=username
                )
                return False, None, "Empty BCH address"

            # Проверяем/регистрируем майнера
            miner = await self.database_service.register_miner(bch_address, worker_name)

            if not miner:
                logger.error(
                    "Не удалось зарегистрировать майнера",
                    event="auth_register_failed",
                    bch_address=bch_address,
                    worker_name=worker_name
                )
                return False, None, "Failed to register miner"

            if not validate_bch_address(bch_address):
                logger.warning(
                    "Невалидный BCH адрес",
                    event="auth_invalid_address",
                    bch_address=bch_address
                )
                return False, None, f"Invalid BCH address format: {bch_address}"

            # Проверяем активность
            if not miner.is_active:
                logger.warning(
                    "Майнер деактивирован",
                    event="auth_miner_deactivated",
                    bch_address=bch_address
                )
                return False, bch_address, f"Miner {bch_address} is deactivated"

            auth_time = (datetime.now(UTC) - auth_start).total_seconds() * 1000

            logger.info(
                "Майнер авторизован",
                event="auth_success",
                bch_address=bch_address,
                worker_name=worker_name,
                miner_id=miner.id,
                auth_time_ms=auth_time
            )
            return True, bch_address, None

        except Exception as e:
            auth_time = (datetime.now(UTC) - auth_start).total_seconds() * 1000
            logger.error(
                "Ошибка авторизации майнера",
                event="auth_error",
                username=username,
                error=str(e),
                error_type=type(e).__name__,
                auth_time_ms=auth_time
            )
            return False, None, f"Authorization error: {str(e)}"


    async def check_miner_registration(self, bch_address: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка регистрации майнера

        Returns: (is_registered, worker_name)
        """
        try:
            miner = await self.database_service.get_miner_by_address(bch_address)

            if miner:
                logger.debug(
                    "Майнер зарегистрирован",
                    event="auth_check_registered",
                    bch_address=bch_address,
                    worker_name=miner.worker_name
                )
                return True, miner.worker_name

            logger.debug(
                "Майнер не зарегистрирован",
                event="auth_check_not_registered",
                bch_address=bch_address
            )
            return False, None


        except Exception as e:
            logger.error(
                "Ошибка проверки регистрации",
                event="auth_check_error",
                bch_address=bch_address,
                error=str(e)
            )

            return False, None

    async def auto_register_if_enabled(self, bch_address: str, worker_name: str = "default") -> bool:
        """Автоматическая регистрация если включена в настройках"""
        if not settings.auto_register_miners:
            logger.debug(
                "Авторегистрация отключена",
                event="auth_auto_register_disabled",
                bch_address=bch_address
            )
            return False

        try:
            logger.info(
                "Авторегистрация майнера",
                event="auth_auto_register_start",
                bch_address=bch_address,
                worker_name=worker_name
            )

            miner = await self.database_service.register_miner(bch_address, worker_name)

            if miner:
                logger.info(
                    "Майнер авторегистрирован",
                    event="auth_auto_register_success",
                    bch_address=bch_address,
                    worker_name=worker_name,
                    miner_id=miner.id
                )
                return True
            else:
                logger.error(
                    "Ошибка авторегистрации",
                    event="auth_auto_register_failed",
                    bch_address=bch_address
                )
                return False

        except Exception as e:
            logger.error(
                "Исключение при авторегистрации",
                event="auth_auto_register_exception",
                bch_address=bch_address,
                error=str(e)
            )
            return False

    @staticmethod
    def validate_bch_address(address: str) -> bool:
        """валидация BCH адреса"""
        is_valid = validate_bch_address(address)

        logger.debug(
            "Валидация BCH адреса",
            event="auth_validate_address",
            address=address[:20] + "..." if len(address) > 20 else address,
            is_valid=is_valid
        )

        return is_valid
