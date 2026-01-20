import hashlib
from typing import Optional, Tuple
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)


class ShareValidator:
    """Валидатор шаров (shares) для Stratum протокола"""

    def __init__(self, target_difficulty: float = 1.0, extra_nonce2_size: int = 4):
        self.target_difficulty = target_difficulty
        self.extra_nonce2_size = extra_nonce2_size  # Размер extra_nonce2 в байтах
        self.jobs_cache = {}  # Кэш заданий: job_id -> job_data

    def add_job(self, job_id: str, job_data: dict):
        """Добавить задание в кэш для валидации"""
        self.jobs_cache[job_id] = job_data
        logger.debug(f"Добавлено задание {job_id} в кэш")

    def remove_job(self, job_id: str):
        """Удалить задание из кэша"""
        removed = self.jobs_cache.pop(job_id, None)
        if removed:
            logger.debug(f"Удалено задание {job_id} из кэша")
        else:
            logger.warning(f"Задание {job_id} не найдено в кэше")

    def validate_share(self,
                       job_id: str,
                       extra_nonce2: str,
                       ntime: str,
                       nonce: str,
                       miner_address: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка валидности шара

        Args:
            job_id: ID задания
            extra_nonce2: Extra nonce 2 (hex)
            ntime: Время (hex)
            nonce: Nonce (hex)
            miner_address: Адрес майнера

        Returns:
            Tuple[bool, Optional[str]]: (валиден ли шар, сообщение об ошибке)
        """
        # Проверяем существование задания
        if job_id not in self.jobs_cache:
            return False, f"Задание {job_id} не найдено"


        job = self.jobs_cache[job_id]

        try:
            # extra_nonce2: длина зависит от extra_nonce2_size (по умолчанию 4 байта = 8 hex символов)
            expected_extra_nonce2_len = self.extra_nonce2_size * 2  # байты -> hex символы

            # 1. ************** Проверяем формат данных
            if not self._validate_hex_format(extra_nonce2, expected_extra_nonce2_len):  # <-- Используем переменную!
                return False, f"Неверный формат extra_nonce2: {extra_nonce2} (ожидается {expected_extra_nonce2_len} hex символов)"

            if not self._validate_hex_format(ntime, 8):
                return False, f"Неверный формат ntime: {ntime}"

            if not self._validate_hex_format(nonce, 8):
                return False, f"Неверный формат nonce: {nonce}"

            # 2. **************** Проверяем ntime (время должно быть в пределах ±2 часов от текущего)
            if not self._validate_ntime(ntime):
                return False, f"Некорректное время ntime: {ntime}"

            # 3. Проверяем уникальность nonce
            if not self._check_nonce_uniqueness(job_id, nonce):
                return False, f"Nonce {nonce} уже использовался для задания {job_id}"

            # 4. Рассчитываем хэш заголовка
            hash_result = self.calculate_hash(job, extra_nonce2, ntime, nonce)

            if hash_result == "0" * 64:
                return False, "Ошибка расчета хэша"

            # 5. Проверяем сложность
            is_difficulty_ok = self.check_difficulty(hash_result, self.target_difficulty)

            if not is_difficulty_ok:
                # Хэш не соответствует целевой сложности
                logger.debug(f"Хэш не соответствует сложности: {hash_result}")
                return False, "Hash doesn't meet target difficulty"

            # 6. Дополнительные проверки
            # Проверяем что хэш меньше чем текущая цель сети
            # TODO: Добавить проверку сетевой сложности

            logger.info(f"Валидный шар от {miner_address}: job={job_id}, hash={hash_result[:16]}...")
            return True, None

        except Exception as e:
            logger.error(f"Ошибка при валидации шара: {e}")
            return False, f"Ошибка валидации: {str(e)}"

    @staticmethod
    def _validate_hex_format(hex_str: str, expected_length: int) -> bool:
        """Проверка формата hex строки"""
        if not hex_str:
            return False

        # Проверяем длину
        if len(hex_str) != expected_length:
            logger.debug(f"Неверная длина hex строки: {hex_str} (длина {len(hex_str)}, ожидается {expected_length})")
            return False

        # Проверяем что это hex
        try:
            int(hex_str, 16)
            return True
        except ValueError:
            logger.debug(f"Неверный hex формат: {hex_str}")
            return False

    @staticmethod
    def _validate_ntime(ntime_hex: str) -> bool:
        """Проверка корректности времени"""
        try:
            # Преобразуем hex в целое
            ntime_int = int(ntime_hex, 16)

            # Преобразуем в Unix timestamp
            # В Stratum ntime - это время в формате UNIX timestamp
            current_time = int(datetime.now(UTC).timestamp())

            # Допустимый диапазон: ±2 часа от текущего времени
            time_diff = abs(ntime_int - current_time)
            max_allowed_diff = 2 * 60 * 60  # 2 часа в секундах

            if time_diff > max_allowed_diff:
                logger.debug(f"ntime вне диапазона: {ntime_int} (текущее: {current_time}, разница: {time_diff} сек)")
                return False

            return True

        except Exception as e:
            logger.debug(f"Ошибка парсинга ntime: {ntime_hex}, ошибка: {e}")
            return False

    def _check_nonce_uniqueness(self, job_id: str, nonce: str) -> bool:
        """Проверка уникальности nonce для задания"""
        # Используем set для хранения использованных nonce
        if not hasattr(self, '_used_nonces'):
            self._used_nonces = {}  # job_id -> set of nonces

        if job_id not in self._used_nonces:
            self._used_nonces[job_id] = set()

        # Проверяем уникальность
        if nonce in self._used_nonces[job_id]:
            return False

        # Добавляем в использованные
        self._used_nonces[job_id].add(nonce)

        # Очищаем старые записи
        self._cleanup_old_nonces()

        return True

    def _cleanup_old_nonces(self, max_per_job: int = 1000):
        """Очистка старых nonce, если их слишком много"""
        for job_id in list(self._used_nonces.keys()):
            if len(self._used_nonces[job_id]) > max_per_job:
                # Оставляем только последние max_per_job nonce
                all_nonces = list(self._used_nonces[job_id])
                self._used_nonces[job_id] = set(all_nonces[-max_per_job:])

    @staticmethod
    def calculate_hash(job_data: dict, extra_nonce2: str, ntime: str, nonce: str) -> str:
        """Расчет хэша заголовка блока"""
        try:
            # Параметры из задания Stratum
            params = job_data["params"]
            prevhash = params[1]  # предыдущий хэш блока
            coinb1 = params[2]  # первая часть coinbase
            coinb2 = params[3]  # вторая часть coinbase
            # merkle_branch = params[4]  # ветки Merkle дерева (не используется)
            version = params[5]  # версия блока
            nbits = params[6]  # сложность в compact формате
            # ntime_param = params[7]  # время из задания (не используется, используем ntime из параметра)

            # Используем extra_nonce1 из Stratum ответа на subscribe
            extra_nonce1 = "ae6812eb4cd7735a302a8a9dd95cf71f"

            # Собираем coinbase транзакцию
            coinbase = coinb1 + extra_nonce1 + extra_nonce2 + coinb2

            # Хэшируем coinbase транзакцию (двойной SHA256)
            coinbase_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase)).digest()).digest()

            # Для упрощения используем только coinbase хэш как Merkle root
            # TODO В реальности нужно вычислить полное Merkle дерево
            merkle_root = coinbase_hash.hex()

            # Собираем заголовок блока
            header = (
                bytes.fromhex(version)[::-1] +  # version (little-endian)
                bytes.fromhex(prevhash)[::-1] +  # previous block hash
                bytes.fromhex(merkle_root)[::-1] +  # merkle root
                bytes.fromhex(ntime)[::-1] +  # timestamp
                bytes.fromhex(nbits)[::-1] +  # bits
                bytes.fromhex(nonce)[::-1]  # nonce
            )

            # Двойной SHA256
            first_hash = hashlib.sha256(header).digest()
            block_hash = hashlib.sha256(first_hash).digest()

            # Переворачиваем (little-endian -> big-endian для отображения)
            return block_hash[::-1].hex()

        except Exception as e:
            logger.error(f"Ошибка расчета хэша: {e}")
            return "0" * 64

    @staticmethod
    def check_difficulty(hash_result: str, target_difficulty: float) -> bool:
        """Проверка соответствия сложности"""
        try:
            # Преобразуем хэш в число
            hash_int = int(hash_result, 16)

            # Целевое значение (для сложности 1.0)
            target_for_difficulty_1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

            # Вычисляем target для текущей сложности
            target = target_for_difficulty_1 // int(target_difficulty)

            # Проверяем: хэш должен быть меньше или равен target
            return hash_int <= target

        except Exception as e:
            logger.error(f"Ошибка проверки сложности: {e}")
            return False

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий"""
        current_time = datetime.now(UTC)
        jobs_to_remove = []

        for job_id, job_data in self.jobs_cache.items():
            # Извлекаем timestamp из job_id
            try:
                if job_id.startswith("job_"):
                    parts = job_id.split('_')
                    if len(parts) >= 2:
                        timestamp_str = parts[1]
                        job_time = datetime.fromtimestamp(float(timestamp_str), UTC)

                        age = (current_time - job_time).total_seconds()
                        if age > max_age_seconds:
                            jobs_to_remove.append(job_id)
            except (IndexError, ValueError, AttributeError):
                # Если не можем распарсить, удаляем
                jobs_to_remove.append(job_id)

        # Удаляем старые задания
        for job_id in jobs_to_remove:
            self.remove_job(job_id)

        if jobs_to_remove:
            logger.info(f"Валидатор: очищено {len(jobs_to_remove)} старых заданий")
