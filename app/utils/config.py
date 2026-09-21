from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional


class Settings(BaseSettings):
    """
    Настройки приложения.

    Все параметры читаются из .env.
    Значения по умолчанию — только fallback.
    """

    # ============================================================
    # БАЗА ДАННЫХ
    # ============================================================
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "pool_db"
    db_user: str = "pool_admin"
    db_password: str = ""

    # ============================================================
    # BCH НОДА
    # ============================================================
    bch_rpc_host: str = "127.0.0.1"
    bch_rpc_port: int = 8332
    bch_rpc_user: Optional[str] = None
    bch_rpc_password: Optional[str] = None
    bch_rpc_use_cookie: bool = True
    bch_network: Optional[str] = None

    # ============================================================
    # НАСТРОЙКИ ПУЛА
    # ============================================================
    pool_fee_percent: float = 1.5
    pool_wallet: str = ""
    pool_name: str = "BCH Solo Pool"

    # ============================================================
    # НАСТРОЙКИ ЗАДАНИЙ
    # ============================================================
    job_broadcast_interval: int = 30      # Интервал рассылки заданий (сек)
    job_cleanup_age: int = 1800           # Время жизни задания (сек)
    job_max_history_size: int = 100       # Максимум заданий в истории

    # ============================================================
    # STRATUM СЕРВЕРЫ
    # ============================================================
    stratum_host: str = "0.0.0.0"
    stratum_port: int = 3333
    stratum_ws_enabled: bool = True
    stratum_tcp_enabled: bool = True

    # ============================================================
    # НАСТРОЙКИ БЛОКОВ
    # ============================================================
    block_version: int = 0x20000000
    block_bits: str = "1d00ffff"
    max_script_sig_size: int = 100
    coinbase_prefix: str = "/BCHPool/"

    # ============================================================
    # FALLBACK ЗНАЧЕНИЯ
    # ============================================================
    fallback_coinbase_value: int = 312500000  # 3.125 BCH в сатоши
    fallback_prev_block_hash: str = "0" * 64
    fallback_difficulty: float = 1.0

    # ============================================================
    # DISPLAY DIFFICULTY — то, что пул ОТПРАВЛЯЕТ ASIC
    # ============================================================
    # Начальная сложность (если ASIC не прислал suggest)
    start_display_difficulty: float = 32768.0

    # Абсолютный минимум для display_difficulty
    min_display_difficulty: float = 1024.0

    # Абсолютный максимум для display_difficulty
    max_display_difficulty: float = 1000000000.0

    # ============================================================
    # VALIDATION DIFFICULTY — то, с чем пул ВАЛИДИРУЕТ шары
    # ============================================================
    default_validation_difficulty: float = 0.0000000001

    # ============================================================
    # ДИНАМИЧЕСКАЯ СЛОЖНОСТЬ
    # ============================================================
    enable_dynamic_difficulty: bool = True
    difficulty_target_time: float = 8.0
    difficulty_adaptation_rate: float = 0.3
    difficulty_min_change: float = 0.1
    difficulty_min_update_interval: float = 60.0
    # Первое обновление сложности — быстрое (через N секунд после первого шара).
    # Нужно, чтобы ASIC быстро получил правильную сложность в начале.
    difficulty_first_update_interval: float = 5.0

    # Устаревший параметр (для совместимости)
    difficulty_update_interval: int = 300

    # ============================================================
    # СТАТИСТИКА
    # ============================================================
    stats_max_age_seconds: int = 1200
    stats_max_shares_in_memory: int = 2000
    stats_cleanup_interval: int = 60
    stats_aggregate_interval: int = 3600

    # ============================================================
    # ПРОЧЕЕ
    # ============================================================
    default_miner_address: str = "qqxsgzrcxvwh3emhrzmgedttm3ju6ks4ec6072chl0"
    enable_share_validation: bool = True
    auto_register_miners: bool = True
    auto_worker_name: str = "default"
    debug: bool = False

    model_config = ConfigDict(
        **{
            "env_file": ".env",
            "env_file_encoding": "utf-8",
            "extra": "ignore"
        }
    )


settings = Settings()