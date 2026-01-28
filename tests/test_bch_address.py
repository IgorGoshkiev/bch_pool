"""
Тесты для обновленного модуля bch_address.py
"""
from app.utils.bch_address import (
    BCHAddress,
    create_p2pkh_script,
    create_p2sh_script,
    create_coinbase_script,
    detect_address_type
)


class TestBCHAddressUpdated:
    """Тесты для обновленного BCHAddress"""

    def test_validate_address(self):
        """Тест валидации адресов"""
        test_cases = [
            # (address, expected_valid, network)
            ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", True, None),
            ("bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e", True, None),
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", True, None),
            ("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", True, None),
            ("invalid_address", False, None),
            ("bitcoincash:invalid", False, None),
        ]

        for address, expected_valid, network in test_cases:
            is_valid, message = BCHAddress.validate(address, network)
            assert is_valid == expected_valid
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
        print(f"✓ Legacy -> CashAddr: {cashaddr[:30]}...")

        # CashAddr -> Legacy
        legacy = BCHAddress.to_legacy_format(cashaddr)
        assert legacy is not None
        print(f"✓ CashAddr -> Legacy: {legacy[:20]}...")

    def test_extract_pubkey_hash(self):
        """Тест извлечения pubkey hash"""
        # P2KH адрес (должен вернуть hash)
        p2kh_address = "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"

        hash_bytes = BCHAddress.extract_pubkey_hash(p2kh_address)
        assert hash_bytes is not None
        assert len(hash_bytes) == 20
        print(f"✓ Извлечение pubkey hash из P2KH: {hash_bytes.hex()[:16]}...")

        # P2SH адрес (не должен вернуть hash)
        p2sh_address = "2MzQwSSnBHWHqSAqtTVQ6v47XtaisrJa1Vc"
        hash_bytes2 = BCHAddress.extract_pubkey_hash(p2sh_address)
        assert hash_bytes2 is None
        print("✓ P2SH адрес не возвращает pubkey hash")

    def test_normalize_address(self):
        """Тест нормализации адресов"""
        test_address = "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"

        # Legacy -> CashAddr
        cashaddr = BCHAddress.normalize(test_address, 'cashaddr')
        assert cashaddr is not None
        assert ':' in cashaddr
        print(f"✓ Нормализация Legacy -> CashAddr: {cashaddr[:30]}...")

        # CashAddr -> Legacy
        legacy = BCHAddress.normalize(cashaddr, 'legacy')
        assert legacy is not None
        assert ':' not in legacy
        print(f"✓ Нормализация CashAddr -> Legacy: {legacy[:20]}...")

    def test_detect_network(self):
        """Тест определения сети"""
        test_cases = [
            ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "mainnet"),
            ("bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e", "testnet"),
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", "mainnet"),
            ("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", "testnet"),
        ]

        for address, expected_network in test_cases:
            network = BCHAddress.detect_network(address)
            assert network == expected_network
            print(f"✓ Определение сети {address[:20]}...: {network}")

    def test_is_valid_for_network(self):
        """Тест проверки адреса на соответствие сети"""
        testnet_addr = "bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e"

        # Правильная сеть
        assert BCHAddress.is_valid_for_network(testnet_addr, 'testnet') is True
        print("✓ Testnet адрес валиден для testnet")

        # Неправильная сеть
        assert BCHAddress.is_valid_for_network(testnet_addr, 'mainnet') is False
        print("✓ Testnet адрес невалиден для mainnet")


class TestScriptCreation:
    """Тесты создания скриптов"""

    def test_create_p2pkh_script(self):
        """Тест создания P2PKH скрипта"""
        # Тестовый pubkey hash (20 байт)
        test_hash = bytes.fromhex("7c154ed1dc59609e3d26abb2df2ea3d587cd8c41")

        script = create_p2pkh_script(test_hash)
        expected = "76a9147c154ed1dc59609e3d26abb2df2ea3d587cd8c4188ac"

        assert script == expected
        print(f"✓ Создание P2PKH скрипта: {script}")

    def test_create_p2sh_script(self):
        """Тест создания P2SH скрипта"""
        # Тестовый script hash (20 байт)
        test_hash = bytes.fromhex("8f55563b9a19f321c211e9b9f9c6b0d6c8c8c8c8")

        script = create_p2sh_script(test_hash)
        expected = "a9148f55563b9a19f321c211e9b9f9c6b0d6c8c8c8c887"

        assert script == expected
        print(f"✓ Создание P2SH скрипта: {script}")

    def test_create_coinbase_script(self):
        """Тест создания coinbase скрипта"""
        # Testnet P2KH адрес
        test_address = "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"

        script = create_coinbase_script(test_address)

        # Проверяем что скрипт создан и имеет правильный формат
        assert script is not None
        assert script.startswith("76a914")  # P2PKH начало
        assert script.endswith("88ac")  # P2PKH конец

        print(f"✓ Создание coinbase скрипта: {script[:50]}...")

    def test_detect_address_type(self):
        """Тест определения типа адреса"""
        test_cases = [
            ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "P2KH"),
            ("bchtest:pp8f7ww2g6y07ypp9r4yendrgyznysc5kfx2tamvu", "P2SH"),
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", "P2KH"),
            ("3CWFddi6m4ndiGyKqzYvsFYagqDLPVMTzC", "P2SH"),
        ]

        for address, expected_type in test_cases:
            addr_type = detect_address_type(address)
            assert addr_type == expected_type
            print(f"✓ Определение типа адреса {address[:20]}...: {addr_type}")


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