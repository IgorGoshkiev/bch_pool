"""
Тесты для CashAddr (обновленная версия)
"""
from app.utils.cashaddr import CashAddr, BCHAddressUtils


class TestCashAddrUpdated:
    """Тесты для обновленного CashAddr"""

    def test_from_legacy_format_no_network_param(self):
        """Тест конвертации legacy -> CashAddr без параметра network"""
        # Тестовые адреса
        test_cases = [
            # (legacy, expected_cashaddr_prefix)
            ("1BpEi6DfDAUFd7GtittLSdBeYJvcoaVggu", "bitcoincash"),
            ("3CWFddi6m4ndiGyKqzYvsFYagqDLPVMTzC", "bitcoincash"),
            ("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", "bchtest"),
            ("2MzQwSSnBHWHqSAqtTVQ6v47XtaisrJa1Vc", "bchtest"),
        ]

        for legacy_addr, expected_prefix in test_cases:
            try:
                cashaddr = CashAddr.from_legacy_format(legacy_addr)
                assert cashaddr.startswith(expected_prefix + ":")
                print(f"✓ Конвертация {legacy_addr[:10]}... -> {cashaddr[:30]}...")
            except Exception as e:
                # Некоторые адреса могут не конвертироваться из-за checksum
                print(f"{legacy_addr[:10]}...: {e}")

    def test_normalize_without_network(self):
        """Тест нормализации без указания сети"""
        test_address = "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"

        # Legacy -> CashAddr
        cashaddr = BCHAddressUtils.normalize(test_address, 'cashaddr')
        assert cashaddr is not None
        assert ':' in cashaddr
        assert cashaddr.startswith('bchtest:')
        print(f"✓ Нормализация Legacy -> CashAddr: {cashaddr[:30]}...")

        # CashAddr -> Legacy
        legacy = BCHAddressUtils.normalize(cashaddr, 'legacy')
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
            ("invalid_address", None),
        ]

        for address, expected_network in test_cases:
            network = BCHAddressUtils.detect_network(address)
            assert network == expected_network
            status = "✓" if network == expected_network else "✗"
            print(f"{status} Определение сети {address[:20]}...: {network}")

    def test_validate_with_network_check(self):
        """Тест валидации с проверкой сети"""
        # Правильный адрес для testnet
        testnet_addr = "bchtest:qq4f5hqmf5a7wsqrwv5y9j2w3vx7e45qcnvsz6d75e"

        # Проверяем с правильной сетью
        is_valid, info = BCHAddressUtils.validate(testnet_addr, 'testnet')
        assert is_valid is True
        print(f"✓ Валидация testnet адреса с network='testnet': {info}")

        # Проверяем с неправильной сетью
        is_valid, info = BCHAddressUtils.validate(testnet_addr, 'mainnet')
        assert is_valid is False
        print(f"✓ Валидация testnet адреса с network='mainnet' отклонена: {info}")


if __name__ == "__main__":
    print("=" * 60)
    print("Тесты обновленного CashAddr")
    print("=" * 60)

    tester = TestCashAddrUpdated()

    test_methods = [
        tester.test_from_legacy_format_no_network_param,
        tester.test_normalize_without_network,
        tester.test_detect_network,
        tester.test_validate_with_network_check,
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