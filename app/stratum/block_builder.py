"""
Модуль для сборки полного блока BCH из данных майнера
"""
import hashlib
import struct
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1
from app.utils.bch_address import create_coinbase_script
from app.utils.config import settings

logger = StructuredLogger(__name__)


class BlockBuilder:
    """Сборщик блоков Bitcoin Cash"""

    def __init__(self, network_manager=None):
        self.network_manager = network_manager

        # Fallback значения из config
        self._fallback_coinbase_value = settings.fallback_coinbase_value
        self._fallback_prev_block_hash = settings.fallback_prev_block_hash
        self._fallback_difficulty = settings.fallback_difficulty
        self._coinbase_prefix = settings.coinbase_prefix.encode('utf-8')
        self._max_script_sig_size = settings.max_script_sig_size
        self._default_bits = settings.block_bits
        self._default_version = settings.block_version

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _get_coinbase_value(self, template: Dict) -> int:
        """Получение награды за блок с fallback"""
        if 'coinbasevalue' in template:
            return template['coinbasevalue']
        elif self.network_manager:
            return self.network_manager.get_fallback_coinbase_value()
        else:
            return self._fallback_coinbase_value

    def _get_prev_block_hash(self, template: Dict) -> str:
        """Получение хэша предыдущего блока с fallback"""
        if 'previousblockhash' in template:
            return template['previousblockhash']
        elif self.network_manager:
            return self.network_manager.get_fallback_prev_block_hash()
        else:
            return self._fallback_prev_block_hash

    def _get_coinbase_prefix(self) -> bytes:
        """Получение префикса для coinbase"""
        if self.network_manager:
            return self.network_manager.get_coinbase_prefix()
        return self._coinbase_prefix

    def _get_max_script_sig_size(self) -> int:
        """Получение максимального размера ScriptSig"""
        if self.network_manager:
            return self.network_manager.get_max_script_sig_size()
        return self._max_script_sig_size

    def _get_default_bits(self) -> str:
        """Получение bits по умолчанию"""
        if self.network_manager:
            return self.network_manager.get_default_bits()
        return self._default_bits

    def _get_default_version(self) -> int:
        """Получение версии блока по умолчанию"""
        if self.network_manager:
            return self.network_manager.get_default_block_version()
        return self._default_version

    @staticmethod
    def calculate_merkle_root(tx_hashes: List[str]) -> str:
        """Вычисление Merkle root из списка хэшей транзакций"""
        if not tx_hashes:
            return "0" * 64

        # Конвертируем все хэши в бинарный формат (little-endian как в BCH)
        hashes = [bytes.fromhex(h)[::-1] for h in tx_hashes]

        while len(hashes) > 1:
            # Если нечетное количество, дублируем последний
            if len(hashes) % 2 != 0:
                hashes.append(hashes[-1])

            new_hashes = []
            for i in range(0, len(hashes), 2):
                # Конкатенируем пару и делаем двойной SHA256
                concat = hashes[i] + hashes[i + 1]
                first_hash = hashlib.sha256(concat).digest()
                new_hash = hashlib.sha256(first_hash).digest()
                new_hashes.append(new_hash)

            hashes = new_hashes

        return hashes[0][::-1].hex()  # Возвращаем в big-endian для отображения

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        """Кодирование varint (переменная длина)"""
        if value < 0xfd:
            return struct.pack('<B', value)
        elif value <= 0xffff:
            return b'\xfd' + struct.pack('<H', value)
        elif value <= 0xffffffff:
            return b'\xfe' + struct.pack('<I', value)
        else:
            return b'\xff' + struct.pack('<Q', value)

    def build_coinbase_transaction(self,
                                   template: Dict,
                                   miner_address: str,
                                   extra_nonce1: str = STRATUM_EXTRA_NONCE1,
                                   extra_nonce2: str = "00000000"
                                   ) -> Tuple[str, str, str]:
        """
        Сборка coinbase транзакции

        Args:
            template: Шаблон блока от ноды
            miner_address: Адрес майнера для выплаты
            extra_nonce1: Extra nonce 1 из Stratum
            extra_nonce2: Extra nonce 2 от майнера

        Returns:
            Tuple[coinbase_hex, coinbase_txid, merkle_branch]
        """
        try:
            # Получаем награду за блок из шаблона
            coinbase_value = self._get_coinbase_value(template)

            # Суммируем комиссии транзакций из шаблона
            transaction_fees = sum(tx.get('fee', 0) for tx in template.get('transactions', []))
            total_value = coinbase_value + transaction_fees

            logger.debug(
                "Создание coinbase транзакции",
                event="block_builder_coinbase_creating",
                height=template.get('height', 'unknown'),
                coinbase_value=coinbase_value,
                transaction_fees=transaction_fees,
                total_value=total_value,
                miner_address=miner_address[:20] + "..." if miner_address else "unknown"
            )

            # ========== 1. Создание ScriptSig (coinbase input) ==========
            # BIP-34: Height должен быть в ScriptSig (начиная с блока 227,836 для BCH)
            height = template.get('height', 0)

            # Подготавливаем данные для ScriptSig
            # Формат для BCH: [height][optional data][extra_nonce1][extra_nonce2]
            if height <= 0xff:
                height_bytes = bytes([height])
            elif height <= 0xffff:
                height_bytes = struct.pack('<H', height)
            else:
                height_bytes = struct.pack('<I', height)

            # Подготавливаем extra nonce
            extra_nonce1_bytes = bytes.fromhex(extra_nonce1)
            extra_nonce2_bytes = bytes.fromhex(extra_nonce2)

            # Создаем ScriptSig с префиксом
            script_sig_data = height_bytes + self._get_coinbase_prefix() + \
                              extra_nonce1_bytes + extra_nonce2_bytes

            # Если ScriptSig слишком длинный, обрезаем
            max_script_sig_size = self._get_max_script_sig_size()
            if len(script_sig_data) > max_script_sig_size:
                script_sig_data = script_sig_data[:max_script_sig_size]
                logger.warning(
                    "ScriptSig слишком длинный, обрезан",
                    event="block_builder_scriptsig_truncated",
                    original_size=len(script_sig_data),
                    truncated_size=max_script_sig_size
                )

            script_sig_size = len(script_sig_data)
            script_sig_varint = self._encode_varint(script_sig_size)

            # ========== 2. Создание ScriptPubKey (output) ==========
            # Создаем ScriptPubKey для адреса майнера
            script_pubkey_hex = create_coinbase_script(miner_address)
            if not script_pubkey_hex:
                logger.error(
                    "Не удалось создать ScriptPubKey для адреса",
                    event="block_builder_scriptpubkey_error",
                    miner_address=miner_address
                )
                return "", "", ""

            script_pubkey_bytes = bytes.fromhex(script_pubkey_hex)
            script_pubkey_size = len(script_pubkey_bytes)
            script_pubkey_varint = self._encode_varint(script_pubkey_size)

            # ========== 3. Сборка транзакции ==========
            # Версия транзакции (4 байта, little-endian)
            version = struct.pack('<I', 1)

            # Количество inputs (varint = 1)
            input_count = b'\x01'

            # Input: 32 нулевых байта + 0xffffffff
            prev_output_hash = bytes(32)  # 0000...0000 для coinbase
            prev_output_index = struct.pack('<I', 0xffffffff)

            # Sequence (4 байта = 0xffffffff)
            sequence = struct.pack('<I', 0xffffffff)

            # Количество outputs (varint = 1)
            output_count = b'\x01'

            # Output: значение (8 байт, little-endian) + ScriptPubKey
            value = struct.pack('<Q', total_value)

            # Lock time (4 байта = 0)
            lock_time = struct.pack('<I', 0)

            # Собираем всю транзакцию
            coinbase_tx = (
                    version +
                    input_count +
                    prev_output_hash +
                    prev_output_index +
                    script_sig_varint +
                    script_sig_data +
                    sequence +
                    output_count +
                    value +
                    script_pubkey_varint +
                    script_pubkey_bytes +
                    lock_time
            )

            # Хэшируем транзакцию для получения txid (двойной SHA256)
            first_hash_obj = hashlib.sha256(coinbase_tx)
            first_hash = first_hash_obj.digest()
            coinbase_txid_obj = hashlib.sha256(first_hash)
            coinbase_txid = coinbase_txid_obj.digest()

            # Переворачиваем для правильного порядка байт (little-endian для Merkle tree)
            coinbase_txid_le = coinbase_txid[::-1].hex()

            # ========== 4. Создание Merkle branch для Stratum ==========
            # Для Stratum протокола нужен список хэшей транзакций для Merkle branch
            merkle_branch = []
            all_tx_hashes = [coinbase_txid_le]

            # Добавляем хэши других транзакций из шаблона
            for tx in template.get('transactions', []):
                if 'hash' in tx:
                    all_tx_hashes.append(tx['hash'])

            # Вычисляем Merkle branch для coinbase
            if len(all_tx_hashes) > 1:
                merkle_branch = self._calculate_merkle_branch(all_tx_hashes)

            logger.info(
                "Coinbase транзакция создана",
                event="block_builder_coinbase_created",
                height=height,
                txid=coinbase_txid.hex(),
                value=total_value,
                script_sig_size=script_sig_size,
                script_pubkey_size=script_pubkey_size,
                merkle_branch_length=len(merkle_branch)
            )

            return coinbase_tx.hex(), coinbase_txid_le, json.dumps(merkle_branch)

        except Exception as e:
            logger.error(
                "Ошибка создания coinbase транзакции",
                event="block_builder_coinbase_error",
                miner_address=miner_address[:20] + "..." if miner_address else "unknown",
                error=str(e),
                error_type=type(e).__name__
            )
            return "", "", ""

    @staticmethod
    def _calculate_merkle_branch(tx_hashes: List[str]) -> List[str]:
        """Вычисление Merkle branch для конкретной транзакции"""
        if not tx_hashes or len(tx_hashes) <= 1:
            return []

        # Находим индекс coinbase транзакции (она всегда первая)
        target_index = 0

        # Конвертируем хэши в бинарный формат
        hashes = [bytes.fromhex(h)[::-1] for h in tx_hashes]
        merkle_branch = []

        while len(hashes) > 1:
            new_hashes = []

            # Если нечетное количество, дублируем последний
            if len(hashes) % 2 != 0:
                hashes.append(hashes[-1])

            for i in range(0, len(hashes), 2):
                if i == target_index:
                    # Добавляем хэш-партнера в branch
                    merkle_branch.append(hashes[i + 1][::-1].hex())
                elif i + 1 == target_index:
                    # Добавляем хэш-партнера в branch
                    merkle_branch.append(hashes[i][::-1].hex())

                # Вычисляем родительский хэш
                concat = hashes[i] + hashes[i + 1]
                # Двойной SHA256 с явным созданием объектов
                hash1: hashlib._Hash = hashlib.sha256(concat)
                intermediate_hash: bytes = hash1.digest()
                hash2: hashlib._Hash = hashlib.sha256(intermediate_hash)
                new_hash: bytes = hash2.digest()
                new_hashes.append(new_hash)

            # Обновляем индекс для следующего уровня
            target_index //= 2
            hashes = new_hashes
        return merkle_branch

    def build_block_header(self,
                           template: Dict,
                           merkle_root: str,
                           ntime: str,
                           nonce: str,
                           version: str = None
                           ) -> Tuple[bytes, str]:
        """
        Сборка заголовка блока

        Args:
            template: Шаблон блока от ноды
            merkle_root: Merkle root
            ntime: Время
            nonce: Nonce
            version: Версия блока (опционально)

        Returns:
            Tuple[header_bytes, header_hash]
        """
        try:
            # Версия блока из шаблона или по умолчанию
            if version:
                version_bytes = bytes.fromhex(version)[::-1]
            else:
                version_int = template.get('version', self._get_default_version())
                version_bytes = struct.pack('<I', version_int)

            # Предыдущий хэш блока из шаблона с fallback
            prev_hash = bytes.fromhex(
                template.get('previousblockhash', self._get_prev_block_hash(template))
            )[::-1]

            # Merkle root (little-endian)
            merkle_bytes = bytes.fromhex(merkle_root)[::-1]

            # Время (ntime может быть hex или int)
            if len(ntime) == 8:
                time_bytes = bytes.fromhex(ntime)[::-1]
            else:
                # Если ntime не hex, используем время из шаблона или текущее
                time_int = int(ntime) if ntime.isdigit() else template.get('curtime', 0)
                time_bytes = struct.pack('<I', time_int)

            # Bits (сложность, little-endian) с fallback
            bits = template.get('bits', self._get_default_bits())
            bits_bytes = bytes.fromhex(bits)[::-1]

            # Nonce
            if len(nonce) == 8:
                nonce_bytes = bytes.fromhex(nonce)[::-1]
            else:
                # Если nonce не hex, преобразуем
                nonce_int = int(nonce, 16) if nonce.startswith('0x') else int(nonce)
                nonce_bytes = struct.pack('<I', nonce_int)

            # Собираем заголовок
            header = (
                    version_bytes +
                    prev_hash +
                    merkle_bytes +
                    time_bytes +
                    bits_bytes +
                    nonce_bytes
            )

            if len(header) != 80:
                logger.error(
                    "Неправильная длина заголовка",
                    event="block_builder_header_length_error",
                    expected=80,
                    actual=len(header),
                    height=template.get('height', 'unknown')
                )
                return b'', ""

            # Рассчитываем хэш заголовка (двойной SHA256)
            first_hash_obj = hashlib.sha256(header)
            first_hash = first_hash_obj.digest()
            header_hash_obj = hashlib.sha256(first_hash)
            header_hash = header_hash_obj.digest()

            # Для проверки сложности нужен big-endian
            header_hash_be = header_hash[::-1].hex()

            logger.debug(
                "Заголовок блока создан",
                event="block_builder_header_created",
                height=template.get('height', 'unknown'),
                header_hash_prefix=header_hash_be[:16],
                header_length=len(header),
                merkle_root_prefix=merkle_root[:16]
            )

            return header, header_hash_be

        except Exception as e:
            logger.error(
                "Ошибка сборки заголовка",
                event="block_builder_header_error",
                height=template.get('height', 'unknown') if template else 'unknown',
                error=str(e),
                error_type=type(e).__name__
            )
            return b'', ""

    def assemble_full_block(
            self,
            template: Dict,
            header: bytes,
            coinbase_tx: str,
            transactions: List[str]
    ) -> str:
        """
        Сборка полного блока в hex

        Args:
            template: Шаблон блока (используется для получения транзакций)
            header: Заголовок блока
            coinbase_tx: Coinbase транзакция в hex
            transactions: Список других транзакций в hex

        Returns:
            hex строка полного блока
        """
        try:
            # Если transactions не переданы, получаем из шаблона
            if not transactions:
                transactions = []
                for tx in template.get('transactions', []):
                    if 'data' in tx:
                        transactions.append(tx['data'])
                    elif 'hex' in tx:
                        transactions.append(tx['hex'])
                    elif 'txid' in tx:
                        # Если есть только txid, нужно получить полную транзакцию из ноды
                        # Пока пропускаем такие транзакции
                        logger.warning(
                            "Транзакция без данных, пропускаем",
                            event="block_builder_transaction_no_data",
                            txid=tx.get('txid', 'unknown')[:16]
                        )

            # Создаем список всех транзакций (coinbase + остальные)
            tx_list = [coinbase_tx] + transactions

            # Количество транзакций (varint)
            tx_count = len(tx_list)
            tx_count_varint = self._encode_varint(tx_count)

            # Собираем блок
            block_bytes = header + tx_count_varint

            for tx_hex in tx_list:
                tx_bytes = bytes.fromhex(tx_hex)
                block_bytes += tx_bytes

            block_hex = block_bytes.hex()

            # Рассчитываем хэш блока для логов
            first_hash_obj = hashlib.sha256(header)
            first_hash = first_hash_obj.digest()
            block_hash_obj = hashlib.sha256(first_hash)
            block_hash = block_hash_obj.digest()[::-1].hex()

            logger.info(
                "Полный блок собран",
                event="block_builder_block_assembled",
                block_size_bytes=len(block_bytes),
                transaction_count=tx_count,
                coinbase_included=True,
                block_hash_prefix=block_hash[:16],
                height=template.get('height', 'unknown')
            )

            return block_hex

        except Exception as e:
            logger.error(
                "Ошибка сборки блока",
                event="block_builder_assembly_error",
                height=template.get('height', 'unknown') if template else 'unknown',
                error=str(e),
                error_type=type(e).__name__
            )
            return ""

    @staticmethod
    def calculate_block_hash(header: bytes) -> str:
        """Расчет хэша блока из заголовка"""
        try:
            first_hash_obj = hashlib.sha256(header)
            first_hash = first_hash_obj.digest()
            block_hash_obj = hashlib.sha256(first_hash)
            block_hash = block_hash_obj.digest()
            return block_hash[::-1].hex()  # big-endian для отображения
        except Exception as e:
            logger.error(
                "Ошибка расчета хэша блока",
                event="block_builder_hash_error",
                error=str(e)
            )
            return ""

    def validate_block_solution(
            self,
            template: Dict,
            merkle_root: str,
            ntime: str,
            nonce: str,
            target_difficulty: float = 1.0
    ) -> Tuple[bool, str, str]:
        """
        Валидация решения блока

        Args:
            template: Шаблон блока
            merkle_root: Merkle root
            ntime: Время
            nonce: Nonce
            target_difficulty: Целевая сложность

        Returns:
            Tuple[is_valid, block_hash, error_message]
        """
        try:
            # Используем текущий экземпляр для сборки заголовка
            header, header_hash = self.build_block_header(
                template, merkle_root, ntime, nonce
            )

            if not header:
                return False, "", "Ошибка сборки заголовка"

            # Преобразуем хэш в число для сравнения с target
            hash_int = int(header_hash, 16)

            # Целевое значение для сложности 1.0 (BCH)
            # Это стандартное значение target для Bitcoin/Bitcoin Cash при сложности 1.0
            # https://en.bitcoin.it/wiki/Difficulty
            target_for_difficulty_1 = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

            # Вычисляем target для текущей сложности
            target = target_for_difficulty_1 // int(target_difficulty)

            # Проверяем: хэш должен быть меньше или равен target
            is_valid = hash_int <= target

            logger.info(
                "Проверка решения блока",
                event="block_builder_solution_validated",
                height=template.get('height', 'unknown'),
                is_valid=is_valid,
                hash_prefix=header_hash[:16],
                target_prefix=format(target, '064x')[:16],
                difficulty=target_difficulty
            )

            return is_valid, header_hash, ""

        except Exception as e:
            logger.error(
                "Ошибка валидации решения блока",
                event="block_builder_validation_error",
                height=template.get('height', 'unknown') if template else 'unknown',
                error=str(e),
                error_type=type(e).__name__
            )
            return False, "", f"Ошибка валидации: {str(e)}"

    def create_complete_block(
            self,
            template: Dict,
            miner_address: str,
            extra_nonce1: str,
            extra_nonce2: str,
            ntime: str,
            nonce: str
    ) -> Optional[Dict]:
        """
        Полное создание блока из всех компонентов

        Args:
            template: Шаблон блока от ноды
            miner_address: Адрес майнера
            extra_nonce1: Extra nonce 1
            extra_nonce2: Extra nonce 2
            ntime: Время
            nonce: Nonce

        Returns:
            Dict с полной информацией о блоке или None при ошибке
        """
        try:
            height = template.get('height', 'unknown')

            logger.info(
                "Создание полного блока",
                event="block_builder_full_creation_start",
                height=height,
                miner_address=miner_address[:20] + "..."
            )

            # 1. Создаем coinbase транзакцию
            coinbase_hex, coinbase_txid, merkle_branch_json = self.build_coinbase_transaction(
                template, miner_address, extra_nonce1, extra_nonce2
            )

            if not coinbase_hex:
                logger.error(
                    "Не удалось создать coinbase транзакцию",
                    event="block_builder_coinbase_failed",
                    height=height
                )
                return None

            # 2. Собираем хэши транзакций для Merkle root
            tx_hashes = [coinbase_txid]

            # Добавляем хэши других транзакций из шаблона
            for tx in template.get('transactions', []):
                if 'hash' in tx:
                    tx_hashes.append(tx['hash'])

            # 3. Рассчитываем Merkle root
            merkle_root = self.calculate_merkle_root(tx_hashes)

            # 4. Собираем заголовок
            header, header_hash = self.build_block_header(
                template, merkle_root, ntime, nonce
            )

            if not header:
                logger.error(
                    "Не удалось создать заголовок блока",
                    event="block_builder_header_failed",
                    height=height
                )
                return None

            # 5. Собираем остальные транзакции в hex
            other_transactions = []
            for tx in template.get('transactions', []):
                if 'data' in tx:
                    other_transactions.append(tx['data'])
                elif 'hex' in tx:
                    other_transactions.append(tx['hex'])

            # 6. Собираем полный блок
            block_hex = self.assemble_full_block(
                template, header, coinbase_hex, other_transactions
            )

            if not block_hex:
                logger.error(
                    "Не удалось собрать полный блок",
                    event="block_builder_assembly_failed",
                    height=height
                )
                return None

            # 7. Рассчитываем размер блока
            block_size = len(block_hex) // 2  # hex -> bytes

            # 8. Создаем результат
            result = {
                "block_hex": block_hex,
                "header_hash": header_hash,
                "height": height,
                "merkle_root": merkle_root,
                "coinbase_txid": coinbase_txid,
                "merkle_branch": merkle_branch_json,
                "transaction_count": len(tx_hashes),
                "timestamp": int(ntime, 16) if len(ntime) == 8 else int(ntime),
                "size_bytes": block_size,
                "difficulty": template.get('bits', '1d00ffff'),
                "previous_block": template.get('previousblockhash', ''),
                "version": template.get('version', 0x20000000),
                "coinbase_value": template.get('coinbasevalue', 0)
            }

            logger.info(
                "Полный блок создан успешно",
                event="block_builder_full_creation_success",
                height=height,
                block_hash_prefix=header_hash[:16] + "...",
                transaction_count=result["transaction_count"],
                block_size_bytes=block_size
            )

            return result

        except Exception as e:
            logger.error(
                "Ошибка создания полного блока",
                event="block_builder_full_creation_error",
                height=template.get('height', 'unknown') if template else 'unknown',
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    def create_stratum_job_data(
            self,
            template: Dict,
            job_id: str,
            miner_address: str,
            extra_nonce1: str = STRATUM_EXTRA_NONCE1
    ) -> Optional[Dict]:
        """
        Создание данных задания для Stratum протокола

        Args:
            template: Шаблон блока от ноды
            job_id: ID задания
            miner_address: Адрес майнера
            extra_nonce1: Extra nonce 1

        Returns:
            Данные задания в формате Stratum или None
        """
        try:
            height = template.get('height', 'unknown')

            # Создаем coinbase транзакцию с placeholder для extra_nonce2
            coinbase_hex, coinbase_txid, merkle_branch_json = self.build_coinbase_transaction(
                template, miner_address, extra_nonce1, "00000000"  # placeholder
            )

            if not coinbase_hex:
                return None

            # Разделяем coinbase на части для Stratum
            coinbase_bytes = bytes.fromhex(coinbase_hex)
            extra_nonce1_bytes = bytes.fromhex(extra_nonce1)

            # Ищем extra_nonce1 в coinbase
            try:
                pos = coinbase_bytes.index(extra_nonce1_bytes)
                coinb1 = coinbase_bytes[:pos].hex()
                # extra_nonce2 placeholder занимает 4 байта (8 hex символов)
                coinb2 = coinbase_bytes[pos + len(extra_nonce1_bytes) + 8:].hex()
            except ValueError:
                # Если не нашли, используем упрощенное разделение
                coinb1 = coinbase_hex[:100]  # Первая часть
                coinb2 = coinbase_hex[100:]  # Вторая часть

            # Получаем Merkle branch из JSON
            try:
                merkle_branch = json.loads(merkle_branch_json)
            except (json.JSONDecodeError, TypeError):
                merkle_branch = []

            # Время из шаблона
            curtime = template.get('curtime', int(datetime.now(UTC).timestamp()))
            ntime_hex = format(curtime, '08x')

            # Создаем Stratum job data
            job_data = {
                "method": "mining.notify",
                "params": [
                    job_id,  # Job ID
                    template.get('previousblockhash', ''),  # prevhash
                    coinb1,  # coinb1
                    coinb2,  # coinb2
                    merkle_branch,  # merkle_branch
                    format(template.get('version', 0x20000000), '08x'),  # version
                    template.get('bits', '1d00ffff'),  # nbits
                    ntime_hex,  # ntime
                    True  # clean_jobs
                ],
                "extra_nonce1": extra_nonce1,
                "template": template  # Сохраняем шаблон для сборки блока
            }

            logger.info(
                "Созданы данные задания Stratum",
                event="block_builder_stratum_job_created",
                height=height,
                job_id=job_id,
                coinb1_length=len(coinb1),
                coinb2_length=len(coinb2),
                merkle_branch_length=len(merkle_branch)
            )

            return job_data

        except Exception as e:
            logger.error(
                "Ошибка создания данных задания Stratum",
                event="block_builder_stratum_job_error",
                job_id=job_id,
                error=str(e),
                error_type=type(e).__name__
            )
            return None

    @staticmethod
    async def verify_block_with_node_async(
            block_hex: str,
            node_client
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Асинхронная проверка блока через ноду (отправка submitblock)

        Args:
            block_hex: Блок в hex формате
            node_client: Клиент для подключения к BCH ноде

        Returns:
            Tuple[success, message, response_data]
        """
        try:
            if not block_hex or len(block_hex) < 160:  # Минимальный размер (80 байт header * 2)
                return False, "Invalid block hex", None

            logger.info(
                "Отправка блока на проверку в ноду",
                event="block_builder_node_verification_start",
                block_size=len(block_hex) // 2,
                block_hash_prefix=hashlib.sha256(hashlib.sha256(
                    bytes.fromhex(block_hex[:160])
                ).digest()).digest()[::-1].hex()[:16]
            )

            # Используем node_client если он имеет метод submit_block
            if hasattr(node_client, 'submit_block'):
                result = await node_client.submit_block(block_hex)

                # Анализируем результат
                if result and isinstance(result, dict):
                    if result.get("status") == "accepted" or result.get("result") is None:
                        logger.info("Блок принят нодой", event="block_builder_node_accepted")
                        return True, "Block accepted by node", result
                    else:
                        error_msg = result.get("message", str(result))
                        logger.error("Блок отклонен нодой", event="block_builder_node_rejected", error=error_msg)
                        return False, f"Block rejected: {error_msg}", result
                else:
                    return False, "Unexpected response from node", result
            else:
                # Если node_client не имеет метода submit_block, возвращаем ошибку
                logger.error(
                    "Node client не имеет метода submit_block",
                    event="block_builder_node_missing_method"
                )
                return False, "Node client doesn't have submit_block method", None

        except Exception as e:
            logger.error(
                "Ошибка при проверке блока через ноду",
                event="block_builder_node_verification_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return False, f"Node verification error: {str(e)}", None

    @staticmethod
    def verify_block_with_node_sync(
            block_hex: str,
            node_client
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Синхронная проверка блока через ноду (отправка submitblock)

        Args:
            block_hex: Блок в hex формате
            node_client: Клиент для подключения к BCH ноде

        Returns:
            Tuple[success, message, response_data]
        """
        try:
            if not block_hex or len(block_hex) < 160:  # Минимальный размер (80 байт header * 2)
                return False, "Invalid block hex", None

            logger.info(
                "Отправка блока на проверку в ноду",
                event="block_builder_node_verification_start",
                block_size=len(block_hex) // 2,
                block_hash_prefix=hashlib.sha256(hashlib.sha256(
                    bytes.fromhex(block_hex[:160])
                ).digest()).digest()[::-1].hex()[:16]
            )

            # Используем node_client если он имеет метод submit_block
            if hasattr(node_client, 'submit_block'):
                # Если submit_block асинхронный, нужно запустить его синхронно
                import asyncio
                try:
                    result = asyncio.run(node_client.submit_block(block_hex))
                except RuntimeError:
                    # Если уже есть event loop
                    loop = asyncio.get_event_loop()
                    result = loop.run_until_complete(node_client.submit_block(block_hex))

                # Анализируем результат
                if result and isinstance(result, dict):
                    if result.get("status") == "accepted" or result.get("result") is None:
                        logger.info("Блок принят нодой", event="block_builder_node_accepted")
                        return True, "Block accepted by node", result
                    else:
                        error_msg = result.get("message", str(result))
                        logger.error("Блок отклонен нодой", event="block_builder_node_rejected", error=error_msg)
                        return False, f"Block rejected: {error_msg}", result
                else:
                    return False, "Unexpected response from node", result
            else:
                return False, "Node client doesn't have submit_block method", None

        except Exception as e:
            logger.error(
                "Ошибка при проверке блока через ноду",
                event="block_builder_node_verification_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return False, f"Node verification error: {str(e)}", None