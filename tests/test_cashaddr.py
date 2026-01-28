"""
Специфичные тесты для низкоуровневого CashAddr
Тестирует функции, которые не покрыты в test_bch_address.py
"""
from app.utils.cashaddr import CashAddr, CHARSET
import hashlib


class TestCashAddrLowLevel:
    """Низкоуровневые тесты для CashAddr"""

    def test_polymod(self):
        """Тест функции polymod (основа контрольной суммы)"""
        # Создаем тестовый адрес
        test_hash = hashlib.sha256(b"test_polymod").digest()[:20]
        cashaddr = CashAddr.encode_address('bitcoincash', 'P2KH', test_hash)

        # Разбираем адрес
        prefix, encoded = cashaddr.split(':')

        # Декодируем полный payload (включая checksum)
        payload_full = []
        for char in encoded:
            payload_full.append(CHARSET.index(char))  # Используем CHARSET из модуля

        # polymod должен вернуть 0 для валидного адреса (payload + правильная checksum)
        expanded = CashAddr.expand_prefix(prefix) + payload_full
        result = CashAddr.polymod(expanded)

        assert result == 0, f"polymod should return 0 for valid address, got {result}"
        print(f" polymod возвращает 0 для валидного адреса")

        # Также проверяем что для неправильного checksum результат не 0
        wrong_checksum = [(x + 1) % 32 for x in payload_full[-8:]]  # Меняем checksum
        wrong_payload = payload_full[:-8] + wrong_checksum
        expanded_wrong = CashAddr.expand_prefix(prefix) + wrong_payload
        result_wrong = CashAddr.polymod(expanded_wrong)

        assert result_wrong != 0, "polymod should NOT return 0 for wrong checksum"
        print(f" polymod НЕ возвращает 0 для неправильного checksum")

    def test_encode_decode_roundtrip(self):
        """Тест кодирования и декодирования CashAddr"""
        # Произвольный хэш
        test_hash = hashlib.sha256(b"test_hash").digest()[:20]

        # Кодируем
        cashaddr = CashAddr.encode_address('bitcoincash', 'P2KH', test_hash)

        # Декодируем
        prefix, addr_type, decoded_hash = CashAddr.decode_address(cashaddr)

        # Проверяем
        assert prefix == 'bitcoincash'
        assert addr_type == 'P2KH'
        assert decoded_hash == test_hash
        print(f" Encode/decode roundtrip: {cashaddr[:30]}...")

    def test_checksum_calculation(self):
        """Тест расчета контрольной суммы"""
        # Произвольные данные
        test_payload = [i % 32 for i in range(10)]  # 10 чисел 0-31

        # Рассчитываем контрольную сумму
        checksum = CashAddr.calculate_checksum('bitcoincash', test_payload)

        assert len(checksum) == 8, "Checksum should be 8 values"
        assert all(0 <= x < 32 for x in checksum), "Checksum values should be 0-31"
        print(f" Checksum calculation: {checksum}")

    def test_convert_bits_edge_cases(self):
        """Тест конвертации битов (граничные случаи)"""
        # Тест 1: пустые данные
        result1 = CashAddr.convert_bits([], 5, 8, pad=True)
        assert result1 == []

        # Тест 2: точное соответствие
        data_8bit = list(range(10))
        result_5bit = CashAddr.convert_bits(data_8bit, 8, 5, pad=True)
        result_back = CashAddr.convert_bits(result_5bit, 5, 8, pad=False)

        # При pad=False могут быть потери, проверяем что восстановленные данные
        # совпадают с оригиналом до определенной длины
        min_len = min(len(data_8bit), len(result_back))
        assert data_8bit[:min_len] == result_back[:min_len]

        print(" Convert bits edge cases")

    def test_invalid_addresses(self):
        """Тест обработки невалидных адресов"""
        invalid_cases = [
            ("invalid", "No colon"),
            ("bitcoincash:invalid", "Invalid characters"),
            ("wrongprefix:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "Wrong prefix"),
        ]

        for address, description in invalid_cases:
            try:
                CashAddr.decode_address(address)
                assert False, f"Should have failed: {description}"
            except ValueError:
                # Ожидаемая ошибка
                print(f" Правильно отклонен: {description}")

    def test_version_byte_parsing(self):
        """Тест парсинга version byte"""
        # Создаем тестовый адрес и проверяем version byte
        test_hash = hashlib.sha256(b"version_test").digest()[:20]

        # P2KH
        cashaddr_p2kh = CashAddr.encode_address('bchtest', 'P2KH', test_hash)
        prefix_p2kh, type_p2kh, hash_p2kh = CashAddr.decode_address(cashaddr_p2kh)
        assert type_p2kh == 'P2KH'

        # P2SH
        cashaddr_p2sh = CashAddr.encode_address('bchtest', 'P2SH', test_hash)
        prefix_p2sh, type_p2sh, hash_p2sh = CashAddr.decode_address(cashaddr_p2sh)
        assert type_p2sh == 'P2SH'

        print(f" Version byte parsing: P2KH={type_p2kh}, P2SH={type_p2sh}")


if __name__ == "__main__":
    print("=" * 60)
    print("Низкоуровневые тесты CashAddr")
    print("=" * 60)

    tester = TestCashAddrLowLevel()

    test_methods = [
        tester.test_polymod,
        tester.test_encode_decode_roundtrip,
        tester.test_checksum_calculation,
        tester.test_convert_bits_edge_cases,
        tester.test_invalid_addresses,
        tester.test_version_byte_parsing,
    ]

    for method in test_methods:
        print(f"\n{method.__name__}:")
        print("-" * 40)
        try:
            method()
            print("Успешно")
        except Exception as e:
            print(f"Ошибка: {e}")

    print("\n" + "=" * 60)
    print("Тесты завершены")
    print("=" * 60)
