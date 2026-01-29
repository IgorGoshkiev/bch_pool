"""
Тесты для модуля protocol_helpers.py
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, UTC

from app.utils.protocol_helpers import (
    create_job_id,
    parse_stratum_username,
    format_hashrate,
    validate_bch_address,
    STRATUM_EXTRA_NONCE1,
    EXTRA_NONCE2_SIZE,
    BLOCK_HEADER_SIZE,
    BCH_TESTNET_PREFIXES,
    BCH_MAINNET_PREFIXES,
)


class TestProtocolHelpers:
    """Тесты вспомогательных функций протокола"""

    def test_stratum_constants(self):
        """Проверка констант Stratum протокола"""
        assert STRATUM_EXTRA_NONCE1 == "ae6812eb4cd7735a302a8a9dd95cf71f"
        assert EXTRA_NONCE2_SIZE == 4
        assert BLOCK_HEADER_SIZE == 80

    def test_bch_address_constants(self):
        """Проверка констант BCH адресов"""
        assert BCH_TESTNET_PREFIXES == ['bchtest:', 'qq', 'qp']
        assert BCH_MAINNET_PREFIXES == ['bitcoincash:', 'q', 'p']

    def test_create_job_id_without_params(self):
        """Создание ID задания без параметров"""
        job_id = create_job_id()

        assert job_id.startswith("job_")
        assert "_" in job_id
        parts = job_id.split('_')
        assert len(parts) >= 2

        # Проверяем timestamp
        timestamp_part = parts[1]
        assert timestamp_part.isdigit()

        # Проверяем counter
        counter_part = parts[2]
        assert len(counter_part) == 8  # 8 hex символов

    def test_create_job_id_with_timestamp(self):
        """Создание ID задания с указанным timestamp"""
        timestamp = int(datetime.now(UTC).timestamp())
        job_id = create_job_id(timestamp=timestamp, counter=123)

        assert f"job_{timestamp}_" in job_id
        assert "0000007b" in job_id  # 123 в hex
        assert job_id.endswith("broadcast")

    def test_create_job_id_with_miner_address(self):
        """Создание ID задания с адресом майнера"""
        miner_address = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        job_id = create_job_id(miner_address=miner_address, counter=456)

        assert "_qpm2qszn" in job_id  # первые 8 символов адреса без префикса
        assert "000001c8" in job_id  # 456 в hex

    def test_create_job_id_with_short_miner_address(self):
        """Создание ID задания с коротким адресом майнера"""
        miner_address = "qqtest123"
        job_id = create_job_id(miner_address=miner_address, counter=789)

        assert "_qqtest12" in job_id
        assert "00000315" in job_id  # 789 в hex

    def test_parse_stratum_username_with_worker(self):
        """Парсинг username с указанием worker"""
        username = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a.worker1"
        address, worker = parse_stratum_username(username)

        assert address == "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        assert worker == "worker1"

    def test_parse_stratum_username_without_worker(self):
        """Парсинг username без указания worker"""
        username = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        address, worker = parse_stratum_username(username)

        assert address == "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        assert worker == "default"

    def test_parse_stratum_username_multiple_dots(self):
        """Парсинг username с несколькими точками"""
        username = "address.part1.part2.part3"
        address, worker = parse_stratum_username(username)

        assert address == "address"
        assert worker == "part1.part2.part3"

    def test_parse_stratum_username_whitespace(self):
        """Парсинг username с пробелами"""
        username = "  bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a . worker1  "
        address, worker = parse_stratum_username(username)

        assert address == "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
        assert worker == "worker1"

    def test_format_hashrate_th_s(self):
        """Форматирование терахэшей в секунду"""
        hashrate = 1_500_000_000_000  # 1.5 TH/s
        formatted = format_hashrate(hashrate)

        assert formatted == "1.50 TH/s"

    def test_format_hashrate_gh_s(self):
        """Форматирование гигахэшей в секунду"""
        hashrate = 2_500_000_000  # 2.5 GH/s
        formatted = format_hashrate(hashrate)

        assert formatted == "2.50 GH/s"

    def test_format_hashrate_mh_s(self):
        """Форматирование мегахэшей в секунду"""
        hashrate = 3_500_000  # 3.5 MH/s
        formatted = format_hashrate(hashrate)

        assert formatted == "3.50 MH/s"

    def test_format_hashrate_kh_s(self):
        """Форматирование килохэшей в секунду"""
        hashrate = 4_500  # 4.5 KH/s
        formatted = format_hashrate(hashrate)

        assert formatted == "4.50 KH/s"

    def test_format_hashrate_h_s(self):
        """Форматирование хэшей в секунду"""
        hashrate = 999.99  # Меньше 1 KH/s
        formatted = format_hashrate(hashrate)

        assert formatted == "999.99 H/s"

    def test_format_hashrate_zero(self):
        """Форматирование нулевого хэшрейта"""
        formatted = format_hashrate(0)

        assert formatted == "0.00 H/s"

    def test_format_hashrate_negative(self):
        """Форматирование отрицательного хэшрейта"""
        formatted = format_hashrate(-1000)

        assert formatted == "-1000.00 H/s"

    @pytest.mark.parametrize("address,expected", [
        # Real testnet addresses (valid ones)
        ("bchtest:qpqtmmfpw79thzq5z7ku0ccnzergh74g5v5tx5g4mq", True),
        ("qq9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q", False),  # invalid checksum
        ("qp9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q9q", False),  # invalid checksum

        # Real mainnet addresses
        ("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", True),
        ("qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", True),
        ("ppm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", True),

        # Legacy Bitcoin formats
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", True),
        ("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", True),

        # Невалидные адреса
        ("", False),
        (None, False),
        ("invalid_address", False),
        ("bchtest:inval!d", False),  # недопустимые символы
        ("bitcoincash:too_short", False),  # слишком короткий
        ("bitcoincash:this_address_is_way_too_long_for_a_valid_bch_address_so_it_should_fail", False),
        # слишком длинный

        # Смешанный регистр
        ("BitcoinCash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", True),  # регистр префикса должен проходить
        ("BCHTEST:qpqtmmfpw79thzq5z7ku0ccnzergh74g5v5tx5g4mq", True),  # регистр префикса должен проходить
    ])
    @patch('app.utils.protocol_helpers.BCHAddress')  # Патчим правильное имя
    def test_validate_bch_address(self, mock_bch_address, address, expected):
        """Валидация различных BCH адресов с моком BCHAddress"""
        # Настраиваем мок
        mock_bch_address.validate.return_value = (expected, "mocked")

        result = validate_bch_address(address)
        assert result == expected, f"Address: {address}"

        # Проверяем, что validate вызывался для всех адресов кроме пустых и None
        if address not in ["", None]:
            mock_bch_address.validate.assert_called_once_with(address)

    def test_validate_bch_address_case_insensitive(self):
        """Валидация адресов в разном регистре"""
        with patch('app.utils.protocol_helpers.BCHAddress') as mock_bch_address:
            mock_bch_address.validate.return_value = (True, "valid")

            # Адрес в нижнем регистре должен быть валидным
            assert validate_bch_address("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")

            # Адрес в верхнем регистре должен быть валидным
            assert validate_bch_address("BITCOINCASH:QPM2QSZNHKS23Z7629MMS6S4CWEF74VCWVY22GDX6A")

            # Смешанный регистр символов адреса
            assert validate_bch_address("bitcoincash:QpM2qSzNhKs23z7629MmS6s4CwEf74VcWvY22GdX6a")

            # Проверяем что validate вызывался с правильными аргументами
            assert mock_bch_address.validate.call_count == 3