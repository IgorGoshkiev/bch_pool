"""
Тесты для WebSocket Stratum сервера
"""
import pytest

from unittest.mock import Mock, AsyncMock
from datetime import datetime, UTC
from fastapi import WebSocket


class TestStratumServer:
    """Тесты для StratumServer (WebSocket)"""

    @pytest.fixture
    def stratum_server(self):
        """Создаем экземпляр Stratum сервера"""
        from app.stratum.websocket_server import StratumServer

        # Мокируем зависимости
        mock_auth_service = Mock()
        mock_database_service = Mock()
        mock_job_service = Mock()

        server = StratumServer(job_manager=None)
        server.auth_service = mock_auth_service
        server.database_service = mock_database_service
        server.job_service = mock_job_service

        return server

    @pytest.fixture
    def mock_websocket(self):
        """Создаем мок WebSocket"""
        websocket = AsyncMock(spec=WebSocket)
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.receive_json = AsyncMock()
        websocket.client = Mock()
        websocket.client.host = "192.168.1.1"

        return websocket

    @pytest.mark.asyncio
    async def test_connect_success(self, stratum_server, mock_websocket):
        """Тест успешного подключения"""
        connection_id = await stratum_server.connect(mock_websocket, "test_address")

        assert connection_id is not None
        assert connection_id in stratum_server.active_connections
        assert stratum_server.active_connections[connection_id] == mock_websocket
        assert stratum_server.miner_addresses[connection_id] == "test_address"

        # Проверяем, что отправлено приветствие
        mock_websocket.accept.assert_called_once()
        mock_websocket.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_success(self, stratum_server, mock_websocket):
        """Тест успешного отключения"""
        # Сначала подключаем
        connection_id = await stratum_server.connect(mock_websocket, "test_address")

        # Настраиваем подписки
        stratum_server.subscriptions["test_address"] = {"job1", "job2"}

        # Настраиваем мок job_service для cleanup
        stratum_server.job_service.cleanup_miner_jobs = Mock()

        await stratum_server.disconnect(connection_id)

        # Проверяем, что все данные очищены
        assert connection_id not in stratum_server.active_connections
        assert connection_id not in stratum_server.miner_addresses
        assert "test_address" not in stratum_server.subscriptions

        # Проверяем cleanup
        stratum_server.job_service.cleanup_miner_jobs.assert_called_once_with("test_address")

    @pytest.mark.asyncio
    async def test_handle_message_subscribe(self, stratum_server, mock_websocket):
        """Тест обработки subscribe сообщения"""
        data = {
            "method": "mining.subscribe",
            "id": 1,
            "params": []
        }

        connection_id = "test_connection"
        stratum_server.active_connections[connection_id] = mock_websocket

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["id"] == 1
        assert "result" in call_args



    @pytest.mark.asyncio
    async def test_handle_message_authorize_success(self, stratum_server, mock_websocket):
        """Тест успешной авторизации"""
        data = {
            "method": "mining.authorize",
            "id": 2,
            "params": ["test_user", "password"]
        }

        # используем реальный connection_id
        connection_id = str(id(mock_websocket))  # ← Как в реальном коде
        stratum_server.active_connections[connection_id] = mock_websocket

        # Настраиваем успешную авторизацию
        stratum_server.auth_service.authorize_miner = AsyncMock(
            return_value=(True, "authorized_address", None)
        )

        stratum_server.send_new_job = AsyncMock()

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        # Проверяем, что адрес обновлен
        assert connection_id in stratum_server.miner_addresses
        assert stratum_server.miner_addresses[connection_id] == "authorized_address"


    @pytest.mark.asyncio
    async def test_handle_message_authorize_failure(self, stratum_server, mock_websocket):
        """Тест неудачной авторизации"""
        data = {
            "method": "mining.authorize",
            "id": 2,
            "params": ["invalid_user", "wrong_pass"]
        }

        connection_id = "test_connection"
        stratum_server.active_connections[connection_id] = mock_websocket

        # Настраиваем неудачную авторизацию
        stratum_server.auth_service.authorize_miner = AsyncMock(
            return_value=(False, None, "Invalid credentials")
        )

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        # Проверяем ошибку
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert "error" in call_args
        assert "Invalid credentials" in call_args["error"][1]

        # Адрес не должен быть обновлен
        assert connection_id not in stratum_server.miner_addresses

    @pytest.mark.asyncio
    async def test_handle_message_submit_success(self, stratum_server, mock_websocket):
        """Тест успешной обработки шара"""
        data = {
            "method": "mining.submit",
            "id": 3,
            "params": ["worker1", "job_123", "extra2", "ntime", "nonce"]
        }

        connection_id = "test_connection"
        miner_address = "test_address"

        stratum_server.active_connections[connection_id] = mock_websocket
        stratum_server.miner_addresses[connection_id] = miner_address

        # Настраиваем успешную валидацию
        stratum_server.job_service.validate_and_process_share.return_value = (
            True, None, {"job": "data"}
        )

        # Настраиваем сохранение в БД
        stratum_server.database_service.save_share = AsyncMock(
            return_value=(True, "share_123")
        )

        # Настраиваем JobManager
        mock_job_manager = Mock()
        mock_job_manager.validate_and_save_share = AsyncMock(
            return_value={"status": "accepted", "message": "Share accepted"}
        )
        stratum_server.job_manager = mock_job_manager

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        # Проверяем цепочку вызовов
        stratum_server.job_service.validate_and_process_share.assert_called_once()
        stratum_server.database_service.save_share.assert_called_once()
        mock_job_manager.validate_and_save_share.assert_called_once()

        # Проверяем успешный ответ
        mock_websocket.send_json.assert_called()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["result"] is True

    @pytest.mark.asyncio
    async def test_handle_message_submit_no_job_manager(self, stratum_server, mock_websocket):
        """Тест обработки шара без JobManager"""
        data = {
            "method": "mining.submit",
            "id": 3,
            "params": ["worker1", "job_123", "extra2", "ntime", "nonce"]
        }

        connection_id = "test_connection"
        miner_address = "test_address"

        stratum_server.active_connections[connection_id] = mock_websocket
        stratum_server.miner_addresses[connection_id] = miner_address
        stratum_server.job_manager = None  # Нет JobManager

        # Настраиваем успешную валидацию
        stratum_server.job_service.validate_and_process_share.return_value = (
            True, None, {"job": "data"}
        )

        # Настраиваем сохранение в БД
        stratum_server.database_service.save_share = AsyncMock(
            return_value=(True, "share_123")
        )

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        # Проверяем, что шар сохранен даже без JobManager
        stratum_server.database_service.save_share.assert_called_once()

        # Проверяем ответ
        mock_websocket.send_json.assert_called()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["result"] is True

    @pytest.mark.asyncio
    async def test_handle_message_submit_invalid(self, stratum_server, mock_websocket):
        """Тест невалидного шара"""
        data = {
            "method": "mining.submit",
            "id": 3,
            "params": ["worker1", "job_123", "extra2", "ntime", "nonce"]
        }

        connection_id = "test_connection"
        miner_address = "test_address"

        stratum_server.active_connections[connection_id] = mock_websocket
        stratum_server.miner_addresses[connection_id] = miner_address

        # Настраиваем невалидный шар
        stratum_server.job_service.validate_and_process_share.return_value = (
            False, "Invalid share", None
        )

        await stratum_server.handle_message(mock_websocket, connection_id, data)

        # Проверяем ошибку
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert "error" in call_args
        assert "Invalid share" in call_args["error"][1]

    @pytest.mark.asyncio
    async def test_send_new_job_success(self, stratum_server, mock_websocket):
        """Тест успешной отправки задания"""
        miner_address = "test_address"

        # Настраиваем получение задания
        job_data = {
            "method": "mining.notify",
            "params": ["job_123", "prevhash", "coinbase", [], "version", "bits", "ntime", True]
        }
        stratum_server.job_service.get_job_for_miner.return_value = job_data

        await stratum_server.send_new_job(mock_websocket, miner_address)

        # Проверяем отправку
        mock_websocket.send_json.assert_called_once_with(job_data)

        # Проверяем подписку
        assert miner_address in stratum_server.subscriptions
        assert "job_123" in stratum_server.subscriptions[miner_address]

    @pytest.mark.asyncio
    async def test_send_new_job_no_job(self, stratum_server, mock_websocket):
        """Тест отправки задания при отсутствии заданий"""
        miner_address = "test_address"

        # Настраиваем отсутствие задания
        stratum_server.job_service.get_job_for_miner.return_value = None

        await stratum_server.send_new_job(mock_websocket, miner_address)

        # Проверяем ошибку
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert "error" in call_args
        assert "No job available" in call_args["error"][1]

    @pytest.mark.asyncio
    async def test_broadcast_new_job(self, stratum_server):
        """Тест рассылки задания всем подключенным"""
        # Создаем несколько подключений
        websocket1 = AsyncMock()
        websocket2 = AsyncMock()

        stratum_server.active_connections = {
            "conn1": websocket1,
            "conn2": websocket2
        }
        stratum_server.miner_addresses = {
            "conn1": "addr1",
            "conn2": "addr2"
        }

        # Настраиваем job_service
        stratum_server.job_service.create_job_id = Mock(side_effect=["job1", "job2"])
        stratum_server.job_service.add_job = Mock()

        job_data = {
            "method": "mining.notify",
            "params": ["old_job", "prevhash", "coinbase", [], "version", "bits", "ntime", True]
        }

        await stratum_server.broadcast_new_job(job_data)

        # Проверяем отправку обоим
        assert websocket1.send_json.called
        assert websocket2.send_json.called

        # Проверяем подписки
        assert "addr1" in stratum_server.subscriptions
        assert "job1" in stratum_server.subscriptions["addr1"]
        assert "addr2" in stratum_server.subscriptions
        assert "job2" in stratum_server.subscriptions["addr2"]

    @pytest.mark.asyncio
    async def test_update_difficulty(self, stratum_server):
        """Тест рассылки обновления сложности"""
        websocket1 = AsyncMock()
        websocket2 = AsyncMock()

        stratum_server.active_connections = {
            "conn1": websocket1,
            "conn2": websocket2
        }
        stratum_server.miner_addresses = {
            "conn1": "addr1",
            "conn2": "addr2"
        }

        await stratum_server.update_difficulty(5.0)

        # Проверяем отправку обоим
        assert websocket1.send_json.called
        assert websocket2.send_json.called

        call_args = websocket1.send_json.call_args[0][0]
        assert call_args["method"] == "mining.set_difficulty"
        assert call_args["params"] == [5.0]

    @pytest.mark.asyncio
    async def test_update_miner_difficulty(self, stratum_server):
        """Тест обновления сложности для конкретного майнера"""
        websocket = AsyncMock()
        connection_id = "conn1"
        miner_address = "addr1"

        stratum_server.active_connections = {connection_id: websocket}
        stratum_server.miner_addresses = {connection_id: miner_address}

        await stratum_server.update_miner_difficulty(connection_id, 3.0)

        websocket.send_json.assert_called_once()
        call_args = websocket.send_json.call_args[0][0]
        assert call_args["params"] == [3.0]

    @pytest.mark.asyncio
    async def test_update_miner_difficulty_not_found(self, stratum_server):
        """Тест обновления сложности для несуществующего подключения"""
        await stratum_server.update_miner_difficulty("nonexistent", 3.0)

        # Ничего не должно произойти

    def test_cleanup_old_jobs(self, stratum_server):
        """Тест очистки старых заданий"""
        # Настраиваем подписки
        stratum_server.subscriptions = {
            "addr1": {"job1", "job2", "job3"},
            "addr2": {"job4"}
        }

        # Настраиваем job_service
        stratum_server.job_service.get_job = Mock(
            side_effect=lambda job_id: None if job_id == "job2" else {"id": job_id})
        stratum_server.job_service.cleanup_old_jobs = Mock()

        stratum_server.cleanup_old_jobs(max_age_seconds=300)

        # Проверяем, что несуществующие задания удалены
        assert stratum_server.subscriptions["addr1"] == {"job1", "job3"}
        assert stratum_server.subscriptions["addr2"] == {"job4"}

        # Проверяем cleanup в job_service
        stratum_server.job_service.cleanup_old_jobs.assert_called_once_with(300)

    def test_get_stats(self, stratum_server):
        """Тест получения статистики"""
        # Настраиваем данные
        stratum_server.active_connections = {"c1": Mock(), "c2": Mock()}
        stratum_server.miner_addresses = {"c1": "addr1", "c2": "addr1"}  # Один адрес дважды
        stratum_server.subscriptions = {"addr1": {"job1", "job2"}}
        stratum_server.start_time = datetime.now(UTC)

        stats = stratum_server.get_stats()

        assert stats["active_connections"] == 2
        assert stats["active_miners"] == 1  # Уникальные адреса
        assert stats["subscriptions"] == 1
        assert stats["total_subscriptions"] == 2
        assert "uptime_seconds" in stats

    def test_cleanup_all(self, stratum_server):
        """Тест полной очистки"""
        # Заполняем данные
        stratum_server.active_connections = {"c1": Mock(), "c2": Mock()}
        stratum_server.miner_addresses = {"c1": "addr1", "c2": "addr2"}
        stratum_server.subscriptions = {"addr1": {"job1"}, "addr2": {"job2"}}

        stratum_server.cleanup_all()

        # Проверяем очистку
        assert len(stratum_server.active_connections) == 0
        assert len(stratum_server.miner_addresses) == 0
        assert len(stratum_server.subscriptions) == 0