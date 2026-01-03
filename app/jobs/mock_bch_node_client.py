import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime, UTC
import logging
logger = logging.getLogger(__name__)

class MockBCHNodeClient:
    """Мок-клиент BCH ноды для тестирования"""

    def __init__(self):
        self.block_height = 840000
        self.difficulty = 12345.6789

    async def get_block_template(self) -> Optional[Dict]:
        """Возвращает тестовый шаблон блока"""
        await asyncio.sleep(0.05)  # Небольшая задержка для реализма

        # Генерируем "случайный" предыдущий хэш
        timestamp = int(time.time())
        prev_hash_input = f"block_{self.block_height}_{timestamp}"
        prev_hash = hashlib.sha256(prev_hash_input.encode()).hexdigest()
        # TODO: исправить 6.25  BCH 3.125 BCH
        return {
            "previousblockhash": prev_hash,
            "coinbaseaux": {"flags": ""},
            "coinbasevalue": 625000000,  # 6.25 BCH в сатоши
            "longpollid": prev_hash + "999",
            "target": "00000000ffff0000000000000000000000000000000000000000000000000000",
            "mintime": timestamp - 7200,  # 2 часа назад
            "mutable": ["time", "transactions", "prevblock"],
            "noncerange": "00000000ffffffff",
            "sigoplimit": 80000,
            "sizelimit": 4000000,
            "curtime": timestamp,
            "bits": "1d00ffff",
            "height": self.block_height,
            "version": 0x20000000,
            "transactions": []
        }

    async def get_blockchain_info(self) -> Optional[Dict]:
        """Информация о блокчейне"""
        await asyncio.sleep(0.05)
        return {
            "chain": "test",
            "blocks": self.block_height,
            "headers": self.block_height,
            "difficulty": self.difficulty,
            "networkhashps": self.difficulty * 2 ** 32 / 600  # Примерный хэшрейт сети
        }

    async def submit_block(self, block_data: str) -> Optional[Dict]:
        """Имитация отправки блока"""
        await asyncio.sleep(0.1)
        logger.info(f" +++++++ [MOCK] Блок отправлен в сеть: {block_data[:64]}...")
        return {"status": "accepted"}
