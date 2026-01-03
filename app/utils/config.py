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
    bch_use_cookie: bool = True
    bch_rpc_use_cookie: bool = True

    # Настройки пула
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""

    # Сервер
    stratum_host: str = "0.0.0.0"
    stratum_port: int = 3333

    # Разработка
    debug: bool = False  #

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # ← ВАЖНО: игнорируем лишние поля


settings = Settings()
