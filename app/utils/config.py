from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional


class Settings(BaseSettings):
    # База данных
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pool_db"
    db_user: str = "pool_admin"
    db_password: str = ""  #  по умолчанию, берется из .env

    # BCH нода
    bch_rpc_host: str = "127.0.0.1"
    bch_rpc_port: int = 28332
    bch_rpc_user: Optional[str] = None
    bch_rpc_password: Optional[str] = None
    bch_rpc_use_cookie: bool = True
    bch_network: Optional[str] = None

    # Настройки пула
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""
    pool_name: str = "BCH Solo Pool"

    # Динамическая сложность
    enable_dynamic_difficulty: bool = True
    difficulty_update_interval: int = 300
    min_difficulty: float = 0.001
    max_difficulty: float = 1000.0
    target_shares_per_minute: int = 60

    # Stratum серверы
    stratum_host: str = "0.0.0.0"
    stratum_port: int = 3333
    stratum_ws_enabled: bool = True
    stratum_tcp_enabled: bool = True

    # Настройки заданий
    job_broadcast_interval: int = 30
    job_cleanup_age: int = 300
    job_max_history_size: int = 100

    # Настройки блоков
    block_version: int = 0x20000000
    block_bits: str = "1d00ffff"
    max_script_sig_size: int = 100
    coinbase_prefix: str = "/BCHPool/"

    # Fallback значения
    fallback_coinbase_value: int = 3125000000
    fallback_prev_block_hash: str = "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9"
    fallback_difficulty: float = 0.001

    # Авторегистрация майнеров
    auto_register_miners: bool = True
    auto_worker_name: str = "default"

    default_share_difficulty: float = 1.0
    default_miner_address: str = "qr5zfhsh0cad3nhtc97d3zr29l9afhnl4shdj6dp34"
    enable_share_validation: bool = True

    # Разработка
    debug: bool = False

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()