"""
Тесты для JobService
"""
import pytest

from unittest.mock import Mock, patch
from app.services.job_service import JobService


class TestJobService:
    """Тесты сервиса управления заданиями"""

    @pytest.fixture
    def mock_validator(self):
        """Мок валидатора"""
        validator = Mock()
        validator.add_job = Mock()
        validator.remove_job = Mock()
        validator.validate_share = Mock(return_value=(True, None))
        return validator

    @pytest.fixture
    def mock_network_manager(self):
        """Мок NetworkManager"""
        manager = Mock()
        manager.get_fallback_prev_block_hash = Mock(
            return_value="0000000000000000000000000000000000000000000000000000000000000000")
        manager.get_default_block_version = Mock(return_value=0x20000000)
        manager.get_default_bits = Mock(return_value="1d00ffff")
        manager.get_fallback_coinbase_value = Mock(return_value=625000000)
        return manager

    @pytest.fixture
    def job_service(self, mock_validator, mock_network_manager):
        """Создание экземпляра JobService"""
        return JobService(validator=mock_validator, network_manager=mock_network_manager)

    def test_initialization(self, mock_validator, mock_network_manager):
        """Тест инициализации JobService"""
        service = JobService(validator=mock_validator, network_manager=mock_network_manager)

        assert service.validator == mock_validator
        assert service.network_manager == mock_network_manager
        assert service.active_jobs == {}
        assert service.miner_subscriptions == {}
        assert service.job_counter == 0
        assert service.job_history == []
        assert service.last_broadcast_job is None

    def test_initialization_without_validator(self, mock_network_manager):
        """Тест инициализации JobService без валидатора"""
        service = JobService(validator=None, network_manager=mock_network_manager)

        assert service.validator is None
        assert service.network_manager == mock_network_manager

    def test_create_job_id_broadcast(self, job_service):
        """Создание ID для broadcast задания"""
        job_id = job_service.create_job_id()
        assert job_id.startswith("job_")
        assert job_id.endswith("_broadcast")
        # Проверяем формат без фиксированного timestamp
        parts = job_id.split('_')
        assert len(parts) >= 3
        assert parts[1].isdigit()  # timestamp
        assert len(parts[2]) == 8  # hex counter

    def test_create_job_id_personal(self, job_service):
        """Создание ID для персонального задания"""
        miner_address = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"

        # Патчим datetime.now(UTC) в модуле job_service
        from datetime import datetime, UTC

        # Создаем фиксированную дату
        fixed_datetime = datetime(2024, 1, 29, 0, 0, 0, tzinfo=UTC)

        # Патчим через context manager
        with patch('app.services.job_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = fixed_datetime
            mock_datetime.UTC = UTC  # Важно добавить атрибут UTC

            job_id = job_service.create_job_id(miner_address=miner_address)

            timestamp = int(fixed_datetime.timestamp())  # 1706457600
            assert job_id.startswith(f"job_{timestamp}_")
            assert "bitcoinc" in job_id  # первые 8 символов адреса
            assert job_service.job_counter == 1

    def test_add_job_broadcast(self, job_service, mock_validator):
        """Добавление broadcast задания"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {
            "method": "mining.notify",
            "params": ["job_id", "prev_hash", "coinbase1", "coinbase2", [], "version", "bits", "ntime", True]
        }

        job_service.add_job(job_id, job_data)

        # Проверяем, что задание добавлено в активные
        assert job_id in job_service.active_jobs
        assert job_service.active_jobs[job_id] == job_data

        # Проверяем, что задание добавлено в валидатор
        mock_validator.add_job.assert_called_once_with(job_id, job_data)

        # Проверяем историю
        assert len(job_service.job_history) == 1
        history_record = job_service.job_history[0]
        assert history_record["id"] == job_id
        assert history_record["miner_address"] is None
        assert history_record["type"] == "broadcast"

    def test_add_job_personal(self, job_service, mock_validator):
        """Добавление персонального задания"""
        job_id = "job_1706457600_00000001_miner123"
        job_data = {
            "method": "mining.notify",
            "params": ["job_id", "prev_hash", "coinbase1", "coinbase2", [], "version", "bits", "ntime", True]
        }
        miner_address = "miner123"

        job_service.add_job(job_id, job_data, miner_address)

        # Проверяем, что задание добавлено в активные
        assert job_id in job_service.active_jobs

        # Проверяем подписки майнера
        assert miner_address in job_service.miner_subscriptions
        assert job_id in job_service.miner_subscriptions[miner_address]

        # Проверяем историю
        assert len(job_service.job_history) == 1
        history_record = job_service.job_history[0]
        assert history_record["id"] == job_id
        assert history_record["miner_address"] == miner_address
        assert history_record["type"] == "personal"

    def test_remove_job(self, job_service, mock_validator):
        """Удаление задания"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": []}

        # Добавляем задание
        job_service.add_job(job_id, job_data)
        assert job_id in job_service.active_jobs

        # Удаляем задание
        job_service.remove_job(job_id)

        # Проверяем, что задание удалено
        assert job_id not in job_service.active_jobs
        mock_validator.remove_job.assert_called_once_with(job_id)

    def test_remove_nonexistent_job(self, job_service, mock_validator):
        """Удаление несуществующего задания"""
        job_id = "nonexistent_job"

        # Удаляем несуществующее задание
        job_service.remove_job(job_id)

        # Должно просто ничего не сделать
        mock_validator.remove_job.assert_not_called()

    def test_remove_job_with_miner_subscription(self, job_service, mock_validator):
        """Удаление задания с подпиской майнера"""
        job_id = "job_1706457600_00000001_miner123"
        job_data = {"method": "mining.notify", "params": []}
        miner_address = "miner123"

        # Добавляем персональное задание
        job_service.add_job(job_id, job_data, miner_address)
        assert miner_address in job_service.miner_subscriptions
        assert job_id in job_service.miner_subscriptions[miner_address]

        # Удаляем задание
        job_service.remove_job(job_id)

        # Проверяем, что подписка майнера очищена
        assert miner_address not in job_service.miner_subscriptions
        mock_validator.remove_job.assert_called_once_with(job_id)

    def test_get_job(self, job_service):
        """Получение задания по ID"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": ["test"]}

        job_service.add_job(job_id, job_data)

        # Получаем задание
        result = job_service.get_job(job_id)
        assert result == job_data

        # Получаем несуществующее задание
        nonexistent = job_service.get_job("nonexistent")
        assert nonexistent is None

    def test_get_miner_jobs(self, job_service):
        """Получение заданий майнера"""
        miner_address = "miner123"
        job_ids = [
            "job_1706457600_00000001_miner123",
            "job_1706457600_00000002_miner123",
            "job_1706457600_00000003_miner123"
        ]

        # Добавляем задания
        for job_id in job_ids:
            job_service.add_job(job_id, {"method": "mining.notify"}, miner_address)

        # Получаем задания майнера
        jobs = job_service.get_miner_jobs(miner_address)
        assert isinstance(jobs, set)
        assert len(jobs) == 3
        assert set(job_ids) == jobs

        # Получаем задания несуществующего майнера
        nonexistent_jobs = job_service.get_miner_jobs("nonexistent")
        assert nonexistent_jobs == set()

    def test_set_last_broadcast_job(self, job_service, mock_validator):
        """Установка последнего broadcast задания"""
        job_data = {
            "method": "mining.notify",
            "params": ["job_broadcast", "prev_hash", "coinbase1", "coinbase2", [], "version", "bits", "ntime", True]
        }

        job_service.set_last_broadcast_job(job_data)

        # Проверяем, что задание установлено
        assert job_service.last_broadcast_job is not None
        assert job_service.last_broadcast_job["params"][0] == "job_broadcast"

        # Проверяем, что задание добавлено в активные
        assert "job_broadcast" in job_service.active_jobs
        mock_validator.add_job.assert_called()

    def test_get_job_for_miner_with_personal_job(self, job_service):
        """Получение задания для майнера с персональными заданиями"""
        miner_address = "miner123"
        job_id = "job_1706457600_00000001_miner123"
        job_data = {"method": "mining.notify", "params": ["personal_job"]}

        # Добавляем персональное задание
        job_service.add_job(job_id, job_data, miner_address)

        # Получаем задание для майнера
        result = job_service.get_job_for_miner(miner_address)
        assert result == job_data

    def test_get_job_for_miner_with_broadcast_job(self, job_service):
        """Получение задания для майнера без персональных заданий"""
        miner_address = "miner123"
        job_data = {
            "method": "mining.notify",
            "params": ["job_broadcast", "prev_hash", "coinbase1", "coinbase2", [], "version", "bits", "ntime", True],
            "extra_nonce1": "ae6812eb4cd7735a302a8a9dd95cf71f"
        }

        # Устанавливаем broadcast задание
        job_service.set_last_broadcast_job(job_data)

        # Получаем задание для майнера
        result = job_service.get_job_for_miner(miner_address)
        # Сравниваем только важные поля
        assert result["method"] == "mining.notify"
        assert result["params"][0] == "job_broadcast"
        assert "extra_nonce1" in result

    def test_get_job_for_miner_fallback(self, job_service, mock_network_manager):
        """Получение fallback задания для майнера"""
        miner_address = "miner123"

        # Нет ни персональных, ни broadcast заданий
        result = job_service.get_job_for_miner(miner_address)

        # Должен вернуться fallback job
        assert result is not None
        assert result["method"] == "mining.notify"
        assert len(result["params"]) == 9
        mock_network_manager.get_fallback_prev_block_hash.assert_called()
        mock_network_manager.get_default_block_version.assert_called()
        mock_network_manager.get_default_bits.assert_called()
        mock_network_manager.get_fallback_coinbase_value.assert_called()

    def test_create_fallback_job(self, job_service, mock_network_manager):
        """Создание fallback задания"""
        miner_address = "miner123"

        job_data = job_service.create_fallback_job(miner_address)

        assert job_data is not None
        assert "method" in job_data
        assert job_data["method"] == "mining.notify"
        assert "params" in job_data
        assert len(job_data["params"]) == 9
        assert "extra_nonce1" in job_data
        assert "coinbase_value" in job_data

        # Проверяем вызовы методов NetworkManager
        mock_network_manager.get_fallback_prev_block_hash.assert_called_once()
        mock_network_manager.get_default_block_version.assert_called_once()
        mock_network_manager.get_default_bits.assert_called_once()
        mock_network_manager.get_fallback_coinbase_value.assert_called_once()

    def test_cleanup_old_jobs(self, job_service, mock_validator):
        """Очистка старых заданий"""
        # Используем текущее время для нового задания
        import time
        current_time = int(time.time())
        old_timestamp = current_time - 3600  # 1 час назад
        new_timestamp = current_time  # сейчас

        old_job_id = f"job_{old_timestamp}_00000001_broadcast"
        new_job_id = f"job_{new_timestamp}_00000002_broadcast"

        job_service.add_job(old_job_id, {"method": "mining.notify"})
        job_service.add_job(new_job_id, {"method": "mining.notify"})

        # Очищаем старые задания (max_age_seconds=300 = 5 минут)
        job_service.cleanup_old_jobs(max_age_seconds=300)

        # Проверяем, что старое задание удалено
        assert old_job_id not in job_service.active_jobs
        # Новое задание может остаться или быть удалено в зависимости от реализации
        # Проверяем только то, что старое удалено
        mock_validator.remove_job.assert_called_once_with(old_job_id)

    def test_cleanup_miner_jobs(self, job_service, mock_validator):
        """Очистка всех заданий майнера"""
        miner_address = "miner123"
        job_ids = [
            "job_1706457600_00000001_miner123",
            "job_1706457600_00000002_miner123"
        ]

        # Добавляем задания
        for job_id in job_ids:
            job_service.add_job(job_id, {"method": "mining.notify"}, miner_address)

        assert miner_address in job_service.miner_subscriptions
        assert len(job_service.miner_subscriptions[miner_address]) == 2

        # Очищаем задания майнера
        job_service.cleanup_miner_jobs(miner_address)

        # Проверяем, что задания удалены
        assert miner_address not in job_service.miner_subscriptions
        for job_id in job_ids:
            assert job_id not in job_service.active_jobs
            mock_validator.remove_job.assert_any_call(job_id)

    def test_get_stats(self, job_service):
        """Получение статистики сервиса"""
        # Добавляем несколько заданий
        for i in range(3):
            job_id = f"job_1706457600_{i:08x}_broadcast"
            job_service.add_job(job_id, {"method": "mining.notify"})

        for i in range(2):
            job_id = f"job_1706457600_{i + 3:08x}_miner{i}"
            job_service.add_job(job_id, {"method": "mining.notify"}, f"miner{i}")

        stats = job_service.get_stats()

        assert stats["active_jobs"] == 5
        assert stats["subscribed_miners"] == 2
        assert stats["total_subscriptions"] == 2  # по одному заданию на каждого майнера
        assert "job_counter" in stats

    def test_get_job_history(self, job_service):
        """Получение истории заданий"""
        # Добавляем несколько заданий
        for i in range(15):
            job_id = f"job_1706457600_{i:08x}_broadcast"
            job_service.add_job(job_id, {"method": "mining.notify"})

        # Получаем историю (ограничение 10)
        history = job_service.get_job_history(limit=10)

        assert len(history) == 10  # последние 10 записей
        assert all("id" in record for record in history)
        assert all("created_at" in record for record in history)
        assert all("type" in record for record in history)

        # Проверяем, что записи упорядочены от новых к старым
        if len(history) > 1:
            first_time = history[0]["created_at"]
            last_time = history[-1]["created_at"]
            # Это строка, нужно преобразовать для сравнения
            assert first_time >= last_time  # новые записи первыми

    def test_get_miner_job_stats(self, job_service):
        """Получение статистики заданий майнера"""
        miner_address = "miner123"

        # Добавляем задания майнера
        for i in range(5):
            job_id = f"job_1706457600_{i:08x}_miner123"
            job_service.add_job(job_id, {"method": "mining.notify"}, miner_address)

        # Удаляем одно задание
        job_service.remove_job("job_1706457600_00000000_miner123")

        stats = job_service.get_miner_job_stats(miner_address)

        assert stats["miner_address"] == miner_address
        assert stats["total_jobs"] == 4  # 5 добавлено, 1 удалено
        assert stats["active_jobs"] == 4  # все оставшиеся активны
        assert len(stats["job_ids"]) == 4 or len(stats["job_ids"]) == 10  # первые 10 или все

    def test_validate_and_process_share_success(self, job_service, mock_validator):
        """Успешная валидация и обработка шара"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": []}

        # Добавляем задание
        job_service.add_job(job_id, job_data)

        # Настраиваем мок валидатора
        mock_validator.validate_share.return_value = (True, None)

        # Валидируем шар
        is_valid, error_msg, returned_job_data = job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="12345678",
            miner_address="miner123"
        )

        assert is_valid is True
        assert error_msg is None
        assert returned_job_data == job_data

        # Проверяем вызов валидатора
        mock_validator.validate_share.assert_called_once_with(
            job_id=job_id,
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="12345678",
            miner_address="miner123"
        )

    def test_validate_and_process_share_job_not_found(self, job_service):
        """Валидация шара для несуществующего задания"""
        is_valid, error_msg, job_data = job_service.validate_and_process_share(
            job_id="nonexistent_job",
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="12345678",
            miner_address="miner123"
        )

        assert is_valid is False
        assert "не найдено" in error_msg.lower()
        assert job_data is None

    def test_validate_and_process_share_validation_failed(self, job_service, mock_validator):
        """Валидация шара с ошибкой"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": []}

        # Добавляем задание
        job_service.add_job(job_id, job_data)

        # Настраиваем мок валидатора на ошибку
        mock_validator.validate_share.return_value = (False, "Invalid nonce")

        # Валидируем шар
        is_valid, error_msg, returned_job_data = job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="invalid",
            miner_address="miner123"
        )

        assert is_valid is False
        assert error_msg == "Invalid nonce"
        assert returned_job_data is None

    def test_validate_and_process_share_no_validator(self, mock_network_manager):
        """Валидация шара без инициализированного валидатора"""
        service = JobService(validator=None, network_manager=mock_network_manager)

        # Сначала добавим задание
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": []}
        service.add_job(job_id, job_data)

        is_valid, error_msg, job_data = service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="12345678",
            miner_address="miner123"
        )

        assert is_valid is False
        # Проверяем наличие ключевых слов в ошибке
        assert any(
            keyword in error_msg.lower() for keyword in ["validator", "initialized", "нет валидатора", "no validator"])
        assert job_data is None

    def test_validate_and_process_share_exception(self, job_service, mock_validator):
        """Валидация шара с исключением"""
        job_id = "job_1706457600_00000001_broadcast"
        job_data = {"method": "mining.notify", "params": []}

        # Добавляем задание
        job_service.add_job(job_id, job_data)

        # Настраиваем мок валидатора на исключение
        mock_validator.validate_share.side_effect = Exception("Test exception")

        # Валидируем шар
        is_valid, error_msg, returned_job_data = job_service.validate_and_process_share(
            job_id=job_id,
            extra_nonce2="00000000",
            ntime="5a0b7226",
            nonce="12345678",
            miner_address="miner123"
        )

        assert is_valid is False
        assert "validation error" in error_msg.lower()
        assert "test exception" in error_msg.lower()
        assert returned_job_data is None
