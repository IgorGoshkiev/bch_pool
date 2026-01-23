"""
Модуль для сборки полного блока BCH из данных майнера
"""
import hashlib
import struct
from typing import Dict, List, Optional, Tuple

from app.utils.logging_config import StructuredLogger
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1

logger = StructuredLogger(__name__)


class BlockBuilder:
    """Сборщик блоков Bitcoin Cash"""

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
    def build_coinbase_transaction(
            template: Dict,
            miner_address: str,
            extra_nonce1: str = STRATUM_EXTRA_NONCE1,
            extra_nonce2: str = "00000000"
    ) -> Tuple[str, str]:
        """
        Сборка coinbase транзакции

        Структура coinbase:
        1. Version (4 байта)
        2. Input count (varint = 1)
        3. Input:
           - Previous output hash (32 байта = 0000...0000)
           - Previous output index (4 байта = 0xffffffff)
           - ScriptSig (размер + данные)
           - Sequence (4 байта = 0xffffffff)
        4. Output count (varint)
        5. Output(s):
           - Value (8 байт)
           - ScriptPubKey (размер + адрес)
        6. Lock time (4 байта = 0)

        Args:
            template: Шаблон блока от ноды
            miner_address: Адрес майнера для выплаты
            extra_nonce1: Extra nonce 1 из Stratum
            extra_nonce2: Extra nonce 2 от майнера

        Returns:
            Tuple[coinbase_hex, coinbase_txid]
        """
        try:
            # Награда за блок (в сатоши)
            #TODO Заменить 3125000000 на переменную , что бы можно было переходить на реальную сеть
            coinbase_value = template.get('coinbasevalue', 3125000000)  # 31.25 BCH в тестнете

            # Если есть информация о комиссиях транзакций
            transaction_fees = sum(tx.get('fee', 0) for tx in template.get('transactions', []))
            total_value = coinbase_value + transaction_fees

            logger.debug(
                "Создание coinbase транзакции",
                event="block_builder_coinbase_creating",
                coinbase_value=coinbase_value,
                transaction_fees=transaction_fees,
                total_value=total_value,
                miner_address=miner_address[:20] + "..." if miner_address else "unknown"
            )

            # ========== 1. Создание ScriptSig (coinbase input) ==========
            # BIP-34: Height должен быть в ScriptSig
            height = template.get('height', 0)

            # Подготовка данных для ScriptSig
            # Формат: [height][optional flags][extra_nonce1][extra_nonce2]
            height_bytes = bytes([height]) if height <= 255 else struct.pack('<I', height)

            # Создаем ScriptSig
            script_sig = height_bytes + b'/BCH Solo Pool/' + bytes.fromhex(extra_nonce1) + bytes.fromhex(extra_nonce2)
            script_sig_size = len(script_sig)

            # Если ScriptSig слишком длинный, обрезаем
            if script_sig_size > 100:
                script_sig = script_sig[:100]
                script_sig_size = 100
                logger.warning(
                    "ScriptSig слишком длинный, обрезан",
                    event="block_builder_scriptsig_truncated",
                    original_size=script_sig_size
                )

            # ========== 2. Создание ScriptPubKey (output) ==========
            # TODO: Реализовать правильное создание ScriptPubKey для BCH адреса
            # Пока используем P2PKH для тестнета
            # Пример: OP_DUP OP_HASH160 <pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG

            # Временная реализация - упрощенный ScriptPubKey
            # В реальности нужно декодировать BCH адрес и создать соответствующий ScriptPubKey
            script_pubkey = bytes.fromhex("76a914") + bytes.fromhex(miner_address[-40:])[:20] + bytes.fromhex("88ac")
            script_pubkey_size = len(script_pubkey)

            # ========== 3. Сборка транзакции ==========
            # Версия (4 байта, little-endian)
            version = struct.pack('<I', 1)

            # Количество inputs (varint = 1)
            input_count = b'\x01'

            # Input: 32 нулевых байта + 0xffffffff
            prev_output_hash = bytes(32)  # 0000...0000 для coinbase
            prev_output_index = struct.pack('<I', 0xffffffff)

            # ScriptSig (длина + данные)
            script_sig_varint = BlockBuilder._encode_varint(script_sig_size)

            # Sequence (4 байта = 0xffffffff)
            sequence = struct.pack('<I', 0xffffffff)

            # Количество outputs (varint = 1)
            output_count = b'\x01'

            # Output: значение (8 байт, little-endian) + ScriptPubKey
            value = struct.pack('<Q', total_value)
            script_pubkey_varint = BlockBuilder._encode_varint(script_pubkey_size)

            # Lock time (4 байта = 0)
            lock_time = struct.pack('<I', 0)

            # Собираем всю транзакцию
            coinbase_tx = (
                    version +
                    input_count +
                    prev_output_hash +
                    prev_output_index +
                    script_sig_varint +
                    script_sig +
                    sequence +
                    output_count +
                    value +
                    script_pubkey_varint +
                    script_pubkey +
                    lock_time
            )

            # Хэшируем транзакцию для получения txid
            first_hash = hashlib.sha256(coinbase_tx).digest()
            coinbase_txid = hashlib.sha256(first_hash).digest()[::-1].hex()

            logger.info(
                "Coinbase транзакция создана",
                event="block_builder_coinbase_created",
                txid=coinbase_txid,
                value=total_value,
                script_sig_size=script_sig_size,
                script_pubkey_size=script_pubkey_size
            )

            return coinbase_tx.hex(), coinbase_txid

        except Exception as e:
            logger.error(
                "Ошибка создания coinbase транзакции",
                event="block_builder_coinbase_error",
                miner_address=miner_address[:20] + "..." if miner_address else "unknown",
                error=str(e),
                error_type=type(e).__name__
            )
            return "", ""

    @staticmethod
    def build_block_header(
            template: Dict,
            merkle_root: str,
            ntime: str,
            nonce: str,
            version: str = None
    ) -> Tuple[bytes, str]:
        """
        Сборка заголовка блока

        Returns:
            Tuple[header_bytes, header_hash]
        """
        try:
            # Версия
            if version:
                version_bytes = bytes.fromhex(version)[::-1]
            else:
                version_bytes = struct.pack('<I', template.get('version', 0x20000000))

            # Предыдущий хэш (little-endian)
            prev_hash = bytes.fromhex(template['previousblockhash'])[::-1]

            # Merkle root (little-endian)
            merkle_bytes = bytes.fromhex(merkle_root)[::-1]

            # Время
            if len(ntime) == 8:
                time_bytes = bytes.fromhex(ntime)[::-1]
            else:
                # Если ntime не hex, преобразуем int в bytes
                time_int = int(ntime) if ntime.isdigit() else template.get('curtime', 0)
                time_bytes = struct.pack('<I', time_int)

            # Bits (сложность, little-endian)
            bits = template.get('bits', '1d00ffff')
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
                    actual=len(header)
                )
                return b'', ""

            # Рассчитываем хэш заголовка
            first_hash = hashlib.sha256(header).digest()
            header_hash = hashlib.sha256(first_hash).digest()

            # Для проверки сложности нужен big-endian
            header_hash_be = header_hash[::-1].hex()

            logger.debug(
                "Заголовок блока создан",
                event="block_builder_header_created",
                header_hash_prefix=header_hash_be[:16],
                header_length=len(header)
            )

            return header, header_hash_be

        except Exception as e:
            logger.error(
                "Ошибка сборки заголовка",
                event="block_builder_header_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return b'', ""

    @staticmethod
    def assemble_full_block(
            template: Dict,
            header: bytes,
            coinbase_tx: str,
            transactions: List[str]
    ) -> str:
        """
        Сборка полного блока в hex

        Структура блока:
        1. Заголовок (80 байт)
        2. Количество транзакций (varint)
        3. Coinbase транзакция
        4. Остальные транзакции
        """
        try:
            # Преобразуем транзакции из hex в bytes
            tx_list = [coinbase_tx] + transactions

            # Количество транзакций (varint)
            tx_count = len(tx_list)
            tx_count_varint = BlockBuilder._encode_varint(tx_count)

            # Собираем блок
            block_bytes = header + tx_count_varint

            for tx_hex in tx_list:
                tx_bytes = bytes.fromhex(tx_hex)
                block_bytes += tx_bytes

            block_hex = block_bytes.hex()

            logger.info(
                "Полный блок собран",
                event="block_builder_block_assembled",
                block_size_bytes=len(block_bytes),
                transaction_count=tx_count,
                block_hash=hashlib.sha256(hashlib.sha256(header).digest()).digest()[::-1].hex()[:16] + "..."
            )

            return block_hex

        except Exception as e:
            logger.error(
                "Ошибка сборки блока",
                event="block_builder_assembly_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return ""

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

    @staticmethod
    def calculate_block_hash(header: bytes) -> str:
        """Расчет хэша блока из заголовка"""
        try:
            first_hash = hashlib.sha256(header).digest()
            block_hash = hashlib.sha256(first_hash).digest()
            return block_hash[::-1].hex()  # big-endian для отображения
        except Exception as e:
            logger.error(
                "Ошибка расчета хэша блока",
                event="block_builder_hash_error",
                error=str(e)
            )
            return ""

    @staticmethod
    def validate_block_solution(
            template: Dict,
            merkle_root: str,
            ntime: str,
            nonce: str,
            target_difficulty: float = 1.0
    ) -> Tuple[bool, str, str]:
        """
        Валидация решения блока

        Returns:
            Tuple[is_valid, block_hash, error_message]
        """
        try:
            # Собираем заголовок
            header, header_hash = BlockBuilder.build_block_header(
                template, merkle_root, ntime, nonce
            )

            if not header:
                return False, "", "Ошибка сборки заголовка"

            # Преобразуем хэш в число для сравнения с target
            hash_int = int(header_hash, 16)

            # Целевое значение для сложности 1.0
            target_for_difficulty_1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

            # Вычисляем target для текущей сложности
            target = target_for_difficulty_1 // int(target_difficulty)

            # Проверяем: хэш должен быть меньше или равен target
            is_valid = hash_int <= target

            logger.info(
                "Проверка решения блока",
                event="block_builder_solution_validated",
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
                error=str(e),
                error_type=type(e).__name__
            )
            return False, "", f"Ошибка валидации: {str(e)}"

    @staticmethod
    def create_complete_block(
            template: Dict,
            miner_address: str,
            extra_nonce1: str,
            extra_nonce2: str,
            ntime: str,
            nonce: str
    ) -> Optional[Dict]:
        """
        Полное создание блока из всех компонентов

        Returns:
            Dict с полной информацией о блоке или None при ошибке
        """
        try:
            logger.info(
                "Создание полного блока",
                event="block_builder_full_creation_start",
                height=template.get('height', 'unknown'),
                miner_address=miner_address[:20] + "..."
            )

            # 1. Создаем coinbase транзакцию
            coinbase_hex, coinbase_txid = BlockBuilder.build_coinbase_transaction(
                template, miner_address, extra_nonce1, extra_nonce2
            )

            if not coinbase_hex:
                logger.error("Не удалось создать coinbase транзакцию")
                return None

            # 2. Собираем хэши транзакций для Merkle root
            tx_hashes = [coinbase_txid]

            # Добавляем хэши других транзакций из шаблона
            for tx in template.get('transactions', []):
                if 'hash' in tx:
                    tx_hashes.append(tx['hash'])

            # 3. Рассчитываем Merkle root
            merkle_root = BlockBuilder.calculate_merkle_root(tx_hashes)

            # 4. Собираем заголовок
            header, header_hash = BlockBuilder.build_block_header(
                template, merkle_root, ntime, nonce
            )

            if not header:
                logger.error("Не удалось создать заголовок блока")
                return None

            # 5. Собираем остальные транзакции в hex
            other_transactions = []
            for tx in template.get('transactions', []):
                if 'data' in tx:
                    other_transactions.append(tx['data'])
                elif 'hex' in tx:
                    other_transactions.append(tx['hex'])

            # 6. Собираем полный блок
            block_hex = BlockBuilder.assemble_full_block(
                template, header, coinbase_hex, other_transactions
            )

            if not block_hex:
                logger.error("Не удалось собрать полный блок")
                return None

            # 7. Создаем результат
            result = {
                "block_hex": block_hex,
                "header_hash": header_hash,
                "height": template.get('height'),
                "merkle_root": merkle_root,
                "coinbase_txid": coinbase_txid,
                "transaction_count": len(tx_hashes),
                "timestamp": int(ntime, 16) if len(ntime) == 8 else int(ntime),
                "size_bytes": len(block_hex) // 2  # hex -> bytes
            }

            logger.info(
                "Полный блок создан успешно",
                event="block_builder_full_creation_success",
                height=result["height"],
                block_hash_prefix=header_hash[:16] + "...",
                transaction_count=result["transaction_count"]
            )

            return result

        except Exception as e:
            logger.error(
                "Ошибка создания полного блока",
                event="block_builder_full_creation_error",
                error=str(e),
                error_type=type(e).__name__
            )
            return None