"""
Pydantic схемы для валидации данных - версия для Pydantic V2
"""
from datetime import datetime, UTC
from typing import Optional, List, Dict, Any, ClassVar, TYPE_CHECKING
from pydantic import BaseModel, Field, field_validator, ConfigDict, StringConstraints
from typing_extensions import Annotated
import re


# ========== БАЗОВЫЕ СХЕМЫ ==========
class PaginationParams(BaseModel):
    """Параметры пагинации"""
    skip: int = Field(default=0, ge=0, description="Количество записей для пропуска")
    limit: int = Field(default=100, ge=1, le=1000, description="Количество записей на странице")

    model_config = ConfigDict(from_attributes=True)


class TimeRangeParams(BaseModel):
    """Параметры временного диапазона"""
    time_range: str = Field(default="24h", description="Диапазон: 1h, 24h, 7d, 30d, all")

    @classmethod
    @field_validator('time_range')
    def validate_time_range(cls, v: str) -> str:
        """Валидатор для time_range"""
        valid_ranges = ["1h", "24h", "7d", "30d", "all"]
        if v not in valid_ranges:
            raise ValueError(f"time_range must be one of {valid_ranges}")
        return v

    model_config = ConfigDict(from_attributes=True)


# ========== МАЙНЕРЫ ==========
class MinerBase(BaseModel):
    """Базовая схема майнера"""
    bch_address: Annotated[str, StringConstraints(min_length=10, max_length=128)] = Field(
        description="BCH адрес для выплат"
    )
    worker_name: str = Field(default="default", min_length=1, max_length=64, description="Имя воркера")

    model_config = ConfigDict(from_attributes=True)


class MinerCreate(MinerBase):
    """Схема для создания майнера"""
    pass


class MinerUpdate(BaseModel):
    """Схема для обновления майнера"""
    worker_name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    is_active: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class MinerResponse(MinerBase):
    """Схема ответа с данными майнера"""
    id: int
    is_active: bool
    total_shares: int
    total_blocks: int
    hashrate: float
    registered_at: datetime


class MinerStatsResponse(BaseModel):
    """Статистика майнера"""
    miner: MinerResponse
    time_range: Dict[str, str]
    statistics: Dict[str, Any]
    recent_activity: Dict[str, Optional[str]]

    model_config = ConfigDict(from_attributes=True)


# ========== ШАРЫ (SHARES) ==========
class ShareBase(BaseModel):
    """Базовая схема шара"""
    miner_address: str
    job_id: str
    extra_nonce2: Optional[str] = None
    ntime: Optional[str] = None
    nonce: Optional[str] = None
    difficulty: float = Field(default=1.0)
    is_valid: bool = Field(default=True)

    model_config = ConfigDict(from_attributes=True)


class ShareCreate(ShareBase):
    """Схема для создания шара"""
    pass


class ShareResponse(ShareBase):
    """Схема ответа с данными шара"""
    id: int
    submitted_at: datetime


# ========== БЛОКИ ==========
class BlockBase(BaseModel):
    """Базовая схема блока"""
    height: int
    hash: str = Field(pattern=r'^[a-fA-F0-9]{64}$')
    miner_address: str
    confirmed: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)


class BlockCreate(BlockBase):
    """Схема для создания блока"""
    pass


class BlockResponse(BlockBase):
    """Схема ответа с данными блока"""
    id: int
    found_at: datetime


# ========== API ОТВЕТЫ С ДЕТАЛИЗАЦИЕЙ ==========
class ApiResponse(BaseModel):
    """Базовая схема ответа API"""
    status: str = Field(..., description="Статус операции: success, error, warning")
    message: str = Field(..., description="Сообщение для пользователя")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Данные ответа")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Временная метка")
    request_id: Optional[str] = Field(default=None, description="Идентификатор запроса (для трассировки)")

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def success(cls, message: str, data: Optional[Dict[str, Any]] = None) -> "ApiResponse":
        """Создание успешного ответа"""
        return cls(status="success", message=message, data=data)

    @classmethod
    def error(cls, message: str, data: Optional[Dict[str, Any]] = None) -> "ApiResponse":
        """Создание ответа об ошибке"""
        return cls(status="error", message=message, data=data)

    @classmethod
    def warning(cls, message: str, data: Optional[Dict[str, Any]] = None) -> "ApiResponse":
        """Создание ответа с предупреждением"""
        return cls(status="warning", message=message, data=data)


class PaginatedResponse(BaseModel):
    """Схема для пагинированных ответов"""
    items: List[Any]
    total: int
    page: int
    size: int
    pages: int

    model_config = ConfigDict(from_attributes=True)


class HealthCheckResponse(BaseModel):
    """Схема для проверки здоровья"""
    service: str
    status: str
    version: str
    timestamp: datetime
    dependencies: Optional[Dict[str, str]] = None

    model_config = ConfigDict(from_attributes=True)


# ========== ВАЛИДАТОРЫ ДЛЯ КАСТОМНЫХ ТИПОВ ==========
class BCHAddressValidator:
    """Валидатор BCH адресов"""

    TESTNET_PREFIXES: ClassVar[List[str]] = ['bchtest:', 'qq', 'qp']

    @classmethod
    def validate(cls, address: str) -> bool:
        """Валидация BCH адреса"""
        return any(address.startswith(prefix) for prefix in cls.TESTNET_PREFIXES)

    @classmethod
    def __get_validators__(cls):
        """Для использования с Pydantic"""
        yield cls.validate_field

    @classmethod
    def validate_field(cls, v: str) -> str:
        if not cls.validate(v):
            raise ValueError(f"Invalid BCH testnet address: {v}")
        return v

class HexStringValidator:
    """Валидатор hex строк"""

    HEX_PATTERN: ClassVar[re.Pattern] = re.compile(r'^[0-9a-fA-F]+$')

    @classmethod
    def validate(cls, hex_str: str) -> bool:
        """Валидация hex строки"""
        return bool(cls.HEX_PATTERN.match(hex_str))

    @classmethod
    def __get_validators__(cls):
        """Для использования с Pydantic"""
        yield cls.validate_field

    @classmethod
    def validate_field(cls, v: str) -> str:
        if not cls.validate(v):
            raise ValueError(f"Invalid hex string: {v}")
        return v.lower()


class JobIdValidator:
    """Валидатор ID заданий"""

    @classmethod
    def validate(cls, job_id: str) -> bool:
        """Валидация ID задания"""
        return job_id.startswith('job_')

    @classmethod
    def __get_validators__(cls):
        """Для использования с Pydantic"""
        yield cls.validate_field

    @classmethod
    def validate_field(cls, v: str) -> str:
        if not cls.validate(v):
            raise ValueError(f"Invalid job ID format: {v}")
        return v




# Типы для использования в других схемах
if TYPE_CHECKING:
    BCHAddress = str
    HexString = str
    JobId = str
else:
    BCHAddress = Annotated[str, Field(..., description="BCH адрес"), BCHAddressValidator]
    HexString = Annotated[str, Field(..., description="Hex строка"), HexStringValidator]
    JobId = Annotated[str, Field(..., description="ID задания"), JobIdValidator]