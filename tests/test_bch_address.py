"""
Тесты для модуля bch_address.py
"""

import hashlib

from app.utils.bch_address import (
    BCHAddress,
    create_p2pkh_script,
    create_p2sh_script,
    create_coinbase_script,
    detect_address_type
)


def create_test_cashaddr(is_testnet: bool = False, is_p2sh: bool = False) -> str:
    """Создание тестового CashAddr адреса"""
    from app.utils.cashaddr import CashAddr

    # Генерируем случайный pubkey hash (20 байт)
    import random
    random_bytes = bytes([random.randint(0, 255) for _ in range(20)])

    # Используем хэш для стабильности в тестах
    hash_bytes = hashlib.sha256(random_bytes).digest()[:20]

    # Определяем префикс и тип
    prefix = 'bchtest' if is_testnet else 'bitcoincash'
    address_type = 'P2SH' if is_p2sh else 'P2KH'

    # Кодируем в CashAddr
    return CashAddr.encode_address(prefix, address_type, hash_bytes)


class TestBCHAddressUpdated:
    """Тесты для обновленного BCHAddress"""

    def test_validate_address(self):
        """Тест валидации адресов"""
        # Создаем реальные тестовые адреса
        test_mainnet_addr = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"  # Известный валидный
        test_testnet_addr = create_test_cashaddr(is_testnet=True, is_p2sh=False)  # Сгенерированный

        test_cases = [
            # (address, expected_valid, network)
            (test_mainnet_addr, True, None),
            (test_testnet_addr, True, None),  # Этот должен быть валидным, так как мы его сгенерировали
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", True, None),
            ("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", True, None),
            ("invalid_address", False, None),
            ("bitcoincash:invalid", False, None),
        ]

        for address, expected_valid, network in test_cases:
            is_valid, message = BCHAddress.validate(address, network)
            assert is_valid == expected_valid, f"Address {address}: expected {expected_valid}, got {is_valid} ({message})"
            status = "✓" if is_valid == expected_valid else "✗"
            print(f"{status} Валидация {address[:20]}...: {is_valid} ({message})")

    def test_conversion_methods(self):
        """Тест методов конвертации"""
        # Testnet P2KH адрес
        testnet_legacy = "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"

        # Legacy -> CashAddr
        cashaddr = BCHAddress.from_legacy_format(testnet_legacy)
        assert cashaddr is not None
        assert cashaddr.startswith('bchtest:')
        print(f"Legacy -> CashAddr: {cashaddr[:30]}...")

        # CashAddr -> Legacy
        legacy = BCHAddress.to_legacy_format(cashaddr)
        assert legacy is not None
        print(f"CashAddr -> Legacy: {legacy[:20]}...")

    def test_extract_pubkey_hash(self):
        """Тест извлечения pubkey hash"""
        # Используем известный P2KH адрес mainnet
        p2kh_address = "1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu"  # Mainnet P2KH

        hash_bytes = BCHAddress.extract_pubkey_hash(p2kh_address)
        assert hash_bytes is not None
        assert len(hash_bytes) == 20
        print(f" Извлечение pubkey hash из P2KH: {hash_bytes.hex()[:16]}...")

        # P2SH адрес (не должен вернуть hash)
        p2sh_address = "3CWFddi6m4ndiGyKqzYvsFYagqDLPVMTzC"  # Mainnet P2SH
        hash_bytes2 = BCHAddress.extract_pubkey_hash(p2sh_address)
        assert hash_bytes2 is None
        print(" P2SH адрес не возвращает pubkey hash")

    def test_normalize_address(self):
        """Тест нормализации адресов"""
        # Используем известный legacy адрес
        test_address = "1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu"  # Mainnet P2KH

        # Legacy -> CashAddr
        cashaddr = BCHAddress.normalize(test_address, 'cashaddr')
        assert cashaddr is not None
        assert ':' in cashaddr
        print(f" Нормализация Legacy -> CashAddr: {cashaddr[:30]}...")

        # CashAddr -> Legacy
        legacy = BCHAddress.normalize(cashaddr, 'legacy')
        assert legacy is not None
        assert ':' not in legacy
        print(f" Нормализация CashAddr -> Legacy: {legacy[:20]}...")

    def test_detect_network(self):
        """Тест определения сети"""
        # Создаем сгенерированные адреса вместо фиктивных
        test_mainnet_cashaddr = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        test_testnet_cashaddr = create_test_cashaddr(is_testnet=True, is_p2sh=False)

        test_cases = [
            (test_mainnet_cashaddr, "mainnet"),
            (test_testnet_cashaddr, "testnet"),
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", "mainnet"),
            ("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", "testnet"),
        ]

        for address, expected_network in test_cases:
            network = BCHAddress.detect_network(address)
            assert network == expected_network, f"Address {address}: expected {expected_network}, got {network}"
            print(f" Определение сети {address[:20]}...: {network}")

    def test_is_valid_for_network(self):
        """Тест проверки адреса на соответствие сети"""
        # Используем сгенерированный testnet адрес
        testnet_addr = create_test_cashaddr(is_testnet=True, is_p2sh=False)

        print(f"Тестовый testnet адрес: {testnet_addr[:30]}...")

        # Правильная сеть
        assert BCHAddress.is_valid_for_network(testnet_addr, 'testnet') is True
        print(" Testnet адрес валиден для testnet")

        # Неправильная сеть
        assert BCHAddress.is_valid_for_network(testnet_addr, 'mainnet') is False
        print(" Testnet адрес невалиден для mainnet")


class TestScriptCreation:
    """Тесты создания скриптов"""

    def test_create_p2pkh_script(self):
        """Тест создания P2PKH скрипта"""
        # Тестовый pubkey hash (20 байт)
        test_hash = bytes.fromhex("7c154ed1dc59609e3d26abb2df2ea3d587cd8c41")

        script = create_p2pkh_script(test_hash)
        expected = "76a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac"

        assert script == expected
        print(f"Создание P2PKH скрипта: {script}")

    def test_create_p2sh_script(self):
        """Тест создания P2SH скрипта"""
        # Тестовый script hash (20 байт)
        test_hash = bytes.fromhex("8f55563b9a19f321c211e9b9f9c6b0d6c8c8c8c8")

        script = create_p2sh_script(test_hash)
        expected = "a9148f55563b9a19f321c211e9b9f9c6b0d6c8c8c8c887"

        assert script == expected
        print(f"Создание P2SH скрипта: {script}")

    def test_create_coinbase_script(self):
        """Тест создания coinbase скрипта"""
        # Используем известный mainnet P2KH адрес
        test_address = "1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu"  # Mainnet P2KH

        script = create_coinbase_script(test_address)

        # Проверяем что скрипт создан и имеет правильный формат
        assert script is not None
        assert script.startswith("76a914")  # P2PKH начало
        assert script.endswith("88ac")  # P2PKH конец

        print(f" Создание coinbase скрипта: {script[:50]}...")

    def test_convert_bits(self):
        """Тест функции convert_bits"""
        from app.utils.cashaddr import CashAddr

        # Тестовые данные
        data_8bit = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09]
        result_5bit = CashAddr.convert_bits(data_8bit, 8, 5, True)
        print(f"8-bit to 5-bit: {data_8bit} -> {result_5bit}")

        # Обратная конвертация
        result_back = CashAddr.convert_bits(result_5bit, 5, 8, False)
        print(f"5-bit to 8-bit: {result_5bit} -> {result_back}")

        assert result_back == data_8bit
        print(" Конвертация битов работает правильно")

    def test_cashaddr_validation(self):
        """Тест валидации конкретных CashAddr адресов"""
        # Известные валидные адресы
        valid_addresses = ["bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a",
                           "bitcoincash:qr95sy3j9xwd2ap32xkykttr4cvcu7as4y0qverfuy",
                           create_test_cashaddr(is_testnet=False, is_p2sh=False),
                           create_test_cashaddr(is_testnet=False, is_p2sh=True),
                           create_test_cashaddr(is_testnet=True, is_p2sh=False),
                           create_test_cashaddr(is_testnet=True, is_p2sh=True)]

        # Добавляем сгенерированные адреса

        for address in valid_addresses:
            is_valid, message = BCHAddress.validate(address)
            assert is_valid, f"Address {address} should be valid: {message}"
            print(f" Валидный адрес: {address[:30]}... ({message})")

        # Невалидные адреса
        invalid_addresses = [
            "bitcoincash:invalid",
            "bchtest:wrong",
            "unknown:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a",
        ]

        for address in invalid_addresses:
            is_valid, message = BCHAddress.validate(address)
            assert not is_valid, f"Address {address} should be invalid"
            print(f" Невалидный адрес: {address[:30]}... ({message})")

    def test_detect_address_type(self):
        """Тест определения типа адреса"""
        # Создаем тестовые адреса
        test_cases = [
            ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "P2KH"),
            (create_test_cashaddr(is_testnet=True, is_p2sh=True), "P2SH"),  # Сгенерированный testnet P2SH
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", "P2KH"),
            ("3CWFddi6m4ndiGyKqzYvsFYagqDLPVMTzC", "P2SH"),
        ]

        for address, expected_type in test_cases:
            addr_type = detect_address_type(address)
            assert addr_type == expected_type, f"Address {address}: expected {expected_type}, got {addr_type}"
            print(f" Определение типа адреса {address[:20]}...: {addr_type}")


if __name__ == "__main__":
    print("=" * 60)
    print("Тесты обновленного модуля bch_address.py")
    print("=" * 60)

    # Запускаем тесты BCHAddress
    print("\nТесты BCHAddress:")
    print("-" * 40)

    address_tester = TestBCHAddressUpdated()
    address_methods = [
        address_tester.test_validate_address,
        address_tester.test_conversion_methods,
        address_tester.test_extract_pubkey_hash,
        address_tester.test_normalize_address,
        address_tester.test_detect_network,
        address_tester.test_is_valid_for_network,
    ]

    for method in address_methods:
        print(f"\n{method.__name__}:")
        try:
            method()
            print("Успешно")
        except Exception as e:
            print(f"Ошибка: {e}")

    # Запускаем тесты Script Creation
    print("\n\nТесты создания скриптов:")
    print("-" * 40)

    script_tester = TestScriptCreation()
    script_methods = [
        script_tester.test_create_p2pkh_script,
        script_tester.test_create_p2sh_script,
        script_tester.test_create_coinbase_script,
        script_tester.test_detect_address_type,
    ]

    for method in script_methods:
        print(f"\n{method.__name__}:")
        try:
            method()
            print("Успешно")
        except Exception as e:
            print(f"Ошибка: {e}")

    print("\n" + "=" * 60)
    print("Все тесты завершены")
    print("=" * 60)
