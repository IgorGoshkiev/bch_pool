from pydantic_settings import BaseSettings
from pydantic import ConfigDict
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
    bch_rpc_port: int = 28332  # testnet4 по умолчанию
    bch_rpc_user: Optional[str] = None  # Используем cookie
    bch_rpc_password: Optional[str] = None
    bch_rpc_use_cookie: bool = True

    # Настройка сети (автоматически определяется по порту)
    bch_network: Optional[str] = None  # mainnet, testnet, testnet4, regtest

    # Настройки пула
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""
    pool_name: str = "BCH Solo Pool"

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
    job_max_history_size: int = 100

    # Настройки блоков
    block_version: int = 0x20000000  # Версия блока по умолчанию
    block_bits: str = "1d00ffff"  # Сложность по умолчанию
    max_script_sig_size: int = 100  # Максимальный размер ScriptSig
    coinbase_prefix: str = "/BCHPool/"  # Префикс в ScriptSig coinbase

    # Fallback значения
    fallback_coinbase_value: int = 3125000000  # 31.25 BCH для testnet4
    fallback_prev_block_hash: str = "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9"
    fallback_difficulty: float = 0.001  # Сложность для fallback заданий

    # Авторегистрация майнеров
    auto_register_miners: bool = True
    auto_worker_name: str = "default"

    # Разработка
    debug: bool = False

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
