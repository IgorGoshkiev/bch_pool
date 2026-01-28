"""
Утилиты для работы с BCH адресами
Использует CashAddr класс для полной поддержки BCH адресов
"""
from typing import Optional, Tuple
from app.utils.logging_config import StructuredLogger
from app.utils.cashaddr import CashAddr, BCHAddressUtils

logger = StructuredLogger(__name__)


class BCHAddress:
    """Класс для работы с BCH адресами (обертка над CashAddr)"""

    @staticmethod
    def validate(address: str, network: str = None) -> Tuple[bool, Optional[str]]:
        """
        Проверка валидности BCH адреса

        Args:
            address: Адрес для проверки
            network: Ожидаемая сеть (mainnet, testnet, testnet4, regtest)

        Returns:
            Tuple[bool, Optional[str]]: (валиден ли адрес, сообщение об ошибке или тип адреса)
        """
        try:
            is_valid, info = BCHAddressUtils.validate(address, network)

            if is_valid:
                logger.debug(
                    "BCH адрес валиден",
                    event="bch_address_validation_success",
                    address=address[:20] + "..." if len(address) > 20 else address,
                    info=info,
                    network=network or "any"
                )
                return is_valid, info

            else:
                logger.warning(
                    "BCH адрес невалиден",
                    event="bch_address_validation_failed",
                    address=address[:20] + "..." if len(address) > 20 else address,
                    info=info,
                    network=network or "any"
                )
                return is_valid, info

        except ValueError as e:
            # Ошибки валидации
            logger.debug(
                "Ошибка валидации BCH адреса",
                event="bch_address_validation_error",
                address=address[:20] + "..." if len(address) > 20 else address,
                error=str(e)
            )
            return False, f"Validation error: {str(e)}"

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при валидации BCH адреса",
                event="bch_address_validation_unexpected_error",
                address=address[:20] + "..." if len(address) > 20 else address,
                error=str(e),
                error_type=type(e).__name__
            )
            return False, f"Unexpected error: {str(e)}"

    @staticmethod
    def to_legacy_format(cash_addr: str) -> Optional[str]:
        """Конвертирование CashAddr в legacy формат"""
        try:
            legacy = CashAddr.to_legacy_format(cash_addr)

            logger.debug(
                "Конвертация CashAddr -> Legacy",
                event="bch_address_cashaddr_to_legacy",
                cashaddr=cash_addr[:30] + "..." if len(cash_addr) > 30 else cash_addr,
                legacy=legacy[:20] + "..." if legacy and len(legacy) > 20 else legacy
            )

            return legacy

        except ValueError as e:
            # Ошибки формата или декодирования
            logger.warning(
                "Ошибка конвертации CashAddr -> Legacy",
                event="bch_address_cashaddr_to_legacy_error",
                cashaddr=cash_addr[:30] + "..." if len(cash_addr) > 30 else cash_addr,
                error=str(e)
            )
            return None

        except ImportError as e:
            # Ошибка импорта base58
            logger.error(
                "Ошибка импорта base58 для конвертации",
                event="bch_address_conversion_import_error",
                error=str(e)
            )
            return None

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при конвертации CashAddr -> Legacy",
                event="bch_address_conversion_unexpected_error",
                cashaddr=cash_addr[:30] + "..." if len(cash_addr) > 30 else cash_addr,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def from_legacy_format(legacy_addr: str) -> Optional[str]:
        """Конвертирование legacy формата в CashAddr"""
        try:
            cashaddr = CashAddr.from_legacy_format(legacy_addr)

            logger.debug(
                "Конвертация Legacy -> CashAddr",
                event="bch_address_legacy_to_cashaddr",
                legacy=legacy_addr[:20] + "..." if len(legacy_addr) > 20 else legacy_addr,
                cashaddr=cashaddr[:30] + "..." if cashaddr and len(cashaddr) > 30 else cashaddr
            )

            return cashaddr

        except ValueError as e:
            # Ошибки формата или декодирования
            logger.warning(
                "Ошибка конвертации Legacy -> CashAddr",
                event="bch_address_legacy_to_cashaddr_error",
                legacy=legacy_addr[:20] + "..." if len(legacy_addr) > 20 else legacy_addr,
                error=str(e)
            )
            return None

        except ImportError as e:
            # Ошибка импорта base58
            logger.error(
                "Ошибка импорта base58 для конвертации",
                event="bch_address_conversion_import_error",
                error=str(e)
            )
            return None

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при конвертации Legacy -> CashAddr",
                event="bch_address_conversion_unexpected_error",
                legacy=legacy_addr[:20] + "..." if len(legacy_addr) > 20 else legacy_addr,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def extract_pubkey_hash(address: str) -> Optional[bytes]:
        """Извлечение pubkey hash из адреса"""
        try:
            hash_bytes = BCHAddressUtils.extract_pubkey_hash(address)

            if hash_bytes:
                logger.debug(
                    "Извлечен pubkey hash из адреса",
                    event="bch_address_extract_pubkey_hash",
                    address=address[:30] + "..." if len(address) > 30 else address,
                    hash_hex=hash_bytes.hex()[:16] + "..."
                )
            else:
                logger.debug(
                    "Не удалось извлечь pubkey hash (не P2KH адрес)",
                    event="bch_address_extract_pubkey_hash_failed",
                    address=address[:30] + "..." if len(address) > 30 else address
                )

            return hash_bytes

        except ValueError as e:
            # Ошибки формата
            logger.warning(
                "Ошибка формата при извлечении pubkey hash",
                event="bch_address_extract_pubkey_hash_format_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e)
            )
            return None

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при извлечении pubkey hash",
                event="bch_address_extract_pubkey_hash_unexpected_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def normalize(address: str, target_format: str = 'cashaddr') -> Optional[str]:
        """Нормализация адреса в указанный формат"""
        try:
            normalized = BCHAddressUtils.normalize(address, target_format)

            if normalized:
                logger.debug(
                    "Адрес нормализован",
                    event="bch_address_normalized",
                    original=address[:30] + "..." if len(address) > 30 else address,
                    normalized=normalized[:30] + "..." if len(normalized) > 30 else normalized,
                    target_format=target_format
                )
            else:
                logger.warning(
                    "Не удалось нормализовать адрес",
                    event="bch_address_normalization_failed",
                    address=address[:30] + "..." if len(address) > 30 else address,
                    target_format=target_format
                )

            return normalized

        except ValueError as e:
            # Ошибки валидации или формата
            logger.warning(
                "Ошибка формата при нормализации адреса",
                event="bch_address_normalization_format_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e)
            )
            return None

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при нормализации адреса",
                event="bch_address_normalization_unexpected_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def detect_network(address: str) -> Optional[str]:
        """Определение сети по адресу"""
        try:
            network = BCHAddressUtils.detect_network(address)

            if network:
                logger.debug(
                    "Определена сеть адреса",
                    event="bch_address_network_detected",
                    address=address[:30] + "..." if len(address) > 30 else address,
                    network=network
                )
            else:
                logger.debug(
                    "Не удалось определить сеть адреса",
                    event="bch_address_network_unknown",
                    address=address[:30] + "..." if len(address) > 30 else address
                )

            return network

        except ValueError as e:
            # Ошибки формата
            logger.debug(
                "Ошибка формата при определении сети",
                event="bch_address_network_detection_format_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e)
            )
            return None

        except Exception as e:
            # Неожиданные ошибки
            logger.error(
                "Неожиданная ошибка при определении сети адреса",
                event="bch_address_network_detection_unexpected_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    def is_valid_for_network(address: str, network: str) -> bool:
        """Проверка что адрес соответствует указанной сети"""
        try:
            is_valid, _ = BCHAddress.validate(address, network)

            logger.debug(
                "Проверка адреса на соответствие сети",
                event="bch_address_network_check",
                address=address[:30] + "..." if len(address) > 30 else address,
                network=network,
                is_valid=is_valid
            )

            return is_valid

        except Exception as e:
            logger.error(
                "Ошибка проверки сети адреса",
                event="bch_address_network_check_error",
                address=address[:30] + "..." if len(address) > 30 else address,
                network=network,
                error=str(e)
            )
            return False


def create_p2pkh_script(pubkey_hash: bytes) -> str:
    """Создание P2PKH ScriptPubKey (76a914{pubkey_hash}88ac)"""
    if not pubkey_hash or len(pubkey_hash) != 20:
        raise ValueError("Invalid pubkey hash length")

    return f"76a914{pubkey_hash.hex()}88ac"


def create_p2sh_script(script_hash: bytes) -> str:
    """Создание P2SH ScriptPubKey (a914{script_hash}87)"""
    if not script_hash or len(script_hash) != 20:
        raise ValueError("Invalid script hash length")

    return f"a914{script_hash.hex()}87"


def create_coinbase_script(miner_address: str) -> Optional[str]:
    """
    Создание ScriptPubKey для coinbase транзакции

    Args:
        miner_address: BCH адрес майнера

    Returns:
        ScriptPubKey в hex или None при ошибке
    """
    try:
        # Извлекаем pubkey hash из адреса
        pubkey_hash = BCHAddress.extract_pubkey_hash(miner_address)

        if not pubkey_hash:
            logger.error(
                "Не удалось извлечь pubkey hash для создания coinbase script",
                event="coinbase_script_extract_hash_error",
                miner_address=miner_address[:30] + "..." if len(miner_address) > 30 else miner_address
            )
            return None

        # Создаем P2PKH ScriptPubKey
        script = create_p2pkh_script(pubkey_hash)

        logger.debug(
            "Создан ScriptPubKey для coinbase",
            event="coinbase_script_created",
            miner_address=miner_address[:30] + "..." if len(miner_address) > 30 else miner_address,
            script=script[:50] + "..." if len(script) > 50 else script
        )

        return script

    except ValueError as e:
        logger.error(
            "Ошибка валидации при создании coinbase script",
            event="coinbase_script_validation_error",
            miner_address=miner_address[:30] + "..." if len(miner_address) > 30 else miner_address,
            error=str(e)
        )
        return None

    except Exception as e:
        logger.error(
            "Неожиданная ошибка при создании coinbase script",
            event="coinbase_script_unexpected_error",
            miner_address=miner_address[:30] + "..." if len(miner_address) > 30 else miner_address,
            error=str(e),
            error_type=type(e).__name__
        )
        return None


def detect_address_type(address: str) -> Optional[str]:
    """
    Определение типа адреса (P2PKH, P2SH)

    Returns:
        'P2PKH', 'P2SH' или None
    """
    try:
        if ':' in address.lower():
            # CashAddr формат
            _, addr_type, _ = CashAddr.decode_address(address.lower())
            return addr_type
        else:
            # Legacy формат - НЕ используем lower!
            import base58
            decoded = base58.b58decode_check(address)  # Убрали .lower()
            version = decoded[0]

            if version in [0x00, 0x6f]:  # P2KH
                return 'P2KH'
            elif version in [0x05, 0xc4]:  # P2SH
                return 'P2SH'

        return None

    except ValueError as e:
        logger.debug(
            "Ошибка формата при определении типа адреса",
            event="address_type_detection_format_error",
            address=address[:30] + "..." if len(address) > 30 else address,
            error=str(e)
        )
        return None

    except ImportError as e:
        logger.error(
            "Ошибка импорта base58",
            event="address_type_detection_import_error",
            error=str(e)
        )
        return None

    except Exception as e:
        logger.error(
            "Неожиданная ошибка при определении типа адреса",
            event="address_type_detection_unexpected_error",
            address=address[:30] + "..." if len(address) > 30 else address,
            error=str(e),
            error_type=type(e).__name__
        )
        return None