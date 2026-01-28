"""
Тесты для NetworkManager
"""
import pytest
from unittest.mock import Mock, patch


class TestNetworkManager:
    """Тесты для NetworkManager"""

    def test_network_detection(self):
        """Тест определения сети"""
        from app.utils.network_config import NetworkManager

        # Тест определения по порту
        with patch('app.utils.network_config.settings') as mock_settings:
            mock_settings.bch_rpc_port = 8332
            manager = NetworkManager()
            assert manager.network == 'mainnet'

            mock_settings.bch_rpc_port = 18332
            manager = NetworkManager()
            assert manager.network == 'testnet'

            mock_settings.bch_rpc_port = 28332
            manager = NetworkManager()
            assert manager.network == 'testnet4'

            mock_settings.bch_rpc_port = 18443
            manager = NetworkManager()
            assert manager.network == 'regtest'

    def test_get_rpc_url(self):
        """Тест получения RPC URL"""
        from app.utils.network_config import NetworkManager

        manager = NetworkManager('testnet4')
        url = manager.get_rpc_url("192.168.1.1")
        assert url == "http://192.168.1.1:28332/"

        url = manager.get_rpc_url()  # По умолчанию localhost
        assert url.startswith("http://127.0.0.1:")

    def test_is_testnet(self):
        """Тест проверки тестовой сети"""
        from app.utils.network_config import NetworkManager

        assert NetworkManager('mainnet').is_testnet() == False
        assert NetworkManager('testnet').is_testnet() == True
        assert NetworkManager('testnet4').is_testnet() == True
        assert NetworkManager('regtest').is_testnet() == True

    def test_get_address_prefix(self):
        """Тест получения префикса адреса"""
        from app.utils.network_config import NetworkManager

        assert NetworkManager('mainnet').get_address_prefix() == 'bitcoincash'
        assert NetworkManager('testnet').get_address_prefix() == 'bchtest'
        assert NetworkManager('testnet4').get_address_prefix() == 'bchtest'
        assert NetworkManager('regtest').get_address_prefix() == 'bchreg'

    def test_get_block_reward(self):
        """Тест получения награды за блок"""
        from app.utils.network_config import NetworkManager

        manager = NetworkManager('testnet4')

        # Награда на высоте 0
        reward = manager.get_block_reward(0)
        assert reward == 6.25

        # Награда на высоте 210000 (после первого halving)
        reward = manager.get_block_reward(210000)
        assert reward == 3.125

        # Награда на высоте 420000 (после второго halving)
        reward = manager.get_block_reward(420000)
        assert reward == 1.5625

    def test_conversion_methods(self):
        """Тест методов конвертации"""
        from app.utils.network_config import NetworkManager

        manager = NetworkManager('testnet4')

        # Конвертация BCH в сатоши
        satoshis = manager.bch_to_satoshis(1.0)
        assert satoshis == 100_000_000

        satoshis = manager.bch_to_satoshis(6.25)
        assert satoshis == 625_000_000

        # Конвертация сатоши в BCH
        bch = manager.satoshis_to_bch(100_000_000)
        assert bch == 1.0

        bch = manager.satoshis_to_bch(625_000_000)
        assert bch == 6.25

    def test_fallback_values(self):
        """Тест fallback значений"""
        from app.utils.network_config import NetworkManager

        manager = NetworkManager('testnet4')

        # Получение fallback значений
        coinbase_value = manager.get_fallback_coinbase_value()
        assert coinbase_value > 0

        prev_hash = manager.get_fallback_prev_block_hash()
        assert len(prev_hash) == 64

        prefix = manager.get_coinbase_prefix()
        assert isinstance(prefix, bytes)

        max_size = manager.get_max_script_sig_size()
        assert max_size > 0

    def test_calculate_block_subsidy(self):
        """Тест расчета субсидии за блок"""
        from app.utils.network_config import NetworkManager

        manager = NetworkManager('testnet4')

        # Субсидия на высоте 0
        subsidy = manager.calculate_block_subsidy(0)
        assert subsidy == 625_000_000  # 6.25 BCH в сатоши

        # Субсидия на высоте 210000 (после halving)
        subsidy = manager.calculate_block_subsidy(210000)
        assert subsidy == 312_500_000  # 3.125 BCH в сатоши


def test_format_satoshis():
    """Тест форматирования сатоши"""
    from app.utils.network_config import NetworkManager

    manager = NetworkManager('testnet4')

    formatted = manager.format_satoshis(100_000_000)
    assert "1.00000000" in formatted
    assert "100,000,000" in formatted

    formatted = manager.format_satoshis(625_000_000)
    assert "6.25000000" in formatted
    assert "625,000,000" in formatted


def test_get_network_manager():
    """Тест фабричной функции"""
    from app.utils.network_config import get_network_manager

    manager = get_network_manager('testnet4')
    assert manager.network == 'testnet4'

    manager = get_network_manager()  # По умолчанию
    assert manager.network is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])