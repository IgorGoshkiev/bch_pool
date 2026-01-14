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
    bch_rpc_port: int = 28332  #
    bch_rpc_user: Optional[str] = None  # Используем cookie
    bch_rpc_password: Optional[str] = None
    # bch_use_cookie: bool = True
    bch_rpc_use_cookie: bool = True

    # Настройки пула
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""

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
