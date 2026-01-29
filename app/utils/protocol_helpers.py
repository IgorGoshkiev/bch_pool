"""
Вспомогательные функции для работы с протоколами (Stratum, TCP)
"""
import time
from typing import Tuple

from app.utils.bch_address import BCHAddress

# ========== КОНСТАНТЫ STRATUM ПРОТОКОЛА ==========
STRATUM_EXTRA_NONCE1 = "ae6812eb4cd7735a302a8a9dd95cf71f"
EXTRA_NONCE2_SIZE = 4  # 4 байта = 8 hex символов
BLOCK_HEADER_SIZE = 80  # байт

# ========== КОНСТАНТЫ BCH АДРЕСОВ ==========
BCH_TESTNET_PREFIXES = ['bchtest:', 'qq', 'qp']
BCH_MAINNET_PREFIXES = ['bitcoincash:', 'q', 'p']

# ========== КОНСТАНТЫ ПАГИНАЦИИ ==========
DEFAULT_PAGINATION_LIMIT = 100
MAX_PAGINATION_LIMIT = 1000

# ========== КОНСТАНТЫ ИСТОРИИ ЗАДАНИЙ ==========
JOB_MAX_HISTORY_SIZE = 100

# ========== ФУНКЦИИ ==========

def create_job_id(timestamp: int = None, counter: int = 0, miner_address: str = None) -> str:
    """Создание уникального ID задания"""
    if timestamp is None:
        timestamp = int(time.time())

    if miner_address:
        # Персональное задание
        clean_address = miner_address

        # Убираем префиксы
        prefixes = ['bitcoincash:', 'bchtest:']
        for prefix in prefixes:
            if clean_address.lower().startswith(prefix):
                clean_address = clean_address[len(prefix):]  # Сохраняем регистр адреса
                break

        # Берем первые 8 символов адреса (без префикса)
        address_suffix = clean_address[:8] if clean_address else "unknown"
        return f"job_{timestamp}_{counter:08x}_{address_suffix}"
    else:
        # Общее задание
        return f"job_{timestamp}_{counter:08x}_broadcast"


def parse_stratum_username(username: str) -> Tuple[str, str]:
    """Парсинг username в формате Stratum (address.worker)"""
    if '.' in username:
        address_str, worker_name = username.split('.', 1)
        return address_str.strip(), worker_name.strip()
    else:
        return username.strip(), "default"


def format_hashrate(hashrate: float) -> str:
    """Форматирование хэшрейта в читаемый вид"""
    if hashrate >= 1_000_000_000_000:  # TH/s
        return f"{hashrate / 1_000_000_000_000:.2f} TH/s"
    elif hashrate >= 1_000_000_000:  # GH/s
        return f"{hashrate / 1_000_000_000:.2f} GH/s"
    elif hashrate >= 1_000_000:  # MH/s
        return f"{hashrate / 1_000_000:.2f} MH/s"
    elif hashrate >= 1_000:  # KH/s
        return f"{hashrate / 1_000:.2f} KH/s"
    else:
        return f"{hashrate:.2f} H/s"


def validate_bch_address(address: str) -> bool:
    """Валидация BCH адреса с использованием сервиса из контейнера"""
    if not address or not isinstance(address, str):
        return False

    try:
        is_valid, _ = BCHAddress.validate(address)
        return is_valid
    except (AttributeError, TypeError):
        # Fallback на базовую проверку если bch_address не работает
        import re
        clean_address = address.lower()

        # Legacy форматы
        if clean_address.startswith('1') or clean_address.startswith('3'):
            return 26 <= len(clean_address) <= 35

        # CashAddr форматы
        valid_prefixes = BCH_TESTNET_PREFIXES + BCH_MAINNET_PREFIXES

        for prefix in valid_prefixes:
            if clean_address.startswith(prefix):
                address_part = clean_address[len(prefix):]

                if len(address_part) < 25 or len(address_part) > 36:
                    return False

                if not re.match(r'^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]+$', address_part):
                    return False

                return True

        return False