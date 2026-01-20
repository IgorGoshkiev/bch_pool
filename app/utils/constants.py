"""
Константы для всего приложения
"""
import os
from datetime import timedelta

# ========== БЛОКЧЕЙН КОНСТАНТЫ ==========
BCH_NETWORK = "testnet"  # или "mainnet"
BCH_SYMBOL = "BCH"

# Размеры в байтах
BLOCK_HEADER_SIZE = 80
COINBASE_SCRIPT_SIG_MAX_SIZE = 100
EXTRA_NONCE2_SIZE = 4  # Размер extra_nonce2 в байтах для Stratum

# Целевая сложность (для сложности 1.0)
TARGET_FOR_DIFFICULTY_1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

# Вознаграждение за блок (в сатоши)
BLOCK_REWARD = 3125000000  # 31.25 BCH для тестнета

# ========== STRATUM ПРОТОКОЛ ==========
STRATUM_VERSION = "1.0.0"
STRATUM_EXTRA_NONCE1 = "ae6812eb4cd7735a302a8a9dd95cf71f"

# Коды ошибок Stratum
STRATUM_ERROR_CODES = {
    20: "Other/Unknown error",
    21: "Job not found",
    22: "Duplicate share",
    23: "Low difficulty share",
    24: "Unauthorized worker",
    25: "Not subscribed",
}

# ========== ВРЕМЕННЫЕ ИНТЕРВАЛЫ ==========
TIME_RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# Интервалы очистки (в секундах)
JOB_CLEANUP_INTERVAL = 300  # 5 минут
SHARE_CLEANUP_INTERVAL = 3600  # 1 час
CONNECTION_TIMEOUT = 300  # 5 минут

# ========== БАЗА ДАННЫХ ==========
DEFAULT_PAGINATION_LIMIT = 100
MAX_PAGINATION_LIMIT = 1000

# ========== API КОНСТАНТЫ ==========
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# ========== ФАЙЛОВЫЕ ПУТИ ==========
# Пути к cookie файлам BCH ноды
BCH_COOKIE_PATHS = [
    # Windows
    os.path.expanduser("~/AppData/Roaming/Bitcoin/testnet4/.cookie"),
    os.path.expanduser("~/AppData/Roaming/Bitcoin/.cookie"),
    # Linux
    os.path.expanduser("~/.bitcoin/testnet4/.cookie"),
    os.path.expanduser("~/.bitcoin/.cookie"),
    # Ubuntu сервер
    "/home/vncuser/.bitcoin/testnet4/.cookie",
    "/home/vncuser/.bitcoin/.cookie",
]

# ========== НАСТРОЙКИ ПУЛА ==========
POOL_FEE_PERCENT = 1.5
AUTO_REGISTER_MINERS = True
DEFAULT_WORKER_NAME = "default"
MIN_HASHRATE_UPDATE_INTERVAL = 60  # секунды

# ========== ЛИМИТЫ ==========
MAX_JOBS_IN_HISTORY = 100
MAX_SHARES_PER_MINER = 1000
MAX_CONNECTIONS_PER_IP = 10
MAX_WEBSOCKET_MESSAGE_SIZE = 1024 * 1024  # 1MB

# ========== ФОРМАТЫ ==========
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

# ========== СТАТУСЫ ==========
MINER_STATUS_ACTIVE = "active"
MINER_STATUS_INACTIVE = "inactive"
MINER_STATUS_SUSPENDED = "suspended"

SHARE_STATUS_VALID = "valid"
SHARE_STATUS_INVALID = "invalid"
SHARE_STATUS_STALE = "stale"

BLOCK_STATUS_PENDING = "pending"
BLOCK_STATUS_CONFIRMED = "confirmed"
BLOCK_STATUS_ORPHANED = "orphaned"

# ========== ЕДИНИЦЫ ИЗМЕРЕНИЯ ==========
HASHRATE_UNITS = {
    "H/s": 1,
    "KH/s": 1000,
    "MH/s": 1000000,
    "GH/s": 1000000000,
    "TH/s": 1000000000000,
}

DIFFICULTY_UNIT = 1.0