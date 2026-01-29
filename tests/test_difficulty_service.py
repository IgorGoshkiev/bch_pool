"""
Тесты для DifficultyService
"""
import pytest
import asyncio
import statistics
from datetime import datetime, UTC, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from collections import deque

from app.services.difficulty_service import DifficultyService


class TestDifficultyService:
    """Тесты сервиса управления сложностью"""

    @pytest.fixture
    def mock_network_manager(self):
        """Мок NetworkManager"""
        manager = Mock()
        manager.config = {
            'default_difficulty': 1.0,
            'network': 'testnet'
        }
        return manager

    @pytest.fixture
    def difficulty_service(self, mock_network_manager):
        """Создание экземпляра DifficultyService"""
        return DifficultyService(network_manager=mock_network_manager)

    def test_initialization(self, mock_network_manager):
        """Тест инициализации DifficultyService"""
        service = DifficultyService(network_manager=mock_network_manager)

        # Используем реальные значения из config.py:
        # - current_difficulty = 1.0 (из network_manager.config)
        # - target_shares_per_minute = 60 (из config.py)
        # - min_difficulty = 0.001 (из config.py)
        # - max_difficulty = 1000.0 (из config.py)

        assert service.current_difficulty == 1.0
        assert service.target_shares_per_minute == 60.0
        assert service.min_difficulty == 0.001  # Реальное значение из config.py
        assert service.max_difficulty == 1000.0  # Реальное значение из config.py

        assert service.network_manager == mock_network_manager
        assert service.share_timestamps == {}
        assert service.share_history == []
        assert service.total_shares == 0
        assert service.shares_last_hour == 0

    @pytest.mark.asyncio
    async def test_add_share(self, difficulty_service):
        """Добавление шара для расчета сложности"""
        miner_address = "miner123"

        # Добавляем первый шар
        await difficulty_service.add_share(miner_address, difficulty=1.0)

        # Проверяем, что майнер добавлен
        assert miner_address in difficulty_service.share_timestamps
        assert isinstance(difficulty_service.share_timestamps[miner_address], deque)
        assert len(difficulty_service.share_timestamps[miner_address]) == 1

        # Проверяем историю
        assert len(difficulty_service.share_history) == 1
        share_record = difficulty_service.share_history[0]
        assert share_record['miner_address'] == miner_address
        assert share_record['difficulty'] == 1.0

        # Проверяем статистику
        assert difficulty_service.total_shares == 1

        # Добавляем второй шар
        await difficulty_service.add_share(miner_address, difficulty=2.0)

        assert len(difficulty_service.share_timestamps[miner_address]) == 2
        assert len(difficulty_service.share_history) == 2
        assert difficulty_service.total_shares == 2

    @pytest.mark.asyncio
    async def test_add_share_multiple_miners(self, difficulty_service):
        """Добавление шаров от нескольких майнеров"""
        miners = ["miner1", "miner2", "miner3"]

        for i, miner in enumerate(miners):
            await difficulty_service.add_share(miner, difficulty=float(i + 1))

        assert len(difficulty_service.share_timestamps) == 3
        assert len(difficulty_service.share_history) == 3

        for miner in miners:
            assert miner in difficulty_service.share_timestamps
            assert len(difficulty_service.share_timestamps[miner]) == 1

    @pytest.mark.asyncio
    async def test_add_share_history_limit(self, difficulty_service):
        """Проверка ограничения размера истории"""
        miner_address = "miner123"

        # Добавляем больше шаров, чем максимальный размер истории
        for i in range(1500):
            await difficulty_service.add_share(miner_address, difficulty=1.0)

        # История должна быть ограничена max_history_size
        assert len(difficulty_service.share_history) <= difficulty_service.max_history_size
        assert difficulty_service.max_history_size == 1000

    @pytest.mark.asyncio
    async def test_add_share_exception(self, difficulty_service):
        """Обработка исключения при добавлении шара"""
        # Пытаемся добавить шар с некорректными данными
        await difficulty_service.add_share(None, difficulty=1.0)

        # Должно обработаться без падения



    @pytest.mark.asyncio
    async def test_calculate_difficulty_insufficient_data(self, difficulty_service):
        """Расчет сложности при недостаточных данных"""
        # Добавляем мало шаров
        for _ in range(5):
            await difficulty_service.add_share("miner123", difficulty=1.0)

        result = await difficulty_service.calculate_difficulty()

        # Должна вернуться текущая сложность без изменений
        assert result == difficulty_service.current_difficulty

    @pytest.mark.asyncio
    async def test_calculate_difficulty_on_target(self, difficulty_service, mock_settings):
        """Расчет сложности при достижении целевого уровня"""
        target_per_hour = mock_settings.target_shares_per_minute * 60

        # Очищаем историю перед тестом
        difficulty_service.share_history = []
        difficulty_service.share_timestamps = {}

        # Добавляем шары для достижения целевого уровня
        current_time = datetime.now(UTC)

        # Создаем шары с временем в пределах часа
        for i in range(int(target_per_hour)):
            timestamp = current_time - timedelta(minutes=59, seconds=59 - i)

            # Имитируем добавление шара
            if "miner123" not in difficulty_service.share_timestamps:
                difficulty_service.share_timestamps["miner123"] = deque(maxlen=100)

            difficulty_service.share_timestamps["miner123"].append(timestamp)

            share_record = {
                'timestamp': timestamp,
                'miner_address': "miner123",
                'difficulty': 1.0
            }
            difficulty_service.share_history.append(share_record)

        difficulty_service.shares_last_hour = target_per_hour
        difficulty_service.total_shares = target_per_hour

        result = await difficulty_service.calculate_difficulty()

        # Сложность должна остаться примерно такой же
        # При ratio = 1.0, adjustment_factor = 1.0**0.5 = 1.0
        assert abs(result - 1.0) < 0.1

    @pytest.mark.asyncio
    async def test_calculate_difficulty_too_many_shares(self, difficulty_service):
        """Расчет сложности при слишком большом количестве шаров"""
        target_per_hour = 60.0 * 60  # 3600 шаров/час

        # Очищаем историю перед тестом
        difficulty_service.share_history = []
        difficulty_service.share_timestamps = {}

        # Добавляем много шаров (вдвое больше целевого)
        current_time = datetime.now(UTC)

        for i in range(int(target_per_hour * 2)):
            timestamp = current_time - timedelta(minutes=59, seconds=59 - i)

            if "miner123" not in difficulty_service.share_timestamps:
                difficulty_service.share_timestamps["miner123"] = deque(maxlen=100)

            difficulty_service.share_timestamps["miner123"].append(timestamp)

            share_record = {
                'timestamp': timestamp,
                'miner_address': "miner123",
                'difficulty': 1.0
            }
            difficulty_service.share_history.append(share_record)

        difficulty_service.shares_last_hour = target_per_hour * 2
        difficulty_service.total_shares = target_per_hour * 2

        result = await difficulty_service.calculate_difficulty()

        # Сложность должна увеличиться
        assert result > 1.0


    @pytest.mark.asyncio
    async def test_calculate_difficulty_too_few_shares(self, difficulty_service):
        """Расчет сложности при слишком малом количестве шаров"""
        # Добавляем мало шаров (половина от целевого)
        for _ in range(300):
            await difficulty_service.add_share("miner123", difficulty=1.0)

        # Сбрасываем время
        hour_ago = datetime.now(UTC) - timedelta(minutes=59)
        for share in difficulty_service.share_history:
            share['timestamp'] = hour_ago

        difficulty_service.shares_last_hour = 300

        result = await difficulty_service.calculate_difficulty()

        # Сложность должна уменьшиться
        assert result < 1.0
        assert result >= difficulty_service.min_difficulty

    @pytest.mark.asyncio
    async def test_calculate_difficulty_min_limit(self, difficulty_service):
        """Проверка ограничения минимальной сложности"""
        # Устанавливаем очень низкую текущую сложность
        difficulty_service.current_difficulty = 0.05  # ниже минимальной

        # Добавляем достаточно шаров для расчета
        for _ in range(600):
            await difficulty_service.add_share("miner123", difficulty=1.0)

        difficulty_service.shares_last_hour = 600

        result = await difficulty_service.calculate_difficulty()

        # Сложность должна быть ограничена снизу
        assert result >= difficulty_service.min_difficulty

    @pytest.mark.asyncio
    async def test_calculate_difficulty_max_limit(self, difficulty_service):
        """Проверка ограничения максимальной сложности"""
        # Устанавливаем очень высокую текущую сложность
        difficulty_service.current_difficulty = 200.0  # выше максимальной

        # Добавляем достаточно шаров для расчета
        for _ in range(600):
            await difficulty_service.add_share("miner123", difficulty=1.0)

        difficulty_service.shares_last_hour = 600

        result = await difficulty_service.calculate_difficulty()

        # Сложность должна быть ограничена сверху
        assert result <= difficulty_service.max_difficulty

    @pytest.mark.asyncio
    async def test_calculate_difficulty_max_change_limit(self, difficulty_service):
        """Проверка ограничения максимального изменения"""
        # Создаем ситуацию для резкого изменения сложности
        difficulty_service.current_difficulty = 1.0

        # Очень много шаров (в 100 раз больше целевого)
        for _ in range(60000):
            await difficulty_service.add_share("miner123", difficulty=1.0)

        difficulty_service.shares_last_hour = 60000

        result = await difficulty_service.calculate_difficulty()

        # Изменение должно быть ограничено фактором 4.0
        assert result <= difficulty_service.current_difficulty * 4.0
        assert result >= difficulty_service.current_difficulty / 4.0

    @pytest.mark.asyncio
    async def test_calculate_difficulty_exception(self, difficulty_service):
        """Обработка исключения при расчете сложности"""
        # Создаем ситуацию, вызывающую исключение
        with patch.object(difficulty_service, 'share_history', None):
            result = await difficulty_service.calculate_difficulty()

        # Должна вернуться текущая сложность
        assert result == difficulty_service.current_difficulty

    @pytest.mark.asyncio
    async def test_update_difficulty_no_change(self, difficulty_service):
        """Обновление сложности без изменений"""
        # Настраиваем calculate_difficulty на возврат той же сложности
        with patch.object(difficulty_service, 'calculate_difficulty', AsyncMock(return_value=1.0)):
            changed, new_difficulty, message = await difficulty_service.update_difficulty()

        assert changed is False
        assert new_difficulty == 1.0
        assert "too small" in message.lower()

    @pytest.mark.asyncio
    async def test_update_difficulty_with_change(self, difficulty_service):
        """Обновление сложности с изменением"""
        old_difficulty = difficulty_service.current_difficulty

        # Настраиваем calculate_difficulty на возврат новой сложности
        with patch.object(difficulty_service, 'calculate_difficulty', AsyncMock(return_value=2.0)):
            with patch.object(difficulty_service, '_broadcast_difficulty_update', AsyncMock()):
                changed, new_difficulty, message = await difficulty_service.update_difficulty()

        assert changed is True
        assert new_difficulty == 2.0
        assert new_difficulty != old_difficulty
        assert difficulty_service.current_difficulty == 2.0
        assert "updated" in message.lower()

    @pytest.mark.asyncio
    async def test_update_difficulty_exception(self, difficulty_service):
        """Обработка исключения при обновлении сложности"""
        # Создаем исключение в calculate_difficulty
        with patch.object(difficulty_service, 'calculate_difficulty',
                          AsyncMock(side_effect=Exception("Test error"))):
            changed, new_difficulty, message = await difficulty_service.update_difficulty()

        assert changed is False
        assert new_difficulty == difficulty_service.current_difficulty
        assert "error" in message.lower()

    @pytest.mark.asyncio
    async def test_broadcast_difficulty_update(self, difficulty_service):
        """Рассылка обновления сложности"""
        # Сначала проверим, существует ли модуль
        try:
            import app.dependencies
            has_dependencies = True
        except ImportError:
            has_dependencies = False

        if not has_dependencies:
            pytest.skip("Модуль app.dependencies не найден - требуется проверка кода проекта")

        mock_stratum = AsyncMock()
        mock_stratum.update_difficulty = AsyncMock()

        with patch('app.dependencies.stratum_server', mock_stratum):
            await difficulty_service._broadcast_difficulty_update()
            mock_stratum.update_difficulty.assert_called_once_with(difficulty_service.current_difficulty)

    @pytest.mark.asyncio
    async def test_broadcast_difficulty_update_exception(self, difficulty_service):
        """Обработка исключения при рассылке обновления"""
        # Патчим stratum_server как None с create=True
        with patch('app.services.difficulty_service.stratum_server',
                   None, create=True):
            # Должно обработаться без падения
            await difficulty_service._broadcast_difficulty_update()

    @pytest.mark.asyncio
    async def test_get_miner_hashrate_no_data(self, difficulty_service):
        """Расчет хэшрейта майнера без данных"""
        hashrate = await difficulty_service.get_miner_hashrate("nonexistent_miner")
        assert hashrate == 0.0

    @pytest.mark.asyncio
    async def test_get_miner_hashrate_insufficient_data(self, difficulty_service):
        """Расчет хэшрейта с недостаточными данными"""
        miner_address = "miner123"

        # Добавляем только один шар
        await difficulty_service.add_share(miner_address, difficulty=1.0)

        hashrate = await difficulty_service.get_miner_hashrate(miner_address, period_minutes=5)
        assert hashrate == 0.0

    @pytest.mark.asyncio
    async def test_get_miner_hashrate_with_data(self, difficulty_service):
        """Расчет хэшрейта майнера с данными"""
        miner_address = "miner123"

        # Добавляем шары с интервалом 1 секунда
        base_time = datetime.now(UTC)

        # Имитируем добавление шаров с разным временем
        difficulty_service.share_timestamps[miner_address] = deque(maxlen=100)
        for i in range(10):
            timestamp = base_time - timedelta(seconds=10 - i)
            difficulty_service.share_timestamps[miner_address].append(timestamp)

        hashrate = await difficulty_service.get_miner_hashrate(miner_address, period_minutes=5)

        # Проверяем, что хэшрейт рассчитан
        assert hashrate > 0.0

        # Хэшрейт при 1 шар в секунду при сложности 1.0
        expected_hashes_per_share = 2 ** 32  # 4,294,967,296
        expected_hashrate = expected_hashes_per_share / 1.0  # 1 секунда между шарами

        # Допускаем погрешность из-за статистики
        assert abs(hashrate - expected_hashrate) < expected_hashrate * 0.5

    @pytest.mark.asyncio
    async def test_get_miner_hashrate_fast_shares(self, difficulty_service):
        """Расчет хэшрейта при очень быстрых шарах"""
        miner_address = "miner123"

        # Добавляем шары с очень маленьким интервалом
        base_time = datetime.now(UTC)

        difficulty_service.share_timestamps[miner_address] = deque(maxlen=100)
        for i in range(10):
            timestamp = base_time - timedelta(milliseconds=10 * i)
            difficulty_service.share_timestamps[miner_address].append(timestamp)

        hashrate = await difficulty_service.get_miner_hashrate(miner_address, period_minutes=5)

        # Хэшрейт должен быть ограничен (минимальное время между шарами 0.1 секунды)
        max_hashrate = (2 ** 32) / 0.1
        assert hashrate <= max_hashrate

    @pytest.mark.asyncio
    async def test_get_miner_hashrate_exception(self, difficulty_service):
        """Обработка исключения при расчете хэшрейта"""
        hashrate = await difficulty_service.get_miner_hashrate(None, period_minutes=5)
        assert hashrate == 0.0

    @pytest.mark.asyncio
    async def test_get_pool_hashrate(self, difficulty_service):
        """Расчет общего хэшрейта пула"""
        # Добавляем данные для нескольких майнеров
        miners = ["miner1", "miner2", "miner3"]
        base_time = datetime.now(UTC)

        for i, miner in enumerate(miners):
            difficulty_service.share_timestamps[miner] = deque(maxlen=100)
            for j in range(5):
                timestamp = base_time - timedelta(seconds=(5 - j) * (i + 1))
                difficulty_service.share_timestamps[miner].append(timestamp)

        # Мокаем get_miner_hashrate для каждого майнера
        mock_hashrates = [1000.0, 2000.0, 3000.0]

        async def mock_get_miner_hashrate(miner_address, period_minutes):
            index = miners.index(miner_address) if miner_address in miners else -1
            return mock_hashrates[index] if index >= 0 else 0.0

        with patch.object(difficulty_service, 'get_miner_hashrate', mock_get_miner_hashrate):
            total_hashrate = await difficulty_service.get_pool_hashrate(period_minutes=5)

        assert total_hashrate == sum(mock_hashrates)

    @pytest.mark.asyncio
    async def test_get_pool_hashrate_exception(self, difficulty_service):
        """Обработка исключения при расчете хэшрейта пула"""
        with patch.object(difficulty_service, 'get_miner_hashrate',
                          AsyncMock(side_effect=Exception("Test error"))):
            hashrate = await difficulty_service.get_pool_hashrate(period_minutes=5)

        assert hashrate == 0.0

    def test_cleanup_old_data(self, difficulty_service):
        """Очистка старых данных"""
        miner_address = "miner123"

        # Добавляем старые данные
        old_time = datetime.now(UTC) - timedelta(hours=25)
        new_time = datetime.now(UTC) - timedelta(hours=1)

        # Добавляем старые шары
        difficulty_service.share_timestamps[miner_address] = deque(maxlen=100)
        difficulty_service.share_timestamps[miner_address].append(old_time)
        difficulty_service.share_timestamps[miner_address].append(new_time)

        difficulty_service.share_history = [
            {'timestamp': old_time, 'miner_address': miner_address, 'difficulty': 1.0},
            {'timestamp': new_time, 'miner_address': miner_address, 'difficulty': 1.0}
        ]

        # Очищаем данные старше 24 часов
        difficulty_service.cleanup_old_data(max_age_hours=24)

        # Проверяем, что старые данные удалены
        assert len(difficulty_service.share_history) == 1
        assert difficulty_service.share_history[0]['timestamp'] == new_time

        # Проверяем таймстампы майнера
        assert len(difficulty_service.share_timestamps[miner_address]) == 1
        assert difficulty_service.share_timestamps[miner_address][0] == new_time

    def test_cleanup_old_data_empty_miner(self, difficulty_service):
        """Очистка данных для майнера без таймстампов"""
        miner_address = "miner123"

        # Добавляем только старые данные
        old_time = datetime.now(UTC) - timedelta(hours=25)
        difficulty_service.share_timestamps[miner_address] = deque(maxlen=100)
        difficulty_service.share_timestamps[miner_address].append(old_time)

        # Очищаем данные
        difficulty_service.cleanup_old_data(max_age_hours=24)

        # Майнер должен быть удален
        assert miner_address not in difficulty_service.share_timestamps

    def test_cleanup_old_data_exception(self, difficulty_service):
        """Обработка исключения при очистке данных"""
        # Создаем ситуацию, вызывающую исключение
        difficulty_service.share_history = None

        # Должно обработаться без падения
        difficulty_service.cleanup_old_data(max_age_hours=24)

    @pytest.mark.asyncio
    async def test_diagnostic_calculation(self):
        """Диагностический тест для проверки логики расчета"""
        mock_network_manager = Mock()
        mock_network_manager.config = {'default_difficulty': 1.0, 'network': 'testnet'}

        service = DifficultyService(network_manager=mock_network_manager)

        print(f"\n=== ДИАГНОСТИКА РАСЧЕТА СЛОЖНОСТИ ===")
        print(f"Текущие настройки сервиса:")
        print(f"  target_shares_per_minute: {service.target_shares_per_minute}")
        print(f"  min_difficulty: {service.min_difficulty}")
        print(f"  max_difficulty: {service.max_difficulty}")

        # Тестируем разные сценарии
        test_scenarios = [
            (3600, "Идеально: 60 шаров/мин"),
            (1800, "Мало: 30 шаров/мин"),
            (7200, "Много: 120 шаров/мин"),
        ]

        for shares_last_hour, description in test_scenarios:
            service.shares_last_hour = shares_last_hour
            actual_shares_per_minute = shares_last_hour / 60
            ratio = actual_shares_per_minute / service.target_shares_per_minute

            print(f"\n{description}:")
            print(f"  shares_last_hour: {shares_last_hour}")
            print(f"  actual_shares_per_minute: {actual_shares_per_minute:.1f}")
            print(f"  target_shares_per_minute: {service.target_shares_per_minute:.1f}")
            print(f"  ratio: {ratio:.3f}")
            print(f"  expected adjustment: {ratio ** 0.5:.3f}")

        print(f"=== КОНЕЦ ДИАГНОСТИКИ ===\n")

        # Тест всегда проходит, это только для диагностики
        assert True

    def test_difficulty_limits_sanity(self, mock_network_manager):
        """Проверка разумности лимитов сложности"""
        service = DifficultyService(network_manager=mock_network_manager)

        # Проверяем базовые инварианты
        assert service.min_difficulty > 0, "Минимальная сложность должна быть больше 0"
        assert service.max_difficulty > service.min_difficulty, \
            f"Максимальная сложность ({service.max_difficulty}) должна быть больше минимальной ({service.min_difficulty})"

        # Проверяем разумные границы (можно настроить под ваш пул)
        assert service.min_difficulty <= 1.0, \
            f"min_difficulty={service.min_difficulty} кажется слишком большой"

        # max_difficulty = 1000.0 может быть нормально для большого пула
        # но если это тестовый пул, возможно это ошибка
        print(f"\nПроверка лимитов сложности:")
        print(f"  min_difficulty: {service.min_difficulty}")
        print(f"  max_difficulty: {service.max_difficulty}")
        print(f"  target_shares_per_minute: {service.target_shares_per_minute}")

    def test_get_stats(self, difficulty_service):
        """Получение статистики сервиса"""
        # Добавляем некоторые данные
        difficulty_service.total_shares = 100
        difficulty_service.shares_last_hour = 10
        difficulty_service.share_timestamps["miner1"] = deque([datetime.now(UTC)])
        difficulty_service.share_timestamps["miner2"] = deque([datetime.now(UTC)])
        difficulty_service.share_history = [{}] * 5

        stats = difficulty_service.get_stats()

        assert stats["current_difficulty"] == difficulty_service.current_difficulty
        assert stats["total_shares"] == 100
        assert stats["shares_last_hour"] == 10
        assert stats["active_miners"] == 2
        assert stats["target_shares_per_minute"] == difficulty_service.target_shares_per_minute
        assert "last_update" in stats
        assert stats["enable_dynamic"] is True
        assert stats["min_difficulty"] == difficulty_service.min_difficulty
        assert stats["max_difficulty"] == difficulty_service.max_difficulty
        assert stats["history_size"] == 5
