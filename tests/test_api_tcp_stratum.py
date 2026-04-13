"""
Тесты для API TCP Stratum эндпоинтов
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, UTC, timedelta
from fastapi.testclient import TestClient

from app.main import app


class TestAPITCPStratum:
    """Тесты для API TCP Stratum"""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_get_tcp_stratum_stats_success(self, mock_tcp_server, client):
        """Тест успешного получения статистики TCP сервера"""
        mock_tcp_server.host = "0.0.0.0"
        mock_tcp_server.port = 3333
        mock_tcp_server.connections = {"client1": Mock(), "client2": Mock()}
        mock_tcp_server.miners = {"client1": "addr1", "client2": "addr2"}

        response = client.get("/api/v1/tcp-stratum/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["host"] == "0.0.0.0"
        assert data["data"]["port"] == 3333
        assert data["data"]["active_connections"] == 2
        assert data["data"]["active_miners"] == 2

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_get_tcp_stratum_stats_exception(self, mock_tcp_server, client):
        """Тест исключения при получении статистики"""

        mock_tcp_server.host = "0.0.0.0"
        mock_tcp_server.port = 3333

        # Симулируем ошибку при доступе к connections
        mock_tcp_server.connections = property(lambda obj: (_ for _ in ()).throw(Exception("Test error")))

        response = client.get("/api/v1/tcp-stratum/stats")

        assert response.status_code == 500


    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_get_tcp_connections_success(self, mock_tcp_server, client):
        """Тест успешного получения списка подключений"""
        # Создаем мок writer с peername
        mock_writer = Mock()
        mock_writer.get_extra_info.return_value = ("192.168.1.1", 12345)

        mock_tcp_server.connections = {"client_123": mock_writer}
        mock_tcp_server.miners = {"client_123": "test_address"}

        response = client.get("/api/v1/tcp-stratum/connections")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]["connections"]) == 1
        assert data["data"]["connections"][0]["client_id"] == "client_123"
        assert data["data"]["connections"][0]["miner_address"] == "test_address"
        assert data["data"]["connections"][0]["remote_address"] == "192.168.1.1:12345"

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_get_tcp_connections_empty(self, mock_tcp_server, client):
        """Тест получения пустого списка подключений"""
        mock_tcp_server.connections = {}
        mock_tcp_server.miners = {}

        response = client.get("/api/v1/tcp-stratum/connections")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 0
        assert data["data"]["connections"] == []

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_get_tcp_connections_exception(self, mock_tcp_server, client):
        """Тест исключения при получении подключений"""
        # Симулируем ошибку в get_extra_info
        mock_writer = Mock()
        mock_writer.get_extra_info.side_effect = Exception("Connection error")
        mock_tcp_server.connections = {"client1": mock_writer}
        mock_tcp_server.miners = {"client1": "addr1"}

        response = client.get("/api/v1/tcp-stratum/connections")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        # Сообщение об ошибке может быть любым
        assert len(data["detail"]) > 0

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_check_tcp_stratum_health_success(self, mock_tcp_server, client):
        """Тест успешной проверки здоровья TCP сервера"""

        mock_server = Mock()
        mock_server.is_serving.return_value = True

        mock_tcp_server.server = mock_server
        mock_tcp_server.host = "0.0.0.0"
        mock_tcp_server.port = 3333
        mock_tcp_server.connections = {"client1": Mock()}

        # ИСПРАВЛЕНО: Просто присваиваем значение, а не создаем property
        mock_start_time = datetime.now(UTC) - timedelta(seconds=3600)
        mock_tcp_server.start_time = mock_start_time  # ← Прямое присваивание

        # Патчим datetime.now
        with patch('app.api.v1.tcp_stratum.datetime') as mock_datetime:
            mock_now = datetime.now(UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.UTC = UTC

            response = client.get("/api/v1/tcp-stratum/health")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "running"
        assert data["data"]["uptime_seconds"] == 3600

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_check_tcp_stratum_health_stopped(self, mock_tcp_server, client):
        """Тест проверки здоровья остановленного сервера"""
        # Сервер остановлен
        mock_server = Mock()
        mock_server.is_serving.return_value = False

        mock_tcp_server.server = mock_server
        mock_tcp_server.host = "0.0.0.0"
        mock_tcp_server.port = 3333
        mock_tcp_server.connections = {}

        response = client.get("/api/v1/tcp-stratum/health")

        assert response.status_code == 200
        data = response.json()
        # С исправленной логикой должно быть "stopped"
        assert data["data"]["status"] == "stopped"

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_check_tcp_stratum_health_no_server(self, mock_tcp_server, client):
        """Тест проверки здоровья когда server = None"""
        mock_tcp_server.server = None
        mock_tcp_server.host = "0.0.0.0"
        mock_tcp_server.port = 3333

        response = client.get("/api/v1/tcp-stratum/health")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "stopped"

    @patch('app.api.v1.tcp_stratum.tcp_stratum_server')
    def test_check_tcp_stratum_health_exception(self, mock_tcp_server, client):
        """Тест исключения при проверке здоровья"""
        mock_tcp_server.server = Mock()
        mock_tcp_server.server.is_serving.side_effect = Exception("Test error")

        response = client.get("/api/v1/tcp-stratum/health")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Ошибка проверки здоровья" in data["detail"]
