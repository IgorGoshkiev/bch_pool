"""
Тесты для API эндпоинтов заданий
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from datetime import datetime, UTC

# Импортируем main приложение для тестов
from app.main import app


class TestAPIJobs:
    """Тесты для API jobs"""

    @pytest.fixture
    def client(self):
        """Создаем тестовый клиент"""
        return TestClient(app)

    @patch('app.api.v1.jobs.job_manager')
    @patch('app.api.v1.jobs.stratum_server')
    def test_get_job_stats_success(self, _mock_stratum_server, mock_job_manager, client):
        """Тест успешного получения статистики заданий"""
        # Настраиваем моки
        mock_job_manager.get_stats.return_value = {
            "current_job": "test_job_123",
            "total_jobs_created": 100,
            "job_history_size": 10,
            "node_info": {"height": 100, "connections": 10}
        }

        response = client.get("/api/v1/jobs/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Статистика заданий получена"
        assert "job_manager" in data["data"]
        assert data["data"]["job_manager"]["current_job"] == "test_job_123"

    @patch('app.api.v1.jobs.job_manager')
    def test_get_job_stats_exception(self, mock_job_manager, client):
        """Тест исключения при получении статистики"""
        mock_job_manager.get_stats.side_effect = Exception("Test error")

        response = client.get("/api/v1/jobs/stats")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Ошибка получения статистики заданий" in data["detail"]

    @patch('app.api.v1.jobs.job_manager')
    @patch('app.api.v1.jobs.stratum_server')
    def test_get_job_history_success(self, _mock_stratum_server, mock_job_manager, client):
        """Тест успешного получения истории заданий"""
        # Настраиваем мок job_history
        mock_job_manager.job_history = [
            {
                "id": "job_1",
                "created_at": datetime.now(UTC),
                "miner_address": "test_address",
                "template": {"height": 100}
            },
            {
                "id": "job_2",
                "created_at": datetime.now(UTC),
                "template": {"height": 101}
            }
        ]

        response = client.get("/api/v1/jobs/history?limit=5")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "jobs" in data["data"]
        assert len(data["data"]["jobs"]) == 2

    def test_get_job_history_invalid_limit(self, client):
        """Тест невалидного параметра limit"""
        response = client.get("/api/v1/jobs/history?limit=0")
        assert response.status_code == 400

        response = client.get("/api/v1/jobs/history?limit=101")
        assert response.status_code == 400

    @patch('app.api.v1.jobs.job_manager')
    def test_get_job_history_empty(self, mock_job_manager, client):
        """Тест получения истории при пустой истории"""
        mock_job_manager.job_history = None

        response = client.get("/api/v1/jobs/history")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total_jobs"] == 0
        assert data["data"]["jobs"] == []

    # ТЕСТ ДЛЯ ПРОБЛЕМЫ №1: broadcast_new_job не зарегистрирован как роут
    def test_broadcast_new_job_endpoint_exists(self, client):
        """Проверяем, что эндпоинт /broadcast существует"""
        response = client.post("/api/v1/jobs/broadcast")
        # Эндпоинт существует, но может вернуть ошибку из-за отсутствия подключения к ноде
        assert response.status_code in [200, 500]

    # ТЕСТ ДЛЯ ПРОБЛЕМЫ №1: также нет эндпоинта /current
    def test_current_job_endpoint_missing(self, client):
        """Проверяем, что эндпоинт /current отсутствует"""
        response = client.get("/api/v1/jobs/current")
        assert response.status_code == 404
