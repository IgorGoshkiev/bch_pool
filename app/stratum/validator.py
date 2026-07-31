import hashlib
from typing import Optional, Tuple, Dict
from datetime import datetime, UTC
from app.utils.config import settings
from app.utils.network_config import NETWORK_CONFIGS

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import (
    EXTRA_NONCE2_SIZE,
)

logger = StructuredLogger(__name__)


class ShareValidator:
    """Валидатор шаров (shares) для Stratum протокола"""

    def __init__(self,
                 pool_difficulty: float = 1.0,
                 extra_nonce2_size: int = EXTRA_NONCE2_SIZE,
                 extra_nonce1: str = None):
        self.pool_difficulty = pool_difficulty
        self.extra_nonce2_size = extra_nonce2_size

        self.extra_nonce1 = extra_nonce1

        self.jobs_cache: Dict[str, dict] = {}
        self._used_nonces: Dict[str, set] = {}
        self.validated_shares = 0
        self.invalid_shares = 0
        self.start_time = datetime.now(UTC)
        self.network_target = None  # текущий target сети (меняется каждые 2 недели, берется из ноды)
        network = getattr(settings, 'bch_network', 'mainnet')
        network_config = NETWORK_CONFIGS.get(network, NETWORK_CONFIGS['mainnet'])
        # константа для расчета сложности (никогда не меняется)
        self.TARGET_FOR_DIFFICULTY_1 = network_config.get(
            'target_for_difficulty_1',
            0x0000000000000000024cb3000000000000000000000000000000000000000000
        )

        self.last_network_update = None

        print(f"🔍 VALIDATOR INIT: target_for_difficulty_1 = {self.TARGET_FOR_DIFFICULTY_1:#066x}", flush=True)
        print(f"🔍 VALIDATOR INIT: target_difficulty = {self.pool_difficulty}", flush=True)

        logger.info(
            "Валидатор инициализирован",
            event="validator_initialized",
            target_difficulty=pool_difficulty,
            extra_nonce2_size=extra_nonce2_size,
            extra_nonce1_length=len(extra_nonce1) if extra_nonce1 else 0,
            target_for_difficulty_1=hex(self.TARGET_FOR_DIFFICULTY_1),
            network=network,
            start_time=self.start_time.isoformat()
        )

    def update_target_from_node(self, target: int):
        """
        Обновить target из ноды для проверки блоков

        Args:
            target: Текущий target сети из getblocktemplate
                   (меняется при каждом изменении сложности)
        """
        self.network_target = target
        self.last_network_update = datetime.now(UTC)
        print(f"🎯 VALIDATOR TARGET UPDATED: {target:#066x}", flush=True)
        logger.info(
            "Validator target обновлен из ноды",
            event="validator_target_updated",
            target=hex(target)
        )

    def add_job(self, job_id: str, job_data: dict):
        """Добавить задание в кэш для валидации"""
        print(f"🟢 VALIDATOR.add_job: job_id={job_id}, exists? {job_id in self.jobs_cache}", flush=True)
        print(f"🟢 VALIDATOR.add_job: cache before size={len(self.jobs_cache)}", flush=True)

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
                       miner_address: str,
                       version: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Проверка валидности шара

        Returns:
            Tuple[is_valid, error_message, extra_data]
            extra_data содержит информацию о найденном блоке
        """
        """Проверка валидности шара"""
        validation_start = datetime.now(UTC)

        print(f"🔍 VALIDATE_SHARE: looking for {job_id}", flush=True)
        print(f"🔍 VALIDATE_SHARE: cache keys = {list(self.jobs_cache.keys())}", flush=True)

        if job_id not in self.jobs_cache:
            self.invalid_shares += 1
            logger.warning(
                "Задание не найдено при валидации шара",
                event="share_validation_failed",
                miner_address=miner_address,
                job_id=job_id,
                reason="job_not_found",
            )
            return False, f"Задание {job_id} не найдено", None

        job = self.jobs_cache[job_id]

        try:
            expected_extra_nonce2_len = self.extra_nonce2_size * 2

            # 1. Проверка форматов
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
                return False, f"Неверный формат extra_nonce2: {extra_nonce2}", None

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
                return False, f"Неверный формат ntime: {ntime}", None

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
                return False, f"Неверный формат nonce: {nonce}", None

            # 2. Проверка времени
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
                return False, f"Некорректное время ntime: {ntime}", None

            # 3. Проверка уникальности nonce
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
                return False, f"Nonce {nonce} уже использовался для задания {job_id}", None

            # 4. Расчет хэша
            hash_result = self.calculate_hash(job, extra_nonce2, ntime, nonce, version)

            if hash_result == "0" * 64:
                self.invalid_shares += 1
                logger.error(
                    "Ошибка расчета хэша",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="hash_calculation_error"
                )
                return False, "Ошибка расчета хэша", None

            # 5. Проверка сложности пула
            print(f"🔍 VALIDATE: BEFORE check_difficulty", flush=True)
            print(f"🔍 hash_result: {hash_result}", flush=True)
            print(f"🔍 target_difficulty: {self.pool_difficulty}", flush=True)

            # 5. Проверка сложности пула
            is_pool_difficulty_ok = self._check_pool_difficulty(hash_result, self.pool_difficulty)

            if not is_pool_difficulty_ok:
                self.invalid_shares += 1
                logger.debug(
                    "Хэш не соответствует сложности пула",
                    event="share_validation_failed",
                    miner_address=miner_address,
                    job_id=job_id,
                    reason="pool_difficulty_not_met"
                )
                return False, "Hash doesn't meet pool target difficulty", None

            # 6. Проверка, является ли шар БЛОКОМ
            is_valid_block = False
            if self.network_target is not None:
                hash_int = int(hash_result, 16)
                is_valid_block = hash_int <= self.network_target

                if is_valid_block:
                    print(f"🎉🎉🎉 BLOCK FOUND! hash={hash_result[:16]}...", flush=True)
                    logger.warning(
                        "ШАР ЯВЛЯЕТСЯ ДЕЙСТВИТЕЛЬНЫМ БЛОКОМ!",
                        event="block_found",
                        miner_address=miner_address,
                        job_id=job_id,
                        hash=hash_result[:16],
                        network_target=hex(self.network_target)
                    )

            # Шар валиден
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
            # Возвращаем дополнительную информацию
            extra_data = {
                "is_valid_block": is_valid_block,
                "hash_result": hash_result,
                "ntime": ntime,
                "nonce": nonce,
                "extra_nonce2": extra_nonce2
            }

            return True, None, extra_data

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
            return False, f"Ошибка валидации: {str(e)}", None

    def _check_pool_difficulty(self, hash_result: str, pool_difficulty: float) -> bool:
        """Проверка сложности пула"""
        try:
            if pool_difficulty <= 0:
                return False

            hash_int = int(hash_result, 16)

            # Для сложности < 1 используем обратную логику
            if pool_difficulty < 1.0:
                target = int(self.TARGET_FOR_DIFFICULTY_1 * (1.0 / pool_difficulty))
            else:
                target = self.TARGET_FOR_DIFFICULTY_1 // int(pool_difficulty)

            print(f"🔍 ========================================", flush=True)
            print(f"🔍 POOL CHECK DETAILS:", flush=True)
            print(f"🔍 hash_result: {hash_result}", flush=True)
            print(f"🔍 hash_int: {hash_int}", flush=True)
            print(f"🔍 target: {target}", flush=True)
            print(f"🔍 target hex: {target:#066x}", flush=True)
            print(f"🔍 hash_int <= target: {hash_int <= target}", flush=True)
            print(f"🔍 DIFF: {hash_int - target}", flush=True)
            print(f"🔍 ========================================", flush=True)

            return hash_int <= target

        except Exception as e:
            logger.error(f"check_difficulty error: {e}")
            return False

    def calculate_hash(self, job_data: dict, extra_nonce2: str, ntime: str, nonce: str, version: Optional[str] = None) -> str:
        """Расчет хэша заголовка блока"""
        try:
            params = job_data["params"]
            prevhash = params[1]
            coinb1 = params[2]
            coinb2 = params[3]
            merkle_branch = params[4]

            # Используем version из параметра или из job_data
            if version:
                version_hex = version
                print(f"🔍 USING VERSION FROM ASIC: {version_hex}", flush=True)
            else:
                version_hex = params[5]
                print(f"🔍 USING VERSION FROM JOB: {version_hex}", flush=True)

            nbits = params[6]

            # ===== ОТЛАДКА =====
            print(f"\n🔍🔍🔍 ПОЛНЫЕ ДАННЫЕ В VALIDATOR 🔍🔍🔍", flush=True)
            print(f"prevhash (оригинал): {prevhash}", flush=True)
            print(f"coinb1: {coinb1}", flush=True)
            print(f"coinb2: {coinb2}", flush=True)
            print(f"merkle_branch: {len(merkle_branch)} элементов", flush=True)
            for i, branch in enumerate(merkle_branch):
                print(f"  branch[{i}]: {branch[:32]}...", flush=True)
            print(f"version: {version if version else params[5]}", flush=True)
            print(f"nbits: {params[6]}", flush=True)
            print(f"ntime: {ntime}", flush=True)
            print(f"nonce: {nonce}", flush=True)
            print(f"extra_nonce2: {extra_nonce2}", flush=True)
            print(f"==========================================\n", flush=True)
            # =============================

            extra_nonce1 = self.extra_nonce1

            # Собираем coinbase
            coinbase = coinb1 + extra_nonce1 + extra_nonce2 + coinb2
            print(f"🔍 COINBASE: {coinbase[:100]}...", flush=True)

            coinbase_hash_obj = hashlib.sha256(bytes.fromhex(coinbase))
            coinbase_hash = hashlib.sha256(coinbase_hash_obj.digest()).digest()
            print(f"🔍 COINBASE HASH: {coinbase_hash.hex()}", flush=True)

            # Merkle root
            merkle_root = self._calculate_merkle_root_with_branch(coinbase_hash.hex(), merkle_branch)
            print(f"🔍 MERKLE ROOT: {merkle_root}", flush=True)

            header = (
                    bytes.fromhex(version_hex)[::-1] +
                    bytes.fromhex(prevhash) +
                    bytes.fromhex(merkle_root)[::-1] +
                    bytes.fromhex(ntime)[::-1] +
                    bytes.fromhex(nbits)[::-1] +
                    bytes.fromhex(nonce)[::-1]
            )

            if len(header) != 80:
                print(f"🔴 ОШИБКА: длина header {len(header)}, должно быть 80", flush=True)
                return "0" * 64

            print(f"🔍 HEADER LENGTH: {len(header)} (должно быть 80)", flush=True)
            print(f"🔍 HEADER HEX: {header.hex()[:100]}...", flush=True)

            # Двойной SHA256
            first_hash = hashlib.sha256(header).digest()
            block_hash = hashlib.sha256(first_hash).digest()
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
            current_hash = bytes.fromhex(coinbase_hash)[::-1]

            for branch_hash_hex in merkle_branch:
                branch_hash = bytes.fromhex(branch_hash_hex)[::-1]
                concat = current_hash + branch_hash
                current_hash = hashlib.sha256(hashlib.sha256(concat).digest()).digest()

            return current_hash[::-1].hex()

        except Exception as e:
            logger.error(f"Ошибка расчета Merkle root: {e}")
            return coinbase_hash

    @staticmethod
    def _validate_hex_format(hex_str: str, expected_length: int) -> bool:
        """Проверка формата hex строки"""
        if not hex_str:
            return False

        if len(hex_str) != expected_length:
            logger.debug(f"Неверная длина hex строки: {hex_str} (длина {len(hex_str)}, ожидается {expected_length})")
            return False

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
            ntime_int = int(ntime_hex, 16)
            current_time = int(datetime.now(UTC).timestamp())
            time_diff = abs(ntime_int - current_time)

            if time_diff > 2 * 60 * 60:
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

        if nonce in self._used_nonces[job_id]:
            return False

        self._used_nonces[job_id].add(nonce)
        self._cleanup_old_nonces()
        return True

    def _cleanup_old_nonces(self, max_per_job: int = 1000):
        """Очистка старых nonce"""
        for job_id in list(self._used_nonces.keys()):
            if len(self._used_nonces[job_id]) > max_per_job:
                all_nonces = list(self._used_nonces[job_id])
                self._used_nonces[job_id] = set(all_nonces[-max_per_job:])

    def cleanup_old_jobs(self, max_age_seconds: int = 300):
        """Очистка старых заданий - НЕ УДАЛЯЕМ КОРОТКИЕ ID"""
        current_time = datetime.now(UTC)
        jobs_to_remove = []

        for job_id in list(self.jobs_cache.keys()):
            # Не удаляем короткие ID (для совместимости)
            if len(job_id) <= 8 and all(c in '0123456789abcdef' for c in job_id.lower()):
                continue

            if job_id.startswith("job_"):
                parts = job_id.split('_')
                if len(parts) >= 2:
                    try:
                        job_time = datetime.fromtimestamp(int(parts[1]), UTC)
                        age = (current_time - job_time).total_seconds()
                        if age > max_age_seconds:
                            jobs_to_remove.append(job_id)
                    except (ValueError, OSError, OverflowError):
                        jobs_to_remove.append(job_id)
                else:
                    jobs_to_remove.append(job_id)

        for job_id in jobs_to_remove:
            self.remove_job(job_id)

        if jobs_to_remove:
            logger.info(f"Validator: cleaned {len(jobs_to_remove)} old jobs")

    def get_stats(self) -> Dict:
        """Получение статистики валидатора"""
        total_shares = self.validated_shares + self.invalid_shares
        success_rate = self.validated_shares / total_shares if total_shares > 0 else 0

        return {
            "jobs_in_cache": len(self.jobs_cache),
            "validated_shares": self.validated_shares,
            "invalid_shares": self.invalid_shares,
            "total_shares": total_shares,
            "success_rate": f"{success_rate:.2%}",
            "uptime_seconds": int((datetime.now(UTC) - self.start_time).total_seconds()),
            "pool_difficulty": self.pool_difficulty,
            "network_target": self.network_target,
            "last_network_update": self.last_network_update.isoformat() if self.last_network_update else None
        }