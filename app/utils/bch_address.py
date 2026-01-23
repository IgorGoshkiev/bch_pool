"""
Утилиты для работы с BCH адресами
"""
import base58

from typing import Optional
from app.utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)

# BCH address formats
BCH_PREFIXES = {
    'mainnet': {
        'p2pkh': 'bitcoincash:q',
        'p2sh': 'bitcoincash:p'
    },
    'testnet': {
        'p2pkh': 'bchtest:q',
        'p2sh': 'bchtest:p'
    }
}


class BCHAddress:
    """Класс для работы с BCH адресами"""

    @staticmethod
    def validate(address: str, network: str = 'testnet') -> bool:
        """Проверка валидности BCH адреса"""
        try:
            if not address:
                return False

            address_lower = address.lower()

            # Проверяем CashAddr формат
            if ':' in address_lower:
                prefix, payload = address_lower.split(':', 1)

                # Проверяем префикс
                if network == 'testnet':
                    valid_prefixes = ['bchtest']
                else:
                    valid_prefixes = ['bitcoincash']

                if prefix not in valid_prefixes:
                    return False

                # Проверяем payload
                if not payload or len(payload) < 25:
                    return False

                # Проверяем тип адреса
                if payload[0] not in ['q', 'p']:
                    return False

                # TODO: Добавить полную проверку checksum для CashAddr
                return True

            # Проверяем legacy формат (base58)
            elif address_lower[0] in ['1', '3', 'm', 'n', '2']:
                try:
                    # Декодируем base58
                    decoded = base58.b58decode_check(address)
                    if not decoded:
                        return False

                    # Проверяем длину
                    if len(decoded) != 21:  # 1 byte version + 20 bytes hash
                        return False

                    return True
                except Exception:
                    return False

            return False

        except Exception as e:
            logger.error(f"Ошибка валидации адреса {address}: {e}")
            return False

    @staticmethod
    def to_legacy_format(cash_addr: str) -> Optional[str]:
        """Конвертирование CashAddr в legacy формат"""
        # TODO: Реализовать конвертацию
        # Это сложная операция, требующая знания сети и типа адреса
        return None

    @staticmethod
    def extract_pubkey_hash(address: str) -> Optional[bytes]:
        """Извлечение pubkey hash из адреса"""
        try:
            if ':' in address:
                # CashAddr формат
                _, payload = address.lower().split(':', 1)
                # TODO: Декодировать CashAddr
                return None
            else:
                # Legacy формат
                decoded = base58.b58decode_check(address)
                if decoded and len(decoded) == 21:
                    return decoded[1:]  # Пропускаем version byte
            return None
        except Exception as e:
            logger.error(f"Ошибка извлечения pubkey hash: {e}")
            return None


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