"""
Конфигурация для тестов
"""
import pytest
import sys
import os
import asyncio
from datetime import datetime, UTC
from unittest.mock import Mock

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def mock_settings():
    """Mock настроек для тестов"""
    import app.utils.config as config_module

    # Сохраняем оригинальный settings
    original_settings = config_module.settings

    # Создаем mock настроек
    mock_settings = Mock()

    # Базовые настройки
    mock_settings.db_host = "localhost"
    mock_settings.db_port = 5433
    mock_settings.db_name = "test_db"
    mock_settings.db_user = "test_user"
    mock_settings.db_password = "test_password"
    mock_settings.bch_rpc_host = "127.0.0.1"
    mock_settings.bch_rpc_port = 28332
    mock_settings.bch_rpc_user = None
    mock_settings.bch_rpc_password = None
    mock_settings.bch_rpc_use_cookie = True
    mock_settings.fallback_coinbase_value = 3125000000
    mock_settings.fallback_prev_block_hash = "0" * 64
    mock_settings.fallback_difficulty = 0.001
    mock_settings.coinbase_prefix = "/TestPool/"
    mock_settings.max_script_sig_size = 100
    mock_settings.block_bits = "1d00ffff"
    mock_settings.block_version = 0x20000000

    # Настройки для DifficultyService - ДОЛЖНЫ СООТВЕТСТВОВАТЬ config.py!
    mock_settings.target_shares_per_minute = 60.0
    mock_settings.min_difficulty = 0.001
    mock_settings.max_difficulty = 1000.0
    mock_settings.enable_dynamic_difficulty = True

    # Заменяем settings на mock
    config_module.settings = mock_settings

    yield mock_settings

    # Восстанавливаем оригинальные settings
    config_module.settings = original_settings


@pytest.fixture(scope="session")
def event_loop():
    """Создание event loop для асинхронных тестов"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def current_timestamp():
    """Текущий timestamp для тестов"""
    return int(datetime.now(UTC).timestamp())


@pytest.fixture
def sample_bch_addresses():
    """Примеры валидных BCH адресов для тестов"""
    return {
        "testnet": [
            "bchtest:qq9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q",
            "qq9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q",
            "qp9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q"
        ],
        "mainnet": [
            "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a",
            "qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a",
            "ppm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        ]
    }


@pytest.fixture
def sample_stratum_job():
    """Пример задания Stratum для тестов"""
    return {
        "method": "mining.notify",
        "params": [
            "job_1234567890_00000001_broadcast",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff",
            "ffffffff0100f2052a010000001976a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac00000000",
            [],
            "20000000",
            "1d00ffff",
            "5a0b7226",
            True
        ],
        "extra_nonce1": "ae6812eb4cd7735a302a8a9dd95cf71f"
    }


@pytest.fixture
def mock_network_manager():
    """Мок NetworkManager для тестов"""
    manager = Mock()
    manager.config = {
        'default_difficulty': 1.0,
        'network': 'testnet',
        'fallback_prev_block_hash': "0" * 64,
        'default_block_version': 0x20000000,
        'default_bits': "1d00ffff",
        'fallback_coinbase_value': 625000000
    }

    # Методы для JobService
    manager.get_fallback_prev_block_hash = Mock(return_value="0" * 64)
    manager.get_default_block_version = Mock(return_value=0x20000000)
    manager.get_default_bits = Mock(return_value="1d00ffff")
    manager.get_fallback_coinbase_value = Mock(return_value=625000000)

    return manager


@pytest.fixture
def mock_validator():
    """Мок валидатора для тестов JobService"""
    validator = Mock()
    validator.add_job = Mock()
    validator.remove_job = Mock()
    validator.validate_share = Mock(return_value=(True, None))
    return validator