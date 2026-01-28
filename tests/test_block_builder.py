"""
Тесты для BlockBuilder
"""
import pytest
import struct
import hashlib
from datetime import datetime, UTC
from unittest.mock import Mock, patch


class MockNetworkManager:
    """Mock NetworkManager для тестов"""

    def get_fallback_coinbase_value(self):
        return 3125000000

    def get_fallback_prev_block_hash(self):
        return "0" * 64

    def get_coinbase_prefix(self):
        return b"/TestPool/"

    def get_max_script_sig_size(self):
        return 100

    def get_default_bits(self):
        return "1d00ffff"

    def get_default_block_version(self):
        return 0x20000000

    def get_fallback_difficulty(self):
        return 0.001

    def get_default_block_version(self):
        return 0x20000000


@pytest.fixture
def mock_network_manager():
    """Фикстура для mock NetworkManager"""
    return MockNetworkManager()


@pytest.fixture
def block_builder(mock_network_manager):
    """Фикстура для BlockBuilder"""
    from app.stratum.block_builder import BlockBuilder
    return BlockBuilder(network_manager=mock_network_manager)


class TestBlockBuilder:
    """Тесты для BlockBuilder"""

    def test_calculate_merkle_root(self):
        """Тест расчета Merkle root"""
        from app.stratum.block_builder import BlockBuilder

        # Пустой список
        empty_root = BlockBuilder.calculate_merkle_root([])
        assert empty_root == "0" * 64

        # Один элемент
        single_hash = "abcd" * 16  # 64 hex символа
        single_root = BlockBuilder.calculate_merkle_root([single_hash])
        assert len(single_root) == 64

        # Несколько элементов - ВАЛИДНЫЕ hex строки
        hashes = [f"{i:064x}" for i in range(5)]  # 5 валидных hex хэшей
        multi_root = BlockBuilder.calculate_merkle_root(hashes)
        assert len(multi_root) == 64

    def test_encode_varint(self):
        """Тест кодирования varint"""
        from app.stratum.block_builder import BlockBuilder

        test_cases = [
            (1, b'\x01'),
            (100, b'd'),
            (252, b'\xfc'),
            (253, b'\xfd\xfd\x00'),
            (65535, b'\xfd\xff\xff'),
            (65536, b'\xfe\x00\x00\x01\x00'),
            (4294967295, b'\xfe\xff\xff\xff\xff'),
        ]

        for value, expected in test_cases:
            result = BlockBuilder._encode_varint(value)
            assert result == expected, f"Varint {value}: expected {expected.hex()}, got {result.hex()}"

    def test_build_block_header(self, block_builder):
        """Тест сборки заголовка блока"""
        template = {
            "height": 100,
            "previousblockhash": "abcd" * 16,  # 64 hex символа
            "version": 0x20000000,
            "bits": "1d00ffff",
            "curtime": 1234567890
        }

        merkle_root = "1234" * 16
        ntime = "499602d2"  # hex для 1234567890
        nonce = "12345678"

        header, header_hash = block_builder.build_block_header(
            template, merkle_root, ntime, nonce
        )

        assert len(header) == 80
        assert len(header_hash) == 64

        # Проверяем структуру заголовка
        version = struct.unpack('<I', header[0:4])[0]
        assert version == template["version"]

        prev_hash = header[4:36][::-1].hex()
        assert prev_hash == template["previousblockhash"]

        # Проверяем расчет хэша
        calculated_hash = block_builder.calculate_block_hash(header)
        assert calculated_hash == header_hash

    def test_assemble_full_block(self, block_builder):
        """Тест сборки полного блока"""
        template = {
            "height": 100,
            "previousblockhash": "abcd" * 16,
            "version": 0x20000000,
            "bits": "1d00ffff",
            "curtime": 1234567890,
            "transactions": [
                {"data": "0100000001" + "00" * 200},
                {"hex": "0200000001" + "11" * 200}
            ]
        }

        # Создаем тестовый заголовок
        merkle_root = "1234" * 16
        header, _ = block_builder.build_block_header(
            template, merkle_root, "499602d2", "12345678"
        )

        # Тестовая coinbase транзакция
        coinbase_tx = "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff" + \
                      "00" * 50 + "ffffffff0100f2052a010000001976a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac00000000"

        # Собираем блок
        block_hex = block_builder.assemble_full_block(
            template, header, coinbase_tx, []
        )

        assert block_hex
        assert len(block_hex) % 2 == 0
        assert block_hex.startswith(header.hex())

    def test_validate_block_solution(self, block_builder):
        """Тест валидации решения блока"""
        template = {
            "height": 100,
            "previousblockhash": "abcd" * 16,
            "version": 0x20000000,
            "bits": "1d00ffff",
            "curtime": 1234567890
        }

        # Тестовые данные
        merkle_root = "1234" * 16
        ntime = "499602d2"
        nonce = "12345678"

        # Валидируем (должно быть невалидно для сложности 1.0 с таким nonce)
        is_valid, block_hash, error = block_builder.validate_block_solution(
            template, merkle_root, ntime, nonce, 1.0
        )

        assert not is_valid  # Случайный nonce не должен быть валиден
        assert block_hash
        assert not error


def test_integration():
    """Интеграционный тест полного создания блока"""
    # Создаем полный тестовый шаблон
    template = {
        "height": 1000,
        "previousblockhash": "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9",
        "version": 0x20000000,
        "bits": "1d00ffff",
        "curtime": int(datetime.now(UTC).timestamp()),
        "coinbasevalue": 3125000000,
        "transactions": []
    }

    # Тестовые данные
    miner_address = "bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e"
    extra_nonce1 = "ae6812eb4cd7735a302a8a9dd95cf71f"
    extra_nonce2 = "00000001"
    ntime = format(template["curtime"], '08x')
    nonce = "12345678"

    # Mock network manager для теста
    mock_manager = MockNetworkManager()

    # Mock ВСЕГО что связано с bch_address
    with patch('app.utils.bch_address.create_coinbase_script') as mock_create_script, \
            patch('app.utils.bch_address.BCHAddress') as mock_bch_address, \
            patch('app.utils.bch_address.BCHAddressUtils') as mock_utils:
        # Настраиваем моки
        mock_create_script.return_value = "76a914" + "11" * 20 + "88ac"  # валидный script

        # Mock для BCHAddress
        mock_address_instance = Mock()
        mock_address_instance.extract_pubkey_hash.return_value = bytes.fromhex("11" * 20)
        mock_bch_address.extract_pubkey_hash.return_value = bytes.fromhex("11" * 20)

        # Mock для BCHAddressUtils
        mock_utils.extract_pubkey_hash.return_value = bytes.fromhex("11" * 20)
        mock_utils.validate.return_value = (True, "P2PKH")

        # Импортируем и создаем BlockBuilder
        from app.stratum.block_builder import BlockBuilder
        builder = BlockBuilder(network_manager=mock_manager)

        # 1. Создаем полный блок
        result = builder.create_complete_block(
            template, miner_address, extra_nonce1, extra_nonce2, ntime, nonce
        )

        assert result is not None
        assert result['height'] == 1000
        assert len(result['header_hash']) == 64
        assert result['transaction_count'] == 1  # только coinbase

        # 2. Создаем Stratum job data
        job_data = builder.create_stratum_job_data(
            template, "integration_test_job", miner_address, extra_nonce1
        )

        assert job_data is not None
        assert job_data["method"] == "mining.notify"
        assert len(job_data["params"]) == 9
        assert job_data["extra_nonce1"] == extra_nonce1


if __name__ == "__main__":
    # Для запуска тестов без pytest
    print("=" * 60)
    print("Запуск тестов BlockBuilder")
    print("=" * 60)

    # Добавляем текущую директорию в путь
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Mock настроек для тестов
    import app.utils.config as config_module


    class TestSettings:
        def __init__(self):
            self.db_password = "test_password"
            self.fallback_coinbase_value = 3125000000
            self.fallback_prev_block_hash = "0" * 64
            self.fallback_difficulty = 0.001
            self.coinbase_prefix = "/TestPool/"
            self.max_script_sig_size = 100
            self.block_bits = "1d00ffff"
            self.block_version = 0x20000000

        def __getattr__(self, name):
            return None


    original_settings = config_module.settings
    config_module.settings = TestSettings()

    try:
        # Запускаем тесты
        pytest.main([__file__, "-v"])
    finally:
        config_module.settings = original_settings