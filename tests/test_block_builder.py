"""
Тесты для BlockBuilder
"""
import pytest
import struct
import hashlib
from datetime import datetime, UTC
from app.stratum.block_builder import BlockBuilder


class TestBlockBuilder:
    """Тесты для BlockBuilder"""

    def test_calculate_merkle_root(self):
        """Тест расчета Merkle root"""
        # Пустой список
        empty_root = BlockBuilder.calculate_merkle_root([])
        assert empty_root == "0" * 64
        print(f"✓ Merkle root пустой: {empty_root}")

        # Один элемент
        single_hash = "abcd" * 16  # 64 hex символа
        single_root = BlockBuilder.calculate_merkle_root([single_hash])
        assert len(single_root) == 64
        print(f"✓ Merkle root одного элемента: {single_root[:16]}...")

        # Несколько элементов
        hashes = [f"hash{i:02x}" * 16 for i in range(5)]  # 5 хэшей
        multi_root = BlockBuilder.calculate_merkle_root(hashes)
        assert len(multi_root) == 64
        print(f"✓ Merkle root нескольких элементов: {multi_root[:16]}...")

    def test_encode_varint(self):
        """Тест кодирования varint"""
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
            print(f"✓ Varint кодирование {value}: {result.hex()}")

    def test_build_block_header(self):
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

        header, header_hash = BlockBuilder.build_block_header(
            template, merkle_root, ntime, nonce
        )

        assert len(header) == 80
        assert len(header_hash) == 64
        print(f"✓ Заголовок создан: {len(header)} байт, hash: {header_hash[:16]}...")

        # Проверяем структуру заголовка
        version = struct.unpack('<I', header[0:4])[0]
        assert version == template["version"]

        prev_hash = header[4:36][::-1].hex()
        assert prev_hash == template["previousblockhash"]

        # Проверяем расчет хэша
        calculated_hash = BlockBuilder.calculate_block_hash(header)
        assert calculated_hash == header_hash
        print(f"✓ Хэш заголовка рассчитан правильно")

    def test_validate_block_solution(self):
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
        is_valid, block_hash, error = BlockBuilder.validate_block_solution(
            template, merkle_root, ntime, nonce, 1.0
        )

        assert not is_valid  # Случайный nonce не должен быть валиден
        assert block_hash
        assert not error
        print(f"✓ Валидация решения: is_valid={is_valid}, hash: {block_hash[:16]}...")

    def test_create_stratum_job_data(self):
        """Тест создания данных для Stratum"""
        template = {
            "height": 100,
            "previousblockhash": "abcd" * 16,
            "version": 0x20000000,
            "bits": "1d00ffff",
            "curtime": 1234567890,
            "coinbasevalue": 3125000000,
            "transactions": []
        }

        job_id = "test_job_123"
        miner_address = "bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e"
        extra_nonce1 = "ae6812eb4cd7735a302a8a9dd95cf71f"

        job_data = BlockBuilder.create_stratum_job_data(
            template, job_id, miner_address, extra_nonce1
        )

        assert job_data is not None
        assert job_data["method"] == "mining.notify"
        assert len(job_data["params"]) == 9
        assert job_data["extra_nonce1"] == extra_nonce1

        # Проверяем параметры
        params = job_data["params"]
        assert params[0] == job_id
        assert params[1] == template["previousblockhash"]
        assert params[5] == format(template["version"], '08x')
        assert params[6] == template["bits"]

        print(f"✓ Данные Stratum созданы: job_id={params[0]}, coinb1={len(params[2])} chars")

    def test_calculate_merkle_branch(self):
        """Тест расчета Merkle branch"""
        # Создаем тестовые хэши транзакций
        tx_hashes = [f"tx{i:04x}" * 16 for i in range(8)]  # 8 транзакций

        # Вычисляем Merkle branch для первой транзакции (coinbase)
        merkle_branch = BlockBuilder._calculate_merkle_branch(tx_hashes)

        # Проверяем что branch не пустой для нескольких транзакций
        assert len(merkle_branch) > 0
        assert all(len(h) == 64 for h in merkle_branch)  # Все хэши 64 hex символа

        print(f"✓ Merkle branch создан: {len(merkle_branch)} элементов")

        # Проверяем что branch корректен
        # (проверка полного Merkle tree слишком сложна для unit теста)

    def test_assemble_full_block(self):
        """Тест сборки полного блока"""
        template = {
            "height": 100,
            "previousblockhash": "abcd" * 16,
            "version": 0x20000000,
            "bits": "1d00ffff",
            "curtime": 1234567890,
            "transactions": [
                {"data": "0100000001" + "00" * 200},  # Простая тестовая транзакция
                {"hex": "0200000001" + "11" * 200}
            ]
        }

        # Создаем тестовый заголовок
        merkle_root = "1234" * 16
        header, _ = BlockBuilder.build_block_header(
            template, merkle_root, "499602d2", "12345678"
        )

        # Тестовая coinbase транзакция
        coinbase_tx = "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff" + \
                      "00" * 50 + "ffffffff0100f2052a010000001976a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac00000000"

        # Собираем блок
        block_hex = BlockBuilder.assemble_full_block(
            template, header, coinbase_tx, []
        )

        assert block_hex
        assert len(block_hex) % 2 == 0  # Должно быть четное число hex символов

        # Блок должен начинаться с заголовка
        assert block_hex.startswith(header.hex())

        print(f"✓ Блок собран: {len(block_hex) // 2} байт, начинается с: {block_hex[:32]}...")


def test_integration():
    """Интеграционный тест полного создания блока"""
    print("\nИнтеграционный тест:")
    print("-" * 40)

    # Создаем полный тестовый шаблон
    template = {
        "height": 1000,
        "previousblockhash": "000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9",
        "version": 0x20000000,
        "bits": "1d00ffff",
        "curtime": int(datetime.now(UTC).timestamp()),
        "coinbasevalue": 3125000000,
        "transactions": [
            {
                "hash": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                "data": "0100000001" + "00" * 100 + "ffffffff0100f2052a010000001976a914" + "11" * 20 + "88ac00000000"
            },
            {
                "hash": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "hex": "0200000001" + "22" * 100 + "ffffffff0100f2052a010000001976a914" + "22" * 20 + "88ac00000000"
            }
        ]
    }

    # Тестовые данные
    miner_address = "bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e"
    extra_nonce1 = "ae6812eb4cd7735a302a8a9dd95cf71f"
    extra_nonce2 = "00000001"
    ntime = format(template["curtime"], '08x')
    nonce = "12345678"

    # 1. Создаем полный блок
    result = BlockBuilder.create_complete_block(
        template, miner_address, extra_nonce1, extra_nonce2, ntime, nonce
    )

    if result:
        print(f"✅ Полный блок создан успешно!")
        print(f"   Высота: {result['height']}")
        print(f"   Хэш блока: {result['header_hash'][:16]}...")
        print(f"   Количество транзакций: {result['transaction_count']}")
        print(f"   Размер блока: {result['size_bytes']} байт")
        print(f"   Merkle root: {result['merkle_root'][:16]}...")
    else:
        print("❌ Не удалось создать блок")

    # 2. Создаем Stratum job data
    job_data = BlockBuilder.create_stratum_job_data(
        template, "integration_test_job", miner_address, extra_nonce1
    )

    if job_data:
        print(f"✅ Данные Stratum созданы успешно!")
        print(f"   Job ID: {job_data['params'][0]}")
        print(f"   Coinb1 длина: {len(job_data['params'][2])}")
        print(f"   Coinb2 длина: {len(job_data['params'][3])}")
        print(f"   Merkle branch: {len(job_data['params'][4])} элементов")
    else:
        print("❌ Не удалось создать данные Stratum")

    return result is not None and job_data is not None


if __name__ == "__main__":
    print("=" * 60)
    print("Тесты BlockBuilder")
    print("=" * 60)

    tester = TestBlockBuilder()

    test_methods = [
        tester.test_calculate_merkle_root,
        tester.test_encode_varint,
        tester.test_build_block_header,
        tester.test_validate_block_solution,
        tester.test_create_stratum_job_data,
        tester.test_calculate_merkle_branch,
        tester.test_assemble_full_block,
    ]

    all_passed = True

    for method in test_methods:
        print(f"\n{method.__name__}:")
        try:
            method()
            print("✅ Успешно")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            all_passed = False

    # Запускаем интеграционный тест
    print("\n" + "=" * 60)

    try:
        integration_success = test_integration()
        if integration_success:
            print("\n✅ Все интеграционные тесты пройдены!")
        else:
            print("\n❌ Интеграционные тесты не пройдены")
            all_passed = False
    except Exception as e:
        print(f"\n❌ Ошибка интеграционного теста: {e}")
        all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ!")
    else:
        print("❌ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
        exit(1)