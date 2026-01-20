import hashlib
from typing import Optional, Tuple, Dict
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger

from app.utils.protocol_helpers import (
    STRATUM_EXTRA_NONCE1,
    EXTRA_NONCE2_SIZE,
    BLOCK_HEADER_SIZE
)

# ========== КОНСТАНТЫ ВАЛИДАЦИИ ==========
TARGET_FOR_DIFFICULTY_1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

logger = StructuredLogger("validator")


class ShareValidator:
    """Валидатор шаров (shares) для Stratum протокола"""

    def __init__(self, target_difficulty: float = 1.0, extra_nonce2_size: int = EXTRA_NONCE2_SIZE):
        self.target_difficulty = target_difficulty
        self.extra_nonce2_size = extra_nonce2_size  # Размер extra_nonce2 в байтах
        self.jobs_cache: Dict[str, dict] = {}  # Кэш заданий: job_id -> job_data
        self._used_nonces: Dict[str, set] = {}  # job_id -> set of nonces
        self.validated_shares = 0
        self.invalid_shares = 0
        self.start_time = datetime.now(UTC)

        logger.info(
            "Валидатор инициализирован",
            event="validator_initialized",
            target_difficulty=target_difficulty,
            extra_nonce2_size=extra_nonce2_size,
            start_time=self.start_time.isoformat()
        )

    def add_job(self, job_id: str, job_data: dict):
        """Добавить задание в кэш для валидации"""
        self.jobs_cache[job_id] = job_data

        logger.debug(
            "Добавлено задание в кэш",
            event="job_added_to_cache",
            job_id=job_id,
            jobs_cache_size=len(self.jobs_cache),
            has_extra_nonce1='extra_nonce1' in job_data
        )

    def remove_job(self, job_id: str):
        """Удалить задание из кэша"""
        removed = self.jobs_cache.pop(job_id, None)

        if removed:
            # Также удаляем использованные nonce для этого задания
            self._used_nonces.pop(job_id, None)

            logger.debug(
                "Удалено задание из кэша",
                event="job_removed_from_cache",
                job_id=job_id,
                remaining_jobs=len(self.jobs_cache)
            )
        else:
            logger.warning(
                "Задание не найдено в кэше",
                event="job_not_in_cache",
                job_id=job_id
            )

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

        validation_start = datetime.now(UTC)

        # Проверяем существование задания
        if job_id not in self.jobs_cache:
            self.invalid_shares += 1
            logger.warning(
                "Задание не найдено при валидации шара",
                event="share_validation_failed",
                miner_address=miner_address,
                job_id=job_id,
                reason="job_not_found",
                validation_time_ms=(datetime.now(UTC) - validation_start).total_seconds() * 1000
            )
            return False, f"Задание {job_id} не найдено"


        job = self.jobs_cache[job_id]

        try:
            # extra_nonce2: длина зависит от extra_nonce2_size (по умолчанию 4 байта = 8 hex символов)
            expected_extra_nonce2_len = self.extra_nonce2_size * 2  # байты -> hex символы

            # 1. ************** Проверяем формат данных
            if not self._validate_hex_format(extra_nonce2, expected_extra_nonce2_len):  # <-- Используем переменную!

                self.invalid_shares += 1
                logger.warning(
                    "Неверный формат extra_nonce2",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="invalid_extra_nonce2_format",
                    extra_nonce2=extra_nonce2,
                    expected_length=expected_extra_nonce2_len,
                    actual_length=len(extra_nonce2)
                )
                return False, f"Неверный формат extra_nonce2: {extra_nonce2} (ожидается {expected_extra_nonce2_len} hex символов)"

            if not self._validate_hex_format(ntime, 8):
                self.invalid_shares += 1
                logger.warning(
                    "Неверный формат ntime",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="invalid_ntime_format",
                    ntime=ntime
                )
                return False, f"Неверный формат ntime: {ntime}"

            if not self._validate_hex_format(nonce, 8):
                self.invalid_shares += 1
                logger.warning(
                    "Неверный формат nonce",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="invalid_nonce_format",
                    nonce=nonce
                )
                return False, f"Неверный формат nonce: {nonce}"

            # 2. **************** Проверяем ntime (время должно быть в пределах ±2 часов от текущего)
            if not self._validate_ntime(ntime):
                self.invalid_shares += 1
                logger.warning(
                    "Некорректное время ntime",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="invalid_ntime_value",
                    ntime=ntime
                )
                return False, f"Некорректное время ntime: {ntime}"

            # 3. Проверяем уникальность nonce
            if not self._check_nonce_uniqueness(job_id, nonce):
                self.invalid_shares += 1
                logger.warning(
                    "Nonce уже использовался",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="duplicate_nonce",
                    nonce=nonce
                )
                return False, f"Nonce {nonce} уже использовался для задания {job_id}"

            # 4. Рассчитываем хэш заголовка
            hash_result = self.calculate_hash(job, extra_nonce2, ntime, nonce)

            if hash_result == "0" * 64:
                self.invalid_shares += 1
                logger.error(
                    "Ошибка расчета хэша",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="hash_calculation_error"
                )
                return False, "Ошибка расчета хэша"

            # 5. Проверяем сложность
            is_difficulty_ok = self.check_difficulty(hash_result, self.target_difficulty)

            if not is_difficulty_ok:
                # Хэш не соответствует целевой сложности
                self.invalid_shares += 1
                logger.debug(
                    "Хэш не соответствует сложности",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="difficulty_not_met",
                    hash_prefix=hash_result[:16]
                )
                return False, "Hash doesn't meet target difficulty"

            # 6. Дополнительные проверки
            # Проверяем что хэш меньше чем текущая цель сети
            # TODO: Добавить проверку сетевой сложности

            # Шар валиден!
            self.validated_shares += 1
            validation_time = (datetime.now(UTC) - validation_start).total_seconds() * 1000

            logger.info(
                "Валидный шар от майнера",
                event="share_validated",
                miner_address=miner_address,
                job_id=job_id,
                hash_prefix=hash_result[:16],
                validation_time_ms=validation_time,
                total_validated=self.validated_shares,
                total_invalid=self.invalid_shares
            )
            return True, None

        except Exception as e:
            self.invalid_shares += 1
            logger.error(
                "Ошибка при валидации шара",
                event="share_validation_error",
                miner_address=miner_address,
                job_id=job_id,
                error=str(e),
                error_type=type(e).__name__,
                validation_time_ms=(datetime.now(UTC) - validation_start).total_seconds() * 1000
            )
            return False, f"Ошибка валидации: {str(e)}"

    def get_stats(self) -> Dict:
        """Получение статистики валидатора"""
        stats = {
            "jobs_in_cache": len(self.jobs_cache),
            "validated_shares": self.validated_shares,
            "invalid_shares": self.invalid_shares,
            "total_shares": self.validated_shares + self.invalid_shares,
            "success_rate": self.validated_shares / (self.validated_shares + self.invalid_shares)
            if (self.validated_shares + self.invalid_shares) > 0 else 0,
            "uptime_seconds": (datetime.now(UTC) - self.start_time).total_seconds(),
            "target_difficulty": self.target_difficulty
        }

        logger.debug(
            "Получение статистики валидатора",
            event="validator_stats_requested",
            stats=stats
        )

        return stats

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
