"""
Конфигурация для разных сетей Bitcoin Cash
"""
import base58
from typing import Dict, Any
from app.utils.config import settings
from app.utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)

# ========== ГЛОБАЛЬНЫЕ КОНСТАНТЫ ==========
# Эти константы одинаковы для всех сетей
SATOSHIS_PER_BCH = 100_000_000
BLOCK_HEADER_SIZE = 80
TX_VERSION_DEFAULT = 1
SEQUENCE_FINAL = 0xffffffff

# Конфигурация для разных сетей
NETWORK_CONFIGS = {
    'mainnet': {
        'name': 'Bitcoin Cash Mainnet',
        'rpc_port': 8332,
        'default_rpc_port': 8332,
        'stratum_port': 3333,
        'address_prefix': 'bitcoincash',
        'bech32_hrp': 'bc',
        'pubkey_hash': 0x00,
        'script_hash': 0x05,
        'wif_prefix': 0x80,
        'bip32_public': 0x0488b21e,
        'bip32_private': 0x0488ade4,
        'block_reward': 6.25,  # BCH
        'halving_interval': 210000,
        'magic_bytes': bytes.fromhex('e3e1f3e8'),
        'genesis_hash': '000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f',
        'default_difficulty': 1.0,
        'testnet': False
    },
    'testnet': {
        'name': 'Bitcoin Cash Testnet',
        'rpc_port': 18332,
        'default_rpc_port': 18332,
        'stratum_port': 3333,
        'address_prefix': 'bchtest',
        'bech32_hrp': 'tb',
        'pubkey_hash': 0x6f,
        'script_hash': 0xc4,
        'wif_prefix': 0xef,
        'bip32_public': 0x043587cf,
        'bip32_private': 0x04358394,
        'block_reward': 6.25,
        'halving_interval': 210000,
        'magic_bytes': bytes.fromhex('f4e5f3f4'),
        'genesis_hash': '000000000933ea01ad0ee984209779baaec3ced90fa3f408719526f8d77f4943',
        'default_difficulty': 1.0,
        'testnet': True
    },
    'testnet4': {
        'name': 'Bitcoin Cash Testnet4',
        'rpc_port': 28332,  # Специфичный порт для testnet4
        'default_rpc_port': 18332,
        'stratum_port': 3333,
        'address_prefix': 'bchtest',
        'bech32_hrp': 'tb',
        'pubkey_hash': 0x6f,
        'script_hash': 0xc4,
        'wif_prefix': 0xef,
        'bip32_public': 0x043587cf,
        'bip32_private': 0x04358394,
        'block_reward': 6.25,
        'halving_interval': 210000,
        'magic_bytes': bytes.fromhex('e2b7daaf'),
        'genesis_hash': '000000001dd410c49a788668ce26751718cc797474d3152a5fc073dd44fd9f7b',
        'default_difficulty': 0.001,  # Низкая сложность для тестирования
        'testnet': True
    },
    'regtest': {
        'name': 'Bitcoin Cash Regtest',
        'rpc_port': 18443,
        'default_rpc_port': 18443,
        'stratum_port': 3333,
        'address_prefix': 'bchreg',
        'bech32_hrp': 'bcrt',
        'pubkey_hash': 0x6f,
        'script_hash': 0xc4,
        'wif_prefix': 0xef,
        'bip32_public': 0x043587cf,
        'bip32_private': 0x04358394,
        'block_reward': 6.25,
        'halving_interval': 150,  # Быстрый halving для тестов
        'magic_bytes': bytes.fromhex('fabfb5da'),
        'genesis_hash': '0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206',
        'default_difficulty': 0.0001,  # Очень низкая сложность
        'testnet': True
    }
}


class NetworkManager:
    """Менеджер для работы с разными сетями BCH"""

    def __init__(self, network: str = None):
        self.network = network or self.detect_network()
        self.config = NETWORK_CONFIGS.get(self.network, NETWORK_CONFIGS['testnet4'])

        logger.info(
            "NetworkManager инициализирован",
            event="network_manager_initialized",
            network=self.network,
            network_name=self.config['name']
        )

    @staticmethod
    def detect_network() -> str:
        """Автоматическое определение сети по настройкам"""
        # По умолчанию используем testnet4 для разработки
        default_network = 'testnet4'

        # Пытаемся определить по порту RPC
        rpc_port = getattr(settings, 'bch_rpc_port', 28332)

        port_to_network = {
            8332: 'mainnet',
            18332: 'testnet',
            28332: 'testnet4',
            18443: 'regtest'
        }

        network = port_to_network.get(rpc_port, default_network)

        logger.debug(
            "Определена сеть по порту RPC",
            event="network_detected_by_port",
            rpc_port=rpc_port,
            detected_network=network
        )

        return network

    def get_rpc_url(self, host: str = None) -> str:
        """Получение URL для RPC подключения"""
        host = host or getattr(settings, 'bch_rpc_host', '127.0.0.1')
        port = self.config['rpc_port']
        return f"http://{host}:{port}/"

    def is_testnet(self) -> bool:
        """Проверка, является ли сеть тестовой"""
        return self.config.get('testnet', True)

    def get_address_prefix(self) -> str:
        """Получение префикса адресов для сети"""
        return self.config['address_prefix']

    def get_pubkey_hash_version(self) -> int:
        """Получение версии для P2PKH адресов"""
        return self.config['pubkey_hash']

    def get_script_hash_version(self) -> int:
        """Получение версии для P2SH адресов"""
        return self.config['script_hash']

    def get_block_reward(self, height: int = None) -> float:
        """Получение награды за блок"""
        reward = self.config['block_reward']

        # Рассчитываем halving если указана высота
        if height is not None:
            halving_interval = self.config['halving_interval']
            halvings = height // halving_interval

            # Применяем halving
            for _ in range(halvings):
                reward /= 2

        return reward

    @staticmethod
    def get_satoshis_per_bch() -> int:
        """Получение количества сатоши в одном BCH"""
        return SATOSHIS_PER_BCH

    @staticmethod
    def bch_to_satoshis(bch_amount: float) -> int:
        """Конвертация BCH в сатоши"""
        return int(bch_amount * SATOSHIS_PER_BCH)

    @staticmethod
    def satoshis_to_bch(satoshis: int) -> float:
        """Конвертация сатоши в BCH"""
        return satoshis / SATOSHIS_PER_BCH

    def get_fallback_coinbase_value(self) -> int:
        """Получение fallback значения coinbase"""
        # Если в настройках указано значение, используем его
        if hasattr(settings, 'fallback_coinbase_value'):
            return settings.fallback_coinbase_value

        # Иначе рассчитываем из награды за блок
        reward_bch = self.get_block_reward()
        return self.bch_to_satoshis(reward_bch)

    @staticmethod
    def get_coinbase_prefix() -> bytes:
        """Получение префикса для ScriptSig coinbase"""
        prefix = getattr(settings, 'coinbase_prefix', '/BCHPool/')
        return prefix.encode('utf-8')

    @staticmethod
    def get_max_script_sig_size() -> int:
        """Получение максимального размера ScriptSig"""
        return getattr(settings, 'max_script_sig_size', 100)

    @staticmethod
    def get_default_block_version() -> int:
        """Получение версии блока по умолчанию"""
        return getattr(settings, 'block_version', 0x20000000)

    @staticmethod
    def get_default_bits() -> str:
        """Получение bits по умолчанию"""
        return getattr(settings, 'block_bits', '1d00ffff')

    @staticmethod
    def get_fallback_prev_block_hash() -> str:
        """Получение fallback хэша предыдущего блока"""
        return getattr(settings, 'fallback_prev_block_hash',
                       '000000000000000007cbc708a5e00de8fd5e4b5b3e2a4f61c5aec6d6b7a9b8c9')

    @staticmethod
    def get_fallback_difficulty() -> float:
        """Получение fallback сложности"""
        return getattr(settings, 'fallback_difficulty', 0.001)

    def format_satoshis(self, satoshis: int) -> str:
        """Форматирование сатоши в читаемый вид"""
        bch = self.satoshis_to_bch(satoshis)
        return f"{bch:.8f} BCH ({satoshis:,} satoshis)"

    def calculate_block_subsidy(self, height: int) -> int:
        """Расчет субсидии за блок (без комиссий)"""
        reward_bch = self.get_block_reward(height)
        return self.bch_to_satoshis(reward_bch)

    def validate_address_for_network(self, address: str) -> bool:
        """Валидация адреса для текущей сети"""
        from app.utils.cashaddr import BCHAddressUtils

        is_valid, info = BCHAddressUtils.validate(address)
        if not is_valid:
            return False

        # Проверяем, что адрес соответствует текущей сети
        if ':' in address.lower():
            prefix = address.lower().split(':')[0]
            expected_prefix = self.get_address_prefix()
            return prefix == expected_prefix
        else:
            # Для legacy адресов проверяем версию

            try:
                decoded = base58.b58decode_check(address.lower())
                version = decoded[0]

                if self.is_testnet():
                    return version in [self.config['pubkey_hash'], self.config['script_hash']]
                else:
                    return version in [0x00, 0x05]  # Mainnet версии
            except Exception as e:
                logger.error(f"Ошибка валидации адреса {address}: {e}")
                return False


def get_network_manager(network: str = None) -> NetworkManager:
    """Получение NetworkManager для указанной сети"""
    return NetworkManager(network)

def get_network_info(self) -> Dict[str, Any]:
        """Получение информации о сети"""
        return {
            'name': self.config['name'],
            'network': self.network,
            'rpc_port': self.config['rpc_port'],
            'stratum_port': self.config['stratum_port'],
            'address_prefix': self.config['address_prefix'],
            'block_reward': self.config['block_reward'],
            'default_difficulty': self.config['default_difficulty'],
            'is_testnet': self.config['testnet'],
            'genesis_hash': self.config['genesis_hash']
        }



