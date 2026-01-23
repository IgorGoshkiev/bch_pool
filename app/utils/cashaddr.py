"""
Реализация CashAddr для Bitcoin Cash (BCH)
Основано на спецификации: https://github.com/bitcoincashorg/bitcoincash.org/blob/master/spec/cashaddr.md
"""
import hashlib
import base58
from typing import Tuple, Optional, List
from app.utils.logging_config import StructuredLogger
from app.utils.network_config import NETWORK_CONFIGS

logger = StructuredLogger(__name__)

# Константы CashAddr
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHECKSUM_CONST = 1
GENERATOR = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470]

# Типы адресов
ADDRESS_TYPES = {
    'P2KH': 0,  # Pay to Public Key Hash
    'P2SH': 1  # Pay to Script Hash
}

# Префиксы сетей
NETWORK_PREFIXES = {
    'mainnet': 'bitcoincash',
    'testnet': 'bchtest',
    'regtest': 'bchreg'
}


class CashAddr:
    """Класс для работы с CashAddr адресами Bitcoin Cash"""

    @staticmethod
    def polymod(values: List[int]) -> int:
        """Полиномиальная функция для расчета checksum"""
        chk = 1
        for value in values:
            top = chk >> 35
            chk = ((chk & 0x07ffffffff) << 5) ^ value
            for i in range(5):
                if (top >> i) & 1:
                    chk ^= GENERATOR[i]
        return chk ^ 1

    @staticmethod
    def expand_prefix(prefix: str) -> List[int]:
        """Расширение префикса для checksum"""
        return [ord(x) & 0x1f for x in prefix] + [0]

    @staticmethod
    def calculate_checksum(prefix: str, payload: List[int]) -> List[int]:
        """Расчет checksum для CashAddr"""
        poly = CashAddr.polymod(CashAddr.expand_prefix(prefix) + payload + [0, 0, 0, 0, 0, 0, 0, 0])
        checksum = []
        for i in range(8):
            checksum.append((poly >> (5 * (7 - i))) & 0x1f)
        return checksum

    @staticmethod
    def verify_checksum(prefix: str, payload: List[int]) -> bool:
        """Проверка checksum CashAddr"""
        poly = CashAddr.polymod(CashAddr.expand_prefix(prefix) + payload)
        return poly == CHECKSUM_CONST

    @staticmethod
    def encode(prefix: str, payload: List[int]) -> str:
        """Кодирование в CashAddr"""
        checksum = CashAddr.calculate_checksum(prefix, payload)
        combined = payload + checksum
        result = prefix + ':'

        for byte in combined:
            result += CHARSET[byte]

        return result

    @staticmethod
    def decode(address: str) -> Tuple[str, List[int]]:
        """Декодирование CashAddr адреса"""
        if ':' not in address:
            raise ValueError(f"Invalid CashAddr format: {address}")

        prefix, encoded = address.split(':')

        # Проверяем префикс
        if prefix not in NETWORK_PREFIXES.values():
            raise ValueError(f"Unknown prefix: {prefix}")

        # Декодируем payload
        payload = []
        for char in encoded.lower():
            if char not in CHARSET:
                raise ValueError(f"Invalid character in address: {char}")
            payload.append(CHARSET.index(char))

        # Проверяем checksum
        if not CashAddr.verify_checksum(prefix, payload[:-8]):
            raise ValueError(f"Invalid checksum for address: {address}")

        # Возвращаем без checksum
        return prefix, payload[:-8]

    @staticmethod
    def convert_bits(data: List[int], from_bits: int, to_bits: int, pad: bool = True) -> List[int]:
        """Конвертация между битовыми представлениями"""
        acc = 0
        bits = 0
        ret = []
        maxv = (1 << to_bits) - 1
        max_acc = (1 << (from_bits + to_bits - 1)) - 1

        for value in data:
            if value < 0 or (value >> from_bits):
                raise ValueError(f"Invalid value: {value}")

            acc = ((acc << from_bits) | value) & max_acc
            bits += from_bits

            while bits >= to_bits:
                bits -= to_bits
                ret.append((acc >> bits) & maxv)

        if pad:
            if bits:
                ret.append((acc << (to_bits - bits)) & maxv)
        elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
            raise ValueError("Invalid padding")

        return ret

    @staticmethod
    def decode_address(address: str) -> Tuple[str, str, bytes]:
        """Полное декодирование CashAddr адреса"""
        try:
            prefix, payload = CashAddr.decode(address)

            # Конвертируем из 5-битного в 8-битное представление
            decoded = CashAddr.convert_bits(payload, 5, 8, False)

            if not decoded:
                raise ValueError("Empty payload")

            # Первый байт - тип адреса и длина
            version_byte = decoded[0]
            address_type = (version_byte >> 3) & 0x1f
            hash_size = version_byte & 0x07

            # Проверяем тип адреса
            if address_type not in [ADDRESS_TYPES['P2KH'], ADDRESS_TYPES['P2SH']]:
                raise ValueError(f"Unsupported address type: {address_type}")

            # Проверяем размер хэша
            expected_sizes = {ADDRESS_TYPES['P2KH']: 20, ADDRESS_TYPES['P2SH']: 20}
            if hash_size != 0 or len(decoded[1:]) != expected_sizes[address_type]:
                raise ValueError(f"Invalid hash size: {len(decoded[1:])}")

            hash_bytes = bytes(decoded[1:])
            address_type_name = 'P2KH' if address_type == ADDRESS_TYPES['P2KH'] else 'P2SH'

            return prefix, address_type_name, hash_bytes  # <-- Теперь типы совпадают

        except Exception as e:
            logger.error(f"Error decoding address {address}: {e}")
            raise

    @staticmethod
    def encode_address(prefix: str, address_type: str, hash_bytes: bytes) -> str:
        """Кодирование в CashAddr"""
        if address_type not in ADDRESS_TYPES:
            raise ValueError(f"Unsupported address type: {address_type}")

        if len(hash_bytes) != 20:
            raise ValueError(f"Invalid hash length: {len(hash_bytes)}")

        # Создаем version byte
        version_byte = (ADDRESS_TYPES[address_type] << 3) | 0

        # Подготавливаем данные
        data = [version_byte] + list(hash_bytes)

        # Конвертируем в 5-битное представление
        converted = CashAddr.convert_bits(data, 8, 5, True)

        # Кодируем в CashAddr
        return CashAddr.encode(prefix, converted)

    @staticmethod
    def to_legacy_format(cash_addr: str) -> str:
        """Конвертация CashAddr в legacy формат"""

        prefix, address_type, hash_bytes = CashAddr.decode_address(cash_addr)

        # Определяем версию для legacy формата
        version_map = {
            ('bitcoincash', 'P2KH'): 0x00,  # 1...
            ('bitcoincash', 'P2SH'): 0x05,  # 3...
            ('bchtest', 'P2KH'): 0x6f,  # m/n...
            ('bchtest', 'P2SH'): 0xc4,  # 2...
            ('bchreg', 'P2KH'): 0x6f,  # regtest использует testnet версии
            ('bchreg', 'P2SH'): 0xc4,
        }

        key = (prefix, address_type)
        if key not in version_map:
            # По умолчанию используем testnet P2KH
            version = 0x6f
            logger.warning(
                f"Неизвестная комбинация префикса и типа: {key}",
                event="cashaddr_unknown_prefix_type",
                prefix=prefix,
                address_type=address_type
            )
        else:
            version = version_map[key]

        # Создаем legacy адрес
        data = bytes([version]) + hash_bytes
        checksum = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
        legacy = base58.b58encode(data + checksum)

        return legacy.decode('utf-8')

    @staticmethod
    def from_legacy_format(legacy_addr: str) -> str:
        """Конвертация legacy формата в CashAddr"""
        import base58

        # Декодируем legacy адрес
        decoded = base58.b58decode_check(legacy_addr)
        if len(decoded) != 21:  # 1 byte version + 20 bytes hash
            raise ValueError(f"Invalid legacy address length: {len(decoded)}")

        version = decoded[0]
        hash_bytes = decoded[1:]

        # Определяем тип адреса и сеть по версии
        version_map = {
            0x00: ('bitcoincash', 'P2KH'),  # Mainnet P2KH
            0x05: ('bitcoincash', 'P2SH'),  # Mainnet P2SH
            0x6f: ('bchtest', 'P2KH'),  # Testnet P2KH
            0xc4: ('bchtest', 'P2SH'),  # Testnet P2SH
            # Регтест использует те же версии что и тестнет
        }

        if version in version_map:
            prefix, address_type = version_map[version]
        else:
            # Для неизвестных версий используем testnet P2KH
            logger.warning(
                f"Неизвестная версия legacy адреса: {version:#04x}",
                event="cashaddr_unknown_legacy_version",
                version=version,
                legacy_addr=legacy_addr[:20] + "..."
            )
            prefix = 'bchtest'
            address_type = 'P2KH'

        # Кодируем в CashAddr
        return CashAddr.encode_address(prefix, address_type, hash_bytes)


class BCHAddressUtils:
    """Утилиты для работы с BCH адресами"""

    @staticmethod
    def validate(address: str, network: str = None) -> Tuple[bool, str]:
        """Валидация BCH адреса с определением типа"""
        try:
            if not address:
                return False, "Empty address"

            address_lower = address.lower()

            # Проверяем CashAddr формат
            if ':' in address_lower:
                try:
                    prefix, address_type, _ = CashAddr.decode_address(address_lower)

                    # Проверяем сеть если указана
                    if network:
                        expected_prefix = None
                        for net_name, config in NETWORK_CONFIGS.items():
                            if net_name == network:
                                expected_prefix = config['address_prefix']
                                break

                        if expected_prefix and prefix != expected_prefix:
                            return False, f"Wrong network. Expected {expected_prefix}, got {prefix}"

                    return True, f"CashAddr {address_type} ({prefix})"
                except Exception as e:
                    return False, f"Invalid CashAddr: {str(e)}"

            # Проверяем legacy формат
            else:
                try:
                    decoded = base58.b58decode_check(address_lower)
                    if len(decoded) != 21:
                        return False, f"Invalid length: {len(decoded)}"

                    version = decoded[0]

                    # Определяем тип по версии
                    version_to_network = {
                        0x00: ("Legacy P2KH", "mainnet"),
                        0x05: ("Legacy P2SH", "mainnet"),
                        0x6f: ("Legacy P2KH", "testnet"),
                        0xc4: ("Legacy P2SH", "testnet")
                    }

                    if version in version_to_network:
                        addr_type, addr_network = version_to_network[version]

                        # Проверяем сеть если указана - ПАРАМЕТР ИСПОЛЬЗУЕТСЯ!
                        if network and addr_network != network:
                            return False, f"Wrong network. Expected {network}, got {addr_network}"

                        return True, f"{addr_type} ({addr_network})"
                    else:
                        return True, f"Legacy unknown (version: {version:#04x})"

                except Exception as e:
                    return False, f"Invalid legacy address: {str(e)}"

        except Exception as e:
            logger.error(f"Error validating address {address}: {e}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def normalize(address: str, target_format: str = 'cashaddr') -> Optional[str]:
        """Нормализация адреса в указанный формат"""
        try:
            is_valid, info = BCHAddressUtils.validate(address)
            if not is_valid:
                return None

            # Если адрес уже в нужном формате
            if target_format == 'cashaddr' and ':' in address.lower():
                return address
            elif target_format == 'legacy' and ':' not in address.lower():
                return address

            # Конвертируем
            if ':' in address.lower():
                # CashAddr -> Legacy
                if target_format == 'legacy':
                    return CashAddr.to_legacy_format(address)
            else:
                # Legacy -> CashAddr
                if target_format == 'cashaddr':
                    return CashAddr.from_legacy_format(address)

            return None

        except Exception as e:
            logger.error(f"Error normalizing address {address}: {e}")
            return None


    @staticmethod
    def extract_pubkey_hash(address: str) -> Optional[bytes]:
        """Извлечение pubkey hash из любого формата адреса"""
        try:
            if ':' in address.lower():
                # CashAddr формат
                _, address_type, hash_bytes = CashAddr.decode_address(address)
                if address_type == 'P2KH':
                    return hash_bytes
            else:
                # Legacy формат
                decoded = base58.b58decode_check(address.lower())
                version = decoded[0]

                # Только P2KH адреса
                if version in [0x00, 0x6f]:  # Mainnet и testnet P2KH
                    return decoded[1:]

            return None

        except Exception as e:
            logger.error(f"Error extracting pubkey hash from {address}: {e}")
            return None

    @staticmethod
    def detect_network(address: str) -> Optional[str]:
        """Определение сети по адресу"""
        try:
            if ':' in address.lower():
                # CashAddr формат
                prefix, _, _ = CashAddr.decode_address(address)

                # Находим сеть по префиксу
                for network, net_prefix in NETWORK_PREFIXES.items():
                    if prefix == net_prefix:
                        return network

                return None
            else:
                # Legacy формат
                decoded = base58.b58decode_check(address.lower())
                version = decoded[0]

                # Определяем сеть по версии
                if version in [0x00, 0x05]:
                    return 'mainnet'
                elif version in [0x6f, 0xc4]:
                    return 'testnet'
                else:
                    return None


        except Exception as e:
            logger.error(f"Error detect network {address}: {e}")
            return None