"""
Конфигурация для тестов
"""
import pytest
import sys
import os
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

    # Заменяем settings на mock
    config_module.settings = mock_settings

    yield mock_settings

    # Восстанавливаем оригинальные settings
    config_module.settings = original_settings