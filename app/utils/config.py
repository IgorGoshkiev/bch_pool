from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # База данных
    db_host: str = "localhost"
    db_port: int = 5433  # 5433 из .env!
    db_name: str = "pool_db"
    db_user: str = "pool_admin"
    db_password: str

    # BCH нода
    bch_rpc_host: str = "127.0.0.1"
    bch_rpc_port: int = 28332  #  testnet4 по умолчанию
    bch_rpc_user: Optional[str] = None  # Используем cookie
    bch_rpc_password: Optional[str] = None
    bch_rpc_use_cookie: bool = True

    # Настройка сети (автоматически определяется по порту)
    bch_network: Optional[str] = None  # mainnet, testnet, testnet4, regtest

    # Настройки пула
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""

    # Динамическая сложность
    enable_dynamic_difficulty: bool = True
    difficulty_update_interval: int = 300  # секунды
    min_difficulty: float = 0.001
    max_difficulty: float = 1000.0
    target_shares_per_minute: int = 60  # Цель - 1 шар в секунду

    # Stratum серверы
    stratum_host: str = "0.0.0.0"
    stratum_port: int = 3333
    stratum_ws_enabled: bool = True  # WebSocket Stratum
    stratum_tcp_enabled: bool = True  # TCP Stratum

    # Настройки заданий
    job_broadcast_interval: int = 30  # секунды
    job_cleanup_age: int = 300  # секунды (5 минут)

    # Авторегистрация майнеров
    auto_register_miners: bool = True
    auto_worker_name: str = "default"

    # Разработка
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # ← ВАЖНО: игнорируем лишние поля


settings = Settings()
