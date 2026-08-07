"""
Вспомогательные функции для работы с протоколами (Stratum, TCP)
"""
import time
from typing import Tuple

# ========== КОНСТАНТЫ STRATUM ПРОТОКОЛА ==========
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
    # Нормализация адреса - убираем префикс bitcoincash: если есть
    if username.startswith('bitcoincash:'):
        username = username[12:]  # убираем префикс

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
    """Валидация BCH адреса (поддерживает CashAddr без префикса)"""
    if not address or not isinstance(address, str):
        return False

    addr = address.strip()

    # CashAddr с префиксом
    if addr.startswith('bitcoincash:') or addr.startswith('bchtest:'):
        clean = addr.split(':', 1)[1]
    else:
        clean = addr

    # CashAddr без префикса (начинается с q или p)
    if clean[0] in ['q', 'p']:
        # Базовые проверки длины
        if len(clean) < 40 or len(clean) > 45:
            return False

        # Проверка символов base32
        import re
        if not re.match(r'^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]+$', clean):
            return False
        return True

    # Legacy формат
    elif clean[0] in ['1', '3']:
        return True

    return False