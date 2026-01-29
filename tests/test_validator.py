"""
Тесты для ShareValidator
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import Mock, patch


@pytest.fixture
def validator():
    """Создание валидатора для тестов"""
    from app.stratum.validator import ShareValidator
    return ShareValidator(target_difficulty=1.0)


@pytest.fixture
def mock_job_data():
    """Тестовые данные задания"""
    return {
        "method": "mining.notify",
        "params": [
            "test_job_001",
            "abcd" * 16,  # prevhash
            "fdfd0800",  # coinb1
            "",  # coinb2
            [],  # merkle_branch
            "20000000",  # version
            "1d00ffff",  # nbits
            format(int(datetime.now(UTC).timestamp()), '08x'),  # ntime
            True
        ],
        "extra_nonce1": "ae6812eb4cd7735a302a8a9dd95cf71f"
    }


class TestShareValidator:
    """Тесты для ShareValidator"""

    def test_initialization(self, validator):
        """Тест инициализации валидатора"""
        assert validator.target_difficulty == 1.0
        assert validator.extra_nonce2_size == 4  # По умолчанию
        assert len(validator.jobs_cache) == 0
        assert validator.validated_shares == 0
        assert validator.invalid_shares == 0

    def test_add_remove_job(self, validator, mock_job_data):
        """Тест добавления и удаления заданий"""
        job_id = "test_job_001"

        # Добавляем задание
        validator.add_job(job_id, mock_job_data)
        assert job_id in validator.jobs_cache
        assert len(validator.jobs_cache) == 1

        # Удаляем задание
        validator.remove_job(job_id)
        assert job_id not in validator.jobs_cache
        assert len(validator.jobs_cache) == 0

    def test_validate_hex_format(self, validator):
        """Тест проверки hex формата"""
        # Корректные hex строки
        assert validator._validate_hex_format("12345678", 8) == True
        assert validator._validate_hex_format("abcdef", 6) == True

        # Некорректные hex строки
        assert validator._validate_hex_format("12345g78", 8) == False  # не hex символ
        assert validator._validate_hex_format("1234567", 8) == False  # неправильная длина
        assert validator._validate_hex_format("", 8) == False  # пустая строка

    def test_validate_ntime(self, validator):
        """Тест проверки времени"""
        current_time = int(datetime.now(UTC).timestamp())

        # Корректное время (текущее)
        assert validator._validate_ntime(format(current_time, '08x')) == True

        # Корректное время (2 часа назад)
        two_hours_ago = current_time - 2 * 60 * 60
        assert validator._validate_ntime(format(two_hours_ago, '08x')) == True

        # Некорректное время (3 часа назад - больше допустимого)
        three_hours_ago = current_time - 3 * 60 * 60
        assert validator._validate_ntime(format(three_hours_ago, '08x')) == False

        # Некорректный формат
        assert validator._validate_ntime("nothex") == False

    def test_check_nonce_uniqueness(self, validator):
        """Тест проверки уникальности nonce"""
        job_id = "test_job_001"
        nonce = "12345678"

        # Первый раз должен быть уникальным
        assert validator._check_nonce_uniqueness(job_id, nonce) == True

        # Второй раз - уже использовался
        assert validator._check_nonce_uniqueness(job_id, nonce) == False

        # Другой nonce для того же задания
        assert validator._check_nonce_uniqueness(job_id, "87654321") == True

    def test_validate_share_missing_job(self, validator):
        """Тест валидации шара с несуществующим заданием"""
        result, error = validator.validate_share(
            job_id="non_existent",
            extra_nonce2="00000001",
            ntime=format(int(datetime.now(UTC).timestamp()), '08x'),
            nonce="12345678",
            miner_address="test_miner"
        )

        assert result == False
        assert "не найдено" in error

    def test_validate_share_invalid_format(self, validator, mock_job_data):
        """Тест валидации шара с неверным форматом данных"""
        job_id = "test_job_001"
        validator.add_job(job_id, mock_job_data)

        # Неверный формат extra_nonce2 (слишком короткий)
        result, error = validator.validate_share(
            job_id=job_id,
            extra_nonce2="000001",  # 6 символов вместо 8
            ntime=format(int(datetime.now(UTC).timestamp()), '08x'),
            nonce="12345678",
            miner_address="test_miner"
        )

        assert result == False
        assert "extra_nonce2" in error

    def test_calculate_hash(self, validator, mock_job_data):
        """Тест расчета хэша"""
        extra_nonce2 = "00000001"
        ntime = format(int(datetime.now(UTC).timestamp()), '08x')
        nonce = "12345678"

        hash_result = validator.calculate_hash(mock_job_data, extra_nonce2, ntime, nonce)

        assert len(hash_result) == 64
        assert hash_result != "0" * 64  # Не должно быть нулевым хэшем

    def test_check_difficulty(self, validator):
        """Тест проверки сложности"""
        # Хэш, который явно превышает target для сложности 1.0
        max_hash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        assert validator.check_difficulty(max_hash, 1.0) == False

        # Хэш, который ДОЛЖЕН пройти при сложности 0.001
        # Нужен хэш, который меньше target для сложности 0.001
        # Target для сложности 0.001 = target_for_difficulty_1 / 0.001 = target_for_difficulty_1 * 1000
        # Это ОЧЕНЬ большое число, почти любой хэш пройдет

        # Пример: хэш с многими нулями в начале
        easy_hash_hex = "00000000ffff0000000000000000000000000000000000000000000000000000"
        assert validator.check_difficulty(easy_hash_hex, 0.001) == True

        # Проверяем граничные случаи
        zero_hash = "0" * 64
        assert validator.check_difficulty(zero_hash, 1.0) == True
        assert validator.check_difficulty(zero_hash, 1000.0) == True

        # Хэш, который немного больше target_for_difficulty_1
        medium_hash = "0000000100000000000000000000000000000000000000000000000000000000"
        assert validator.check_difficulty(medium_hash, 1.0) == False
        assert validator.check_difficulty(medium_hash, 0.5) == True  # При меньшей сложности пройдет

    def test_get_stats(self, validator, mock_job_data):
        """Тест получения статистики"""
        # Добавляем задание
        validator.add_job("test_job_001", mock_job_data)

        # Валидируем несколько шаров
        validator.validate_share(
            job_id="test_job_001",
            extra_nonce2="00000001",
            ntime=format(int(datetime.now(UTC).timestamp()), '08x'),
            nonce="12345678",
            miner_address="test_miner"
        )

        stats = validator.get_stats()

        assert "jobs_in_cache" in stats
        assert "validated_shares" in stats
        assert "invalid_shares" in stats
        assert "success_rate" in stats
        assert stats["jobs_in_cache"] == 1


def test_validator_cleanup():
    """Тест очистки старых заданий"""
    from app.stratum.validator import ShareValidator

    validator = ShareValidator()

    # Добавляем задания в правильном формате ID
    # Формат: job_{timestamp}_{counter}_{address}
    validator.add_job("job_1000000000_001_test", {"params": ["job_1000000000_001_test"]})
    validator.add_job("job_1000000000_002_test", {"params": ["job_1000000000_002_test"]})

    # Добавляем новое задание
    current_time = int(datetime.now(UTC).timestamp())
    validator.add_job(f"job_{current_time}_003_test", {"params": [f"job_{current_time}_003_test"]})

    # Очищаем старые задания (timestamp=1000000000 очень старый - 2001 год)
    validator.cleanup_old_jobs(max_age_seconds=1)

    # Проверяем что старые задания удалены
    assert "job_1000000000_001_test" not in validator.jobs_cache
    assert "job_1000000000_002_test" not in validator.jobs_cache
    # Новое задание должно остаться
    assert f"job_{current_time}_003_test" in validator.jobs_cache


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])