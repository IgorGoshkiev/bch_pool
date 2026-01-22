# app/utils/logging_config.py
"""
Конфигурация логирования для приложения
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
import json
from datetime import datetime, UTC
from typing import Dict, Any, Optional

from app.utils.config import settings


class JSONFormatter(logging.Formatter):
    """Форматировщик логов в JSON"""

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Добавляем дополнительные поля
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Добавляем extra поля если они есть
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in log_record and not key.startswith('_'):
                    log_record[key] = value

        return json.dumps(log_record, ensure_ascii=False, default=str)


class ColorFormatter(logging.Formatter):
    """Цветной форматировщик для консоли"""

    COLORS = {
        'DEBUG': '\033[36m',  # Cyan
        'INFO': '\033[32m',  # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',  # Red
        'CRITICAL': '\033[41m',  # Red background
        'RESET': '\033[0m',  # Reset
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # Форматируем время
        log_time = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')

        # Форматируем сообщение
        message = super().format(record)

        # Добавляем цвет
        return f"{log_time} {color}{record.levelname:8s}{reset} [{record.name}] {message}"


def setup_logging():
    """
    Настройка логирования для приложения
    """
    # Создаем директорию для логов
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Базовый логгер
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Удаляем существующие обработчики
    logger.handlers.clear()

    # Консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    console_formatter = ColorFormatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # Файловый обработчик (ротация по размеру)
    file_handler = RotatingFileHandler(
        filename=log_dir / "bch_pool.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = JSONFormatter()
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Обработчик ошибок (отдельный файл)
    error_handler = RotatingFileHandler(
        filename=log_dir / "errors.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.WARNING)
    error_formatter = JSONFormatter()
    error_handler.setFormatter(error_formatter)
    logger.addHandler(error_handler)

    # Настраиваем логи для внешних библиотек
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    logger.info("Логирование настроено")

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Получение логгера с заданным именем

    Args:
        name: Имя логгера

    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)


class StructuredLogger:
    """
    Логгер для структурированного логирования
    """

    def __init__(self, name: str):
        self.logger = get_logger(name)

    def _log_with_context(self, level: str, msg: str, **kwargs):
        """Логирование с дополнительным контекстом"""
        # Используем extra для передачи дополнительных полей
        extra = kwargs.copy()

        # Создаем запись
        if level == "DEBUG":
            self.logger.debug(msg, extra=extra, stacklevel=2)
        elif level == "INFO":
            self.logger.info(msg, extra=extra, stacklevel=2)
        elif level == "WARNING":
            self.logger.warning(msg, extra=extra, stacklevel=2)
        elif level == "ERROR":
            self.logger.error(msg, extra=extra, stacklevel=2)
        elif level == "CRITICAL":
            self.logger.critical(msg, extra=extra, stacklevel=2)

    def info(self, msg: str, **kwargs):
        """Логирование уровня INFO"""
        self._log_with_context("INFO", msg, **kwargs)

    def debug(self, msg: str, **kwargs):
        """Логирование уровня DEBUG"""
        self._log_with_context("DEBUG", msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        """Логирование уровня WARNING"""
        self._log_with_context("WARNING", msg, **kwargs)

    def error(self, msg: str, **kwargs):
        """Логирование уровня ERROR"""
        self._log_with_context("ERROR", msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        """Логирование уровня CRITICAL"""
        self._log_with_context("CRITICAL", msg, **kwargs)

    def miner_connected(self, miner_address: str, connection_type: str, **kwargs):
        """Логирование подключения майнера"""
        self.info(f"Майнер подключился: {miner_address}",
                  event="miner_connected",
                  miner_address=miner_address,
                  connection_type=connection_type,
                  **kwargs)

    def miner_disconnected(self, miner_address: str, connection_type: str, **kwargs):
        """Логирование отключения майнера"""
        self.info(f"Майнер отключился: {miner_address}",
                  event="miner_disconnected",
                  miner_address=miner_address,
                  connection_type=connection_type,
                  **kwargs)

    def share_submitted(self, miner_address: str, job_id: str, is_valid: bool, **kwargs):
        """Логирование отправки шара"""
        if is_valid:
            self.info(f"Валидный шар от майнера: {miner_address}",
                      event="share_submitted",
                      miner_address=miner_address,
                      job_id=job_id,
                      is_valid=is_valid,
                      **kwargs)
        else:
            self.warning(f"Невалидный шар от майнера: {miner_address}",
                         event="share_submitted",
                         miner_address=miner_address,
                         job_id=job_id,
                         is_valid=is_valid,
                         **kwargs)

    def job_created(self, job_id: str, job_type: str, miner_address: Optional[str] = None, **kwargs):
        """Логирование создания задания"""
        self.info(f"Задание создано: {job_id}",
                  event="job_created",
                  job_id=job_id,
                  job_type=job_type,
                  miner_address=miner_address,
                  **kwargs)

    def block_found(self, height: int, block_hash: str, miner_address: str, **kwargs):
        """Логирование найденного блока"""
        self.info(f"Блок найден! Высота: {height}, майнер: {miner_address}",
                  event="block_found",
                  height=height,
                  block_hash=block_hash,
                  miner_address=miner_address,
                  **kwargs)

