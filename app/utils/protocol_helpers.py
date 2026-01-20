"""
Вспомогательные функции для работы с протоколами (Stratum, TCP)
"""
import time
from typing import Tuple


def create_job_id(timestamp: int = None, counter: int = 0, miner_address: str = None) -> str:
    """Создание уникального ID задания"""
    if timestamp is None:
        timestamp = int(time.time())

    if miner_address:
        # Персональное задание
        address_suffix = miner_address.replace(":", "")[:8] if miner_address else "unknown"
        return f"job_{timestamp}_{counter:08x}_{address_suffix}"
    else:
        # Общее задание
        return f"job_{timestamp}_{counter:08x}_broadcast"


def parse_stratum_username(username: str) -> Tuple[str, str]:
    """Парсинг username в формате Stratum (address.worker)"""
    if '.' in username:
        bch_address, worker_name = username.split('.', 1)
        return bch_address.strip(), worker_name.strip()
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
    """Простая валидация BCH адреса"""
    if not address:
        return False

    # Тестнет префиксы BCH
    testnet_prefixes = ['bchtest:', 'qq', 'qp']
    mainnet_prefixes = ['bitcoincash:', 'q', 'p']

    valid_prefixes = testnet_prefixes + mainnet_prefixes
    return any(address.startswith(prefix) for prefix in valid_prefixes)