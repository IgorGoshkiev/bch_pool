import hashlib
from typing import Optional, Tuple, Dict
from datetime import datetime, UTC
from app.utils.config import settings

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import (
    STRATUM_EXTRA_NONCE1,
    EXTRA_NONCE2_SIZE,
)

# ========== ЕДИНЫЕ КОНСТАНТЫ ДЛЯ MAINNET ==========
# Target для difficulty 1.0 (BCH mainnet)
# Из документации: 0x0000000000000000024cb3000000000000000000000000000000000000000000
MAINNET_TARGET_DIFFICULTY_1 = 0x0000000000000000024cb3000000000000000000000000000000000000000000

# Target для testnet4
TESTNET_TARGET_DIFFICULTY_1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

logger = StructuredLogger(__name__)


class ShareValidator:
    """Валидатор шаров (shares) для Stratum протокола"""

    def __init__(self,
                 target_difficulty: float = 1.0,
                 extra_nonce2_size: int = EXTRA_NONCE2_SIZE,
                 extra_nonce1: str = STRATUM_EXTRA_NONCE1):
        self.target_difficulty = target_difficulty
        self.extra_nonce2_size = extra_nonce2_size  # Размер extra_nonce2 в байтах
        self.extra_nonce1 = extra_nonce1  # Extra nonce 1 из конфигурации
        self.jobs_cache: Dict[str, dict] = {}  # Кэш заданий: job_id -> job_data
        self._used_nonces: Dict[str, set] = {}  # job_id -> set of nonces
        self.validated_shares = 0
        self.invalid_shares = 0
        self.start_time = datetime.now(UTC)

        # Определяем target для текущей сети
        network = getattr(settings, 'bch_network', 'mainnet')
        if network in ['testnet', 'testnet4', 'regtest']:
            self.target_for_difficulty_1 = TESTNET_TARGET_DIFFICULTY_1
        else:
            self.target_for_difficulty_1 = MAINNET_TARGET_DIFFICULTY_1

        self.network_difficulty = target_difficulty
        self.last_network_update = None

        logger.info(
            "Валидатор инициализирован",
            event="validator_initialized",
            target_difficulty=target_difficulty,
            extra_nonce2_size=extra_nonce2_size,
            extra_nonce1_length=len(extra_nonce1),
            start_time=self.start_time.isoformat()
        )

    def add_job(self, job_id: str, job_data: dict):
        """Добавить задание в кэш для валидации"""
        print(f"🟢 VALIDATOR.add_job: job_id={job_id}, exists? {job_id in self.jobs_cache}", flush=True)
        print(f"🟢 VALIDATOR.add_job: cache before size={len(self.jobs_cache)}", flush=True)

        # ВСЕГДА перезаписываем
        self.jobs_cache[job_id] = job_data

        print(f"✅ VALIDATOR.add_job: cache after size={len(self.jobs_cache)}", flush=True)
        print(f"✅ VALIDATOR.add_job: now in cache? {job_id in self.jobs_cache}", flush=True)

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

        print(f"🔍 VALIDATE_SHARE: looking for {job_id}", flush=True)
        print(f"🔍 VALIDATE_SHARE: cache keys = {list(self.jobs_cache.keys())}", flush=True)
        # Проверяем существование задания
        if job_id not in self.jobs_cache:
            if job_id not in self.jobs_cache:
                print(f"🔴 VALIDATOR: job {job_id} NOT FOUND in cache!", flush=True)
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
            if not self._validate_hex_format(extra_nonce2, expected_extra_nonce2_len):
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

            # 5. Проверяем сложность (целевую сложность пула)
            is_pool_difficulty_ok = self.check_difficulty(hash_result, self.target_difficulty)

            if not is_pool_difficulty_ok:
                # Хэш не соответствует целевой сложности пула
                self.invalid_shares += 1
                logger.debug(
                    "Хэш не соответствует сложности пула",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="pool_difficulty_not_met",
                    hash_prefix=hash_result[:16],
                    pool_difficulty=self.target_difficulty
                )
                return False, "Hash doesn't meet pool target difficulty"

            # 6. Проверяем сетевую сложность
            is_network_difficulty_ok = self.check_network_difficulty(hash_result)

            if not is_network_difficulty_ok:
                # Хэш не соответствует сетевой сложности (слишком легкий для сети)
                self.invalid_shares += 1
                logger.debug(
                    "Хэш не соответствует сетевой сложности",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="network_difficulty_not_met",
                    hash_prefix=hash_result[:16],
                    network_difficulty=self.network_difficulty
                )
                return False, "Hash doesn't meet network difficulty"

            # 7. Проверяем, является ли шар действительным блоком (выше сетевой сложности)
            is_valid_block = self.check_if_valid_block(hash_result)

            if is_valid_block:
                logger.warning(
                    "ШАР ЯВЛЯЕТСЯ ДЕЙСТВИТЕЛЬНЫМ БЛОКОМ!",
                    event="share_is_valid_block",
                    miner_address=miner_address,
                    job_id=job_id,
                    hash=hash_result,
                    network_difficulty=self.network_difficulty
                )
                # Здесь должна быть дополнительная логика для обработки найденного блока

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
                total_invalid=self.invalid_shares,
                is_valid_block=is_valid_block
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
        total_shares = self.validated_shares + self.invalid_shares
        success_rate = self.validated_shares / total_shares if total_shares > 0 else 0

        stats = {
            "jobs_in_cache": len(self.jobs_cache),
            "validated_shares": self.validated_shares,
            "invalid_shares": self.invalid_shares,
            "total_shares": total_shares,
            "success_rate": f"{success_rate:.2%}",
            "uptime_seconds": int((datetime.now(UTC) - self.start_time).total_seconds()),
            "target_difficulty": self.target_difficulty,
            "network_difficulty": self.network_difficulty,
            "last_network_update": self.last_network_update.isoformat() if self.last_network_update else None
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

    def calculate_hash(self, job_data: dict, extra_nonce2: str, ntime: str, nonce: str) -> str:
        """Расчет хэша заголовка блока"""
        try:
            # Параметры из задания Stratum
            params = job_data["params"]
            prevhash = params[1]  # предыдущий хэш блока
            coinb1 = params[2]  # первая часть coinbase
            coinb2 = params[3]  # вторая часть coinbase
            merkle_branch = params[4]  # ветки Merkle дерева
            version = params[5]  # версия блока
            nbits = params[6]  # сложность в compact формате
            # ntime_param = params[7]  # время из задания

            # Используем extra_nonce1 из инициализации
            extra_nonce1 = self.extra_nonce1

            # Собираем coinbase транзакцию
            coinbase = coinb1 + extra_nonce1 + extra_nonce2 + coinb2
            print(f"🔍 COINBASE: {coinbase[:100]}...", flush=True)

            # Хэшируем coinbase транзакцию (двойной SHA256)
            coinbase_hash_obj = hashlib.sha256(bytes.fromhex(coinbase))
            coinbase_hash = hashlib.sha256(coinbase_hash_obj.digest()).digest()
            print(f"🔍 COINBASE HASH: {coinbase_hash.hex()}", flush=True)

            # Вычисляем Merkle root с использованием merkle_branch
            merkle_root = self._calculate_merkle_root_with_branch(
                coinbase_hash.hex(),
                merkle_branch
            )
            print(f"🔍 MERKLE ROOT: {merkle_root}", flush=True)

            # Собираем заголовок блока
            header = (
                    bytes.fromhex(version)[::-1] +  # version (little-endian)
                    bytes.fromhex(prevhash)[::-1] +  # previous block hash
                    bytes.fromhex(merkle_root)[::-1] +  # merkle root
                    bytes.fromhex(ntime)[::-1] +  # timestamp
                    bytes.fromhex(nbits)[::-1] +  # bits
                    bytes.fromhex(nonce)[::-1]  # nonce
            )

            print(f"🔍 HEADER LENGTH: {len(header)} (должно быть 80)", flush=True)
            print(f"🔍 HEADER HEX: {header.hex()[:100]}...", flush=True)
            # Двойной SHA256
            first_hash_obj = hashlib.sha256(header)
            first_hash = first_hash_obj.digest()
            block_hash_obj = hashlib.sha256(first_hash)
            block_hash = block_hash_obj.digest()

            # Переворачиваем (little-endian -> big-endian для отображения)
            result = block_hash[::-1].hex()
            print(f"🔍 BLOCK HASH: {result}", flush=True)

            return result

        except Exception as e:
            logger.error(f"Ошибка расчета хэша: {e}")
            return "0" * 64

    @staticmethod
    def _calculate_merkle_root_with_branch(coinbase_hash: str, merkle_branch: list) -> str:
        """Вычисление Merkle root с использованием ветвей"""
        try:
            # Начинаем с хэша coinbase
            current_hash = bytes.fromhex(coinbase_hash)[::-1]  # little-endian

            # Проходим по всем ветвям Merkle дерева
            for branch_hash_hex in merkle_branch:
                branch_hash = bytes.fromhex(branch_hash_hex)[::-1]

                # Конкатенируем и вычисляем родительский хэш
                # Порядок важен: для четного индекса - текущий хэш слева
                concat = current_hash + branch_hash
                first_hash_obj = hashlib.sha256(concat)
                first_hash = first_hash_obj.digest()
                current_hash = hashlib.sha256(first_hash).digest()

            # Возвращаем в big-endian
            return current_hash[::-1].hex()

        except Exception as e:
            logger.error(f"Ошибка расчета Merkle root: {e}")
            # Fallback: используем только coinbase hash
            return coinbase_hash

    def check_difficulty(self, hash_result: str, target_difficulty: float) -> bool:
        """
        Проверка сложности пула (минимальный порог для принятия шара)
        """
        try:
            if target_difficulty <= 0:
                return False

            hash_int = int(hash_result, 16)

            # Используем единый target_for_difficulty_1 из __init__
            target = self.target_for_difficulty_1 // int(target_difficulty)

            print(f"🔍 POOL CHECK: hash={hash_int:#064x}", flush=True)
            print(f"🔍 POOL CHECK: target(diff={target_difficulty})={target:#064x}", flush=True)
            print(f"🔍 POOL CHECK: result={hash_int <= target}", flush=True)

            return hash_int <= target

        except Exception as e:
            logger.error(f"check_difficulty error: {e}")
            return False

    def check_network_difficulty(self, hash_result: str) -> bool:
        """
        Проверка сетевой сложности (является ли шар БЛОКОМ)
        Для SOLO пула: если True - это блок, нужно отправить в ноду!
        """
        try:
            if self.network_difficulty <= 0:
                logger.debug("Network difficulty unknown, cannot check block")
                return False

            hash_int = int(hash_result, 16)

            # Используем единый target_for_difficulty_1 из __init__
            network_target = self.target_for_difficulty_1 // int(self.network_difficulty)

            is_block = hash_int <= network_target

            if is_block:
                print(f"🎉🎉🎉 BLOCK CANDIDATE! hash={hash_result[:16]}...", flush=True)
                logger.warning(
                    f"BLOCK CANDIDATE! Network difficulty: {self.network_difficulty}",
                    event="block_candidate",
                    hash_prefix=hash_result[:16]
                )

            return is_block

        except Exception as e:
            logger.error(f"check_network_difficulty error: {e}")
            return False

    def check_if_valid_block(self, hash_result: str) -> bool:
        """Проверяем, является ли шар действительным блоком (выше сетевой сложности)"""
        try:
            return self.check_network_difficulty(hash_result)
        except Exception as e:
            logger.error(f"Ошибка проверки валидности блока: {e}")
            return False

    def update_network_difficulty(self, new_difficulty: float):
        """Обновление сетевой сложности (вызывается из job_manager)"""
        if new_difficulty <= 0:
            logger.warning(f"Invalid network difficulty: {new_difficulty}")
            return

        old = self.network_difficulty
        self.network_difficulty = new_difficulty
        self.last_network_update = datetime.now(UTC)

        print(f"📊 NETWORK DIFFICULTY UPDATED: {old} -> {new_difficulty}", flush=True)
        logger.info(
            "Network difficulty updated",
            event="network_difficulty_updated",
            old=old,
            new=new_difficulty
        )

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий - НЕ УДАЛЯЕМ КОРОТКИЕ ID"""
        current_time = datetime.now(UTC)
        jobs_to_remove = []

        for job_id in list(self.jobs_cache.keys()):
            # Пропускаем короткие ID (4 hex символа) - их не чистим
            if len(job_id) <= 8 and all(c in '0123456789abcdef' for c in job_id.lower()):
                continue  # ← НЕ УДАЛЯЕМ КОРОТКИЕ ID

            # Только для длинных ID с timestamp
            if job_id.startswith("job_"):
                parts = job_id.split('_')
                if len(parts) >= 2:
                    timestamp_str = parts[1]
                    try:
                        job_time = datetime.fromtimestamp(int(timestamp_str), UTC)
                        age = (current_time - job_time).total_seconds()
                        if age > max_age_seconds:
                            jobs_to_remove.append(job_id)
                    except (ValueError, OSError, OverflowError):
                        jobs_to_remove.append(job_id)
                else:
                    jobs_to_remove.append(job_id)
            else:
                # Для других форматов - не удаляем автоматически
                continue

        for job_id in jobs_to_remove:
            self.remove_job(job_id)

        if jobs_to_remove:
            logger.info(f"Validator: cleaned {len(jobs_to_remove)} old jobs")