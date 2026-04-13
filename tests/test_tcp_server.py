"""
Тесты для TCP Stratum сервера
"""
import pytest

import json
from unittest.mock import Mock, AsyncMock
from datetime import datetime, UTC


class TestStratumTCPServer:
    """Тесты для StratumTCPServer"""

    @pytest.fixture
    def tcp_server(self):
        """Создаем экземпляр TCP сервера"""
        from app.stratum.tcp_server import StratumTCPServer
        server = StratumTCPServer(host="127.0.0.1", port=3333)

        # Мокируем зависимости
        server.auth_service = Mock()
        server.database_service = Mock()
        server.job_service = Mock()

        return server

    @pytest.fixture
    def mock_reader_writer(self):
        """Создаем моки reader и writer"""
        reader = AsyncMock()
        writer = AsyncMock()
        writer.get_extra_info.return_value = ("192.168.1.1", 12345)
        writer.write = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = AsyncMock()
        writer.wait_closed = AsyncMock()
        writer.is_closing.return_value = False

        return reader, writer

    @pytest.mark.asyncio
    async def test_send_welcome(self, tcp_server, mock_reader_writer):
        """Тест отправки приветственного сообщения"""
        reader, writer = mock_reader_writer

        await tcp_server._send_welcome(writer)

        # Проверяем, что был вызван _send_json
        assert writer.write.called
        # Проверяем, что отправлен JSON с приветствием
        call_args = writer.write.call_args[0][0]
        assert b"Welcome to BCH Solo Pool (TCP)" in call_args

    @pytest.mark.asyncio
    async def test_handle_subscribe(self, tcp_server, mock_reader_writer):
        """Тест обработки подписки"""
        reader, writer = mock_reader_writer

        await tcp_server._handle_subscribe(1, writer)

        assert writer.write.called
        call_args = writer.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert data["id"] == 1
        assert "result" in data
        # Проверяем наличие extra_nonce
        assert len(data["result"]) == 3

    @pytest.mark.asyncio
    async def test_handle_message_subscribe(self, tcp_server, mock_reader_writer):
        """Тест обработки subscribe сообщения"""
        reader, writer = mock_reader_writer

        data = {
            "method": "mining.subscribe",
            "id": 1,
            "params": []
        }

        await tcp_server.handle_message(data, writer, "client_123")

        assert writer.write.called

    @pytest.mark.asyncio
    async def test_handle_message_authorize_success(self, tcp_server, mock_reader_writer):
        """Тест успешной авторизации"""
        reader, writer = mock_reader_writer

        # Настраиваем мок auth_service
        tcp_server.auth_service.authorize_miner = AsyncMock(
            return_value=(True, "test_address", None)
        )

        # Настраиваем мок job_service для отправки задания
        tcp_server.send_new_job_tcp = AsyncMock()

        data = {
            "method": "mining.authorize",
            "id": 2,
            "params": ["test_user", ""]
        }

        await tcp_server.handle_message(data, writer, "client_123")

        # Проверяем, что майнер добавлен
        assert "client_123" in tcp_server.miners
        assert tcp_server.miners["client_123"] == "test_address"

        # Проверяем, что отправлен успешный ответ
        assert writer.write.called
        # Проверяем, что отправлено задание
        tcp_server.send_new_job_tcp.assert_called_once_with("test_address", writer)

    @pytest.mark.asyncio
    async def test_handle_message_authorize_failure(self, tcp_server, mock_reader_writer):
        """Тест неудачной авторизации"""
        reader, writer = mock_reader_writer

        tcp_server.auth_service.authorize_miner = AsyncMock(
            return_value=(False, None, "Invalid credentials")
        )

        data = {
            "method": "mining.authorize",
            "id": 2,
            "params": ["invalid_user", ""]
        }

        await tcp_server.handle_message(data, writer, "client_123")

        # Проверяем, что отправлена ошибка
        assert writer.write.called
        call_args = writer.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert "error" in data
        assert data["error"][1] == "Invalid credentials"

        # Проверяем, что майнер не добавлен
        assert "client_123" not in tcp_server.miners

    @pytest.mark.asyncio
    async def test_handle_message_submit_success(self, tcp_server, mock_reader_writer):
        """Тест успешной обработки шара"""
        reader, writer = mock_reader_writer

        # Добавляем майнера
        tcp_server.miners["client_123"] = "test_address"

        # Настраиваем мок job_service
        tcp_server.job_service.validate_and_process_share.return_value = (
            True, None, {"job_data": "test"}
        )

        # Настраиваем мок database_service
        tcp_server.database_service.save_share = AsyncMock(return_value=(True, "share_123"))

        data = {
            "method": "mining.submit",
            "id": 3,
            "params": ["worker1", "job_123", "extra2", "ntime", "nonce"]
        }

        await tcp_server.handle_message(data, writer, "client_123")

        # Проверяем, что шар валидирован и сохранен
        tcp_server.job_service.validate_and_process_share.assert_called_once()
        tcp_server.database_service.save_share.assert_called_once()

        # Проверяем успешный ответ
        assert writer.write.called

    @pytest.mark.asyncio
    async def test_handle_message_submit_unauthorized(self, tcp_server, mock_reader_writer):
        """Тест отправки шара без авторизации"""
        reader, writer = mock_reader_writer

        data = {
            "method": "mining.submit",
            "id": 3,
            "params": ["worker1", "job_123", "extra2", "ntime", "nonce"]
        }

        await tcp_server.handle_message(data, writer, "client_123")

        # Проверяем, что отправлена ошибка авторизации
        assert writer.write.called
        call_args = writer.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert "error" in data
        assert "Not authorized" in data["error"][1]

    @pytest.mark.asyncio
    async def test_handle_message_unknown_method(self, tcp_server, mock_reader_writer):
        """Тест неизвестного метода"""
        reader, writer = mock_reader_writer

        data = {
            "method": "unknown.method",
            "id": 4,
            "params": []
        }

        await tcp_server.handle_message(data, writer, "client_123")

        # Проверяем ошибку
        assert writer.write.called
        call_args = writer.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert "error" in data
        assert "Unknown method" in data["error"][1]

    @pytest.mark.asyncio
    async def test_handle_client_connection_limit(self, tcp_server, mock_reader_writer):
        """Тест превышения лимита подключений"""
        reader, writer = mock_reader_writer

        # Мокаем get_extra_info чтобы возвращал кортеж
        writer.get_extra_info.return_value = ("192.168.1.1", 12345)

        # Заполняем connections до максимума
        for i in range(tcp_server.max_connections):
            tcp_server.connections[f"client_{i}"] = Mock()

        # Добавляем атрибут _ip_connections если его нет
        if not hasattr(tcp_server, '_ip_connections'):
            tcp_server._ip_connections = {}

        await tcp_server.handle_client(reader, writer)

        # Проверяем, что соединение закрыто
        writer.close.assert_called_once()
        writer.wait_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_new_job(self, tcp_server):
        """Тест рассылки нового задания"""
        # Добавляем несколько подключений
        writer1 = AsyncMock()
        writer2 = AsyncMock()

        tcp_server.connections = {
            "client1": writer1,
            "client2": writer2
        }
        tcp_server.miners = {
            "client1": "addr1",
            "client2": "addr2"
        }

        # Настраиваем мок job_service
        tcp_server.job_service.create_job_id = Mock(side_effect=["job1", "job2"])
        tcp_server.job_service.add_job = Mock()

        job_data = {
            "method": "mining.notify",
            "params": ["old_job_id", "prevhash", "coinbase", [], "version", "bits", "ntime", True]
        }

        await tcp_server.broadcast_new_job(job_data)

        # Проверяем, что задание отправлено обоим клиентам
        assert writer1.write.called
        assert writer2.write.called
        assert tcp_server.job_service.add_job.call_count == 2

    @pytest.mark.asyncio
    async def test_broadcast_difficulty(self, tcp_server):
        """Тест рассылки обновления сложности"""
        writer1 = AsyncMock()
        writer2 = AsyncMock()

        tcp_server.connections = {
            "client1": writer1,
            "client2": writer2
        }
        tcp_server.miners = {
            "client1": "addr1",
            "client2": "addr2"
        }

        await tcp_server.broadcast_difficulty(10.0)

        # Проверяем отправку сообщения о сложности
        assert writer1.write.called
        assert writer2.write.called

        # Проверяем содержимое сообщения
        call_args = writer1.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert data["method"] == "mining.set_difficulty"
        assert data["params"] == [10.0]

    @pytest.mark.asyncio
    async def test_update_miner_difficulty(self, tcp_server):
        """Тест обновления сложности для конкретного майнера"""
        writer = AsyncMock()

        tcp_server.connections = {"client_123": writer}
        tcp_server.miners = {"client_123": "test_address"}

        await tcp_server.update_miner_difficulty("test_address", 5.0)

        assert writer.write.called
        call_args = writer.write.call_args[0][0]
        data = json.loads(call_args.decode().strip())
        assert data["params"] == [5.0]

    @pytest.mark.asyncio
    async def test_update_miner_difficulty_not_found(self, tcp_server):
        """Тест обновления сложности для несуществующего майнера"""
        tcp_server.connections = {}
        tcp_server.miners = {}

        await tcp_server.update_miner_difficulty("nonexistent", 5.0)

        # Ничего не должно произойти

    def test_get_stats(self, tcp_server):
        """Тест получения статистики сервера"""
        tcp_server.connections = {"c1": Mock(), "c2": Mock()}
        tcp_server.miners = {"c1": "addr1", "c2": "addr2"}
        tcp_server.start_time = datetime.now(UTC)

        stats = tcp_server.get_stats()

        assert stats["active_connections"] == 2
        assert stats["active_miners"] == 2
        assert stats["protocol"] == "stratum+tcp"
        assert "uptime_seconds" in stats

    @pytest.mark.asyncio
    async def test_stop_server(self, tcp_server):
        """Тест остановки сервера"""
        mock_server = AsyncMock()
        mock_server.close = Mock()
        mock_server.wait_closed = AsyncMock()

        tcp_server.server = mock_server
        tcp_server.connections = {"c1": Mock()}

        await tcp_server.stop()

        mock_server.close.assert_called_once()
        mock_server.wait_closed.assert_called_once()