"""
Модуль для сборки полного блока BCH из данных майнера
"""
import hashlib
import struct
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class BlockBuilder:
    """Сборщик блоков Bitcoin Cash"""

    @staticmethod
    def calculate_merkle_root(tx_hashes: List[str]) -> str:
        """Вычисление Merkle root из списка хэшей транзакций"""
        if not tx_hashes:
            return "0" * 64

        # Конвертируем все хэши в бинарный формат
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

        return hashes[0][::-1].hex()

    @staticmethod
    def build_coinbase_transaction(template: Dict, miner_address: str,
                                   extra_nonce1: str, extra_nonce2: str) -> str:
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
        """
        try:
            # Значение награды (в сатоши)
            reward = template.get('coinbasevalue', 3125000000)  # 31.25 BCH в тестнете

            # Пока используем упрощенный coinbase
            # В реальности нужно правильно сгенерировать ScriptSig и ScriptPubKey
            # TODO: Реализовать полную coinbase транзакцию

            # Временная реализация - возвращаем coinbase из шаблона если есть
            if 'coinbasetxn' in template:
                coinbase_hex = template['coinbasetxn']['data']
            else:
                # Генерируем простую coinbase
                # TODO: Заменить на реальную генерацию
                coinbase_hex = "01000000" + "01" + "00" * 32 + "ffffffff" + "00" + "ffffffff"

            return coinbase_hex

        except Exception as e:
            logger.error(f"Ошибка сборки coinbase: {e}")
            return ""

    @staticmethod
    def build_block_header(template: Dict, merkle_root: str,
                           ntime: str, nonce: str, version: str = None) -> bytes:
        """
        Сборка заголовка блока

        Структура заголовка (80 байт):
        1. Version (4 байта, little-endian)
        2. Previous block hash (32 байта, little-endian)
        3. Merkle root (32 байта, little-endian)
        4. Timestamp (4 байта, little-endian)
        5. Bits (4 байта, little-endian)
        6. Nonce (4 байта, little-endian)
        """
        try:
            # Версия
            if version:
                version_bytes = bytes.fromhex(version)[::-1]
            else:
                version_bytes = struct.pack('<I', template.get('version', 0x20000000))

            # Предыдущий хэш
            prev_hash = bytes.fromhex(template['previousblockhash'])[::-1]

            # Merkle root
            merkle_bytes = bytes.fromhex(merkle_root)[::-1]

            # Время
            if len(ntime) == 8:
                time_bytes = bytes.fromhex(ntime)[::-1]
            else:
                # Если ntime не hex, преобразуем int в bytes
                time_int = int(ntime) if ntime.isdigit() else template.get('curtime', 0)
                time_bytes = struct.pack('<I', time_int)

            # Bits (сложность)
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
            header = version_bytes + prev_hash + merkle_bytes + time_bytes + bits_bytes + nonce_bytes

            if len(header) != 80:
                logger.error(f"Неправильная длина заголовка: {len(header)} байт")

            return header

        except Exception as e:
            logger.error(f"Ошибка сборки заголовка: {e}")
            return b''

    @staticmethod
    def assemble_full_block(template: Dict, header: bytes,
                            coinbase_tx: str, transactions: List[str]) -> str:
        """
        Сборка полного блока в hex

        Структура блока:
        1. Заголовок (80 байт)
        2. Количество транзакций (varint)
        3. Coinbase транзакция
        4. Остальные транзакции
        """
        try:
            # TODO: Реализовать полную сборку блока
            # Пока возвращаем только заголовок для тестов
            return header.hex()

        except Exception as e:
            logger.error(f"Ошибка сборки блока: {e}")
            return ""