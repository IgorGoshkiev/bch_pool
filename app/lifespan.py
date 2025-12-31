from contextlib import asynccontextmanager
from fastapi import FastAPI
import asyncio
import logging

# Импортируем из dependencies
from app.dependencies import job_manager, stratum_server

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan менеджер для управления событиями запуска/остановки приложения.
    Заменяет устаревшие @app.on_event("startup") и @app.on_event("shutdown")
    """

    # ========== STARTUP ==========
    logger.info("Запуск BCH Solo Pool...")

    try:
        # Устанавливаем связь между менеджерами
        job_manager.set_stratum_server(stratum_server)

        # Инициализируем JobManager
        if await job_manager.initialize():
            logger.info("JobManager готов к работе")

            # Создаем первое общее задание
            await job_manager.broadcast_new_job_to_all()
            logger.info("Первое задание создано и разослано")

            # Запускаем периодическую рассылку заданий в фоне
            broadcast_task = asyncio.create_task(_periodic_job_broadcaster())
            logger.info("Периодическая рассылка заданий запущена")
        else:
            logger.error("Ошибка инициализации JobManager")
            broadcast_task = None

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}")
        broadcast_task = None

    # Передаем управление приложению
    yield

    # ========== SHUTDOWN ==========
    logger.info("Остановка BCH Solo Pool...")

    # Отменяем задачу рассылки если она была создана
    if broadcast_task:
        broadcast_task.cancel()
        try:
            await broadcast_task
        except asyncio.CancelledError:
            logger.info("Задача рассылки заданий остановлена")

    # Очищаем данные Stratum сервера
    stratum_server.cleanup_all()
    logger.info("Очистка завершена")


async def _periodic_job_broadcaster():
    """Периодическая рассылка новых заданий"""
    while True:
        try:
            await asyncio.sleep(30)  # Каждые 30 секунд

            # Проверяем есть ли активные майнеры
            if stratum_server.active_connections:
                await job_manager.broadcast_new_job_to_all()
            else:
                logger.debug("Нет активных майнеров, пропускаем рассылку")

        except asyncio.CancelledError:
            # Задача была отменена при остановке
            logger.info("Задача рассылки заданий остановлена")
            break
        except Exception as e:
            logger.error(f"Ошибка в периодической рассылке: {e}")
            await asyncio.sleep(5)  # Пауза при ошибке