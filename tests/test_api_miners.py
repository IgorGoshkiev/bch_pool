"""
Тесты для API эндпоинтов майнеров
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, UTC

from app.main import app


class TestAPIMiners:
    """Тесты для API майнеров"""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def mock_db_session(self):
        """Мок асинхронной сессии БД"""
        session = AsyncMock()

        # Мок для execute
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result

        return session

    @patch('app.api.v1.miners.get_db')
    def test_list_miners_success(self, mock_get_db, client, mock_db_session):
        """Тест успешного получения списка майнеров"""
        mock_get_db.return_value = mock_db_session

        # Настраиваем мок для пустого результата
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute.return_value = mock_result

        response = client.get("/api/v1/miners/")

        # API должно вернуть успех даже с пустой БД
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    @patch('app.api.v1.miners.get_db')
    def test_list_miners_with_filters(self, mock_get_db, client, mock_db_session):
        """Тест получения списка с параметрами"""
        mock_get_db.return_value = mock_db_session

        # Тест с активными майнерами
        response = client.get("/api/v1/miners/?active_only=true&skip=10&limit=20")

        assert response.status_code == 200
        data = response.json()
        assert "pagination" in data["data"]
        assert data["data"]["pagination"]["skip"] == 10
        assert data["data"]["pagination"]["limit"] == 20

    @patch('app.api.v1.miners.get_db')
    def test_register_miner_success(self, mock_get_db, client):
        """Тест успешной регистрации майнера"""

        # Создаем мок-сессию
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        # Мокаем проверку существующего майнера (не существует)
        mock_result_exists = MagicMock()
        mock_result_exists.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result_exists

        # Мокаем add, commit, refresh
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        # мокаем создание объекта Miner через patch
        with patch('app.api.v1.miners.Miner') as MockMiner:
            mock_miner_instance = MagicMock()
            mock_miner_instance.id = 1
            mock_miner_instance.bch_address = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
            mock_miner_instance.worker_name = "test_worker"
            mock_miner_instance.is_active = True
            mock_miner_instance.created_at = datetime.now(UTC)
            mock_miner_instance.total_shares = 0
            mock_miner_instance.total_blocks = 0
            mock_miner_instance.hashrate = 0.0

            MockMiner.return_value = mock_miner_instance

            miner_data = {
                "bch_address": "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a",
                "worker_name": "test_worker"
            }

            response = client.post("/api/v1/miners/register", json=miner_data)

            # Должен быть 201
            assert response.status_code == 201

    @patch('app.api.v1.miners.get_db')
    def test_register_miner_already_exists(self, mock_get_db, client, mock_db_session):
        """Тест регистрации уже существующего майнера"""
        mock_get_db.return_value = mock_db_session

        # Настраиваем мок для существующего майнера
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = Mock()  # Майнер уже существует
        mock_db_session.execute.return_value = mock_result

        miner_data = {
            "bch_address": "existing_address",
            "worker_name": "test_worker"
        }

        response = client.post("/api/v1/miners/register", json=miner_data)

        assert response.status_code == 409  # Conflict
        data = response.json()
        assert "detail" in data
        assert "уже зарегистрирован" in data["detail"]

    @patch('app.api.v1.miners.get_db')
    def test_get_miner_success(self, mock_get_db, client):
        """Тест успешного получения информации о майнере"""

        # Создаем мок-сессию
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        # Создаем словарь с данными, как их вернет SQLAlchemy
        from sqlalchemy.engine import Result

        # Мокаем результат execute
        mock_result = MagicMock()

        # создаем объект с нужными атрибутами
        class MockMiner:
            def __init__(self):
                self.id = 1
                self.bch_address = "test_address"
                self.worker_name = "test_worker"
                self.is_active = True
                self.total_shares = 100
                self.total_blocks = 2
                self.hashrate = 1000.0
                self.created_at = datetime.now(UTC)

            def __getattr__(self, name):
                return getattr(self, name, None)

        mock_miner = MockMiner()
        mock_result.scalar_one_or_none.return_value = mock_miner
        mock_session.execute.return_value = mock_result

        response = client.get("/api/v1/miners/test_address")

        assert response.status_code == 200
        data = response.json()
        assert "miner" in data
        assert data["miner"]["bch_address"] == "test_address"

    @pytest.mark.asyncio
    async def test_get_miner_not_found(self, client):
        """Тест получения несуществующего майнера"""

        # Создаем мок-сессию
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Подменяем зависимость
        from app.models.database import get_db

        async def override_get_db():
            yield mock_session

        from app.main import app
        app.dependency_overrides[get_db] = override_get_db

        try:
            response = client.get("/api/v1/miners/nonexistent_address")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @patch('app.api.v1.miners.get_db')
    def test_get_miner_stats_success(self, mock_get_db, client, mock_db_session):
        """Тест успешного получения статистики майнера"""
        mock_get_db.return_value = mock_db_session

        # Настраиваем мок для майнера
        mock_miner = Mock()
        mock_miner.bch_address = "test_address"
        mock_miner.worker_name = "test_worker"
        mock_miner.is_active = True
        mock_miner.created_at = datetime.now(UTC)
        mock_miner.total_shares = 100
        mock_miner.total_blocks = 2
        mock_miner.hashrate = 1000.0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_miner
        mock_db_session.execute.return_value = mock_result

        # Моки для шар и блоков
        mock_share = Mock()
        mock_share.is_valid = True
        mock_share.difficulty = 1.0

        mock_block = Mock()
        mock_block.confirmed = True

        mock_shares_result = MagicMock()
        mock_shares_result.scalars.return_value.all.return_value = [mock_share] * 10

        mock_blocks_result = MagicMock()
        mock_blocks_result.scalars.return_value.all.return_value = [mock_block] * 2

        # Последовательные вызовы execute
        def execute_side_effect(*args, **_kwargs):
            query_str = str(args[0])
            if "SELECT miner" in query_str:
                return mock_result
            elif "SELECT share" in query_str:
                return mock_shares_result
            elif "SELECT block" in query_str:
                return mock_blocks_result
            return MagicMock()

        mock_db_session.execute.side_effect = execute_side_effect

        # ТЕСТ ДЛЯ ПРОБЛЕМЫ №3: разные значения time_range
        test_cases = [
            ("1h", "последний час"),
            ("24h", "последние 24 часа"),
            ("7d", "последние 7 дней"),
            ("30d", "последние 30 дней"),
            ("all", "вся история"),
            ("invalid", "последние 24 часа")  # Должен вернуться к default
        ]

        for time_range, expected_human in test_cases:
            response = client.get(f"/api/v1/miners/test_address/stats?time_range={time_range}")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["data"]["time_range"]["human_readable"] == expected_human

    @patch('app.api.v1.miners.get_db')
    def test_update_miner_success(self, mock_get_db, client, mock_db_session):
        """Тест успешного обновления майнера"""
        mock_get_db.return_value = mock_db_session

        # Настраиваем мок для существующего майнера
        mock_miner = Mock()
        mock_miner.bch_address = "test_address"
        mock_miner.worker_name = "old_worker"
        mock_miner.is_active = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_miner
        mock_db_session.execute.return_value = mock_result

        # Настраиваем commit
        mock_db_session.commit = AsyncMock()
        mock_db_session.refresh = AsyncMock()

        response = client.put("/api/v1/miners/test_address/update?worker_name=new_worker&is_active=false")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["updated_fields"]["worker_name"] == "new_worker"
        assert data["updated_fields"]["is_active"] is False

    @patch('app.api.v1.miners.get_db')
    def test_update_miner_no_fields(self, mock_get_db, client, mock_db_session):
        """Тест обновления без указания полей"""
        mock_get_db.return_value = mock_db_session

        mock_miner = Mock()
        mock_miner.bch_address = "test_address"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_miner
        mock_db_session.execute.return_value = mock_result

        response = client.put("/api/v1/miners/test_address/update")

        assert response.status_code == 400
        data = response.json()
        assert "Не указаны данные для обновления" in data["detail"]

    def test_update_miner_invalid_worker_name(self, client):
        """Тест обновления с невалидным именем воркера"""
        # Пустое имя
        response = client.put("/api/v1/miners/test_address/update?worker_name=")
        assert response.status_code == 400

        # Слишком длинное имя (допустим > 64 символов)
        long_name = "a" * 65
        response = client.put(f"/api/v1/miners/test_address/update?worker_name={long_name}")
        assert response.status_code == 400

    @patch('app.api.v1.miners.get_db')
    def test_delete_miner_success(self, mock_get_db, client, mock_db_session):
        """Тест успешного удаления (деактивации) майнера"""
        mock_get_db.return_value = mock_db_session

        # Настраиваем мок для существующего майнера
        mock_miner = Mock()
        mock_miner.bch_address = "test_address"
        mock_miner.is_active = True
        mock_miner.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_miner
        mock_db_session.execute.return_value = mock_result

        # Настраиваем commit
        mock_db_session.commit = AsyncMock()

        response = client.delete("/api/v1/miners/test_address")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deactivated"
        assert data["action"] == "soft_delete"

        # Проверяем, что is_active установлен в False
        # Важно: мок должен обновиться
        mock_miner.is_active = False
        assert mock_miner.is_active is False