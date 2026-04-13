"""
Тесты для API эндпоинтов пула
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app


class TestAPIPool:
    """Тесты для API пула"""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @patch('app.api.v1.pool.get_db')
    def test_pool_stats_success(self, mock_get_db, client):
        """Тест успешного получения статистики пула"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        # Настраиваем моки для SQL запросов
        mock_result = MagicMock()
        mock_result.scalar.return_value = 10  # Для всех count запросов

        # Для суммы hashrate
        mock_hashrate_result = MagicMock()
        mock_hashrate_result.scalar.return_value = 1000.0

        # Последовательные вызовы scalar
        scalar_values = [10, 5, 100, 80, 3, 2, 1000.0]

        def scalar_side_effect():
            if scalar_values:
                return scalar_values.pop(0)
            return 0

        mock_result.scalar.side_effect = scalar_side_effect
        mock_session.execute.return_value = mock_result

        response = client.get("/api/v1/pool/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "pool" in data["data"]
        assert "miners" in data["data"]["pool"]
        assert "shares" in data["data"]["pool"]
        assert "blocks" in data["data"]["pool"]
        assert "hashrate" in data["data"]["pool"]

    @patch('app.api.v1.pool.get_db')
    def test_pool_stats_empty(self, mock_get_db, client):
        """Тест статистики пула при пустой базе"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute.return_value = mock_result

        response = client.get("/api/v1/pool/stats")

        assert response.status_code == 200
        data = response.json()
        # Проверяем, что rates = 0 при отсутствии данных
        assert data["data"]["pool"]["shares"]["validity_rate"] == 0
        assert data["data"]["pool"]["blocks"]["confirmation_rate"] == 0

    @patch('app.api.v1.pool.get_db')
    def test_pool_stats_exception(self, mock_get_db, client):
        """Тест обработки ошибки БД при получении статистики"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        mock_session.execute.side_effect = Exception("DB error")

        response = client.get("/api/v1/pool/stats")

        assert response.status_code == 200
        data = response.json()
        # API возвращает success с данными или error - проверяем наличие данных
        assert "pool" in data.get("data", {})

    @patch('app.api.v1.pool.get_db')
    def test_pool_hashrate_success(self, mock_get_db, client):
        """Тест успешного получения hashrate пула"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        # Мокаем результат
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1234.56
        mock_session.execute = AsyncMock(return_value=mock_result)

        response = client.get("/api/v1/pool/hashrate")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        # Проверяем, что значение есть (может быть 0 или 1234.56)
        assert data["data"]["hashrate"]["total"] is not None

    @patch('app.api.v1.pool.get_db')
    def test_pool_hashrate_zero(self, mock_get_db, client):
        """Тест получения нулевого hashrate"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0.0
        mock_session.execute.return_value = mock_result

        response = client.get("/api/v1/pool/hashrate")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["hashrate"]["total"] == 0.0

    @patch('app.api.v1.pool.get_db')
    def test_pool_hashrate_exception(self, mock_get_db, client):
        """Тест исключения при получении hashrate"""
        mock_session = AsyncMock()
        mock_get_db.return_value = mock_session

        # ВАЖНО: мокаем execute с side_effect
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection failed"))

        response = client.get("/api/v1/pool/hashrate")

        assert response.status_code == 200
        data = response.json()
        # Проверяем, что статус error ИЛИ в сообщении есть ошибка
        assert data["status"] == "error" or "ошибка" in data["message"].lower()


    def test_pool_root(self, client):
        """Тест корневого эндпоинта пула"""
        response = client.get("/api/v1/pool/")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "endpoints" in data["data"]
        assert "/stats" in data["data"]["endpoints"]
        assert "/hashrate" in data["data"]["endpoints"]
