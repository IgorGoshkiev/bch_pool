"""
Управление жизненным циклом приложения
"""

from contextlib import asynccontextmanager
import asyncio
import logging

from app.dependencies import job_manager, stratum_server, tcp_stratum_server, share_validator
from app.utils.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app):
    """
    Lifespan менеджер для управления событиями запуска/остановки приложения.
    """
    # ========== STARTUP ==========
    logger.info("Запуск BCH Solo Pool...")

    # Фоновые задачи
    background_tasks = []

    try:
        # 1. Устанавливаем связь между менеджерами
        job_manager.set_stratum_server(stratum_server)

        # Устанавливаем JobManager в stratum_server
        stratum_server.set_job_manager(job_manager)

        # 2. Инициализируем JobManager
        if await job_manager.initialize():
            logger.info("JobManager готов к работе")
            await job_manager.broadcast_new_job_to_all()
            logger.info("Первое задание создано")
        else:
            logger.error("Ошибка инициализации JobManager")

        # 3. Запускаем TCP Stratum сервер в фоне (если включен)
        if settings.stratum_tcp_enabled:
            tcp_task = asyncio.create_task(tcp_stratum_server.start())
            background_tasks.append(tcp_task)
            logger.info(f"TCP Stratum сервер запущен на порту {tcp_stratum_server.port}")

        # 4. Запускаем периодическую рассылку заданий
        broadcast_task = asyncio.create_task(_periodic_job_broadcaster())
        background_tasks.append(broadcast_task)
        logger.info("Периодическая рассылка заданий запущена")

        # 5. Запускаем очистку старых заданий
        cleanup_task = asyncio.create_task(_periodic_job_cleanup())
        background_tasks.append(cleanup_task)
        logger.info("Периодическая очистка заданий запущена")

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}")

    # Передаем управление приложению
    yield

    # ========== SHUTDOWN ==========
    logger.info("Остановка BCH Solo Pool...")

    # Останавливаем все фоновые задачи
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Ошибка при остановке задачи: {e}")

    # Останавливаем TCP сервер
    try:
        await tcp_stratum_server.stop()
        logger.info("TCP Stratum сервер остановлен")
    except Exception as e:
        logger.warning(f"Ошибка остановки TCP сервера: {e}")

    # Очищаем данные WebSocket сервера
    stratum_server.cleanup_all()
    logger.info("WebSocket сервер очищен")

    logger.info("Остановка завершена")


async def _periodic_job_broadcaster():
    """Периодическая рассылка новых заданий"""
    while True:
        try:
            await asyncio.sleep(settings.job_broadcast_interval)

            # Проверяем есть ли активные майнеры
            active_miners = (
                    len(stratum_server.active_connections) +
                    len(tcp_stratum_server.connections)
            )

            if active_miners > 0:
                await job_manager.broadcast_new_job_to_all()
                logger.debug(f"Задание разослано {active_miners} майнерам")
            else:
                logger.debug("Нет активных майнеров, пропускаем рассылку")

        except asyncio.CancelledError:
            logger.info("Задача рассылки заданий остановлена")
            break
        except Exception as e:
            logger.error(f"Ошибка в периодической рассылке: {e}")
            await asyncio.sleep(5)


async def _periodic_job_cleanup():
    """Периодическая очистка старых заданий"""
    while True:
        try:
            await asyncio.sleep(60)

            # Очищаем задания в WebSocket сервере
            stratum_server.cleanup_old_jobs(max_age_seconds=settings.job_cleanup_age)

            # Очищаем задания в валидаторе
            if hasattr(share_validator, 'cleanup_old_jobs'):
                share_validator.cleanup_old_jobs(max_age_seconds=settings.job_cleanup_age)

            logger.debug("Очистка старых заданий выполнена")

        except asyncio.CancelledError:
            logger.info("Задача очистки заданий остановлена")
            break
        except Exception as e:
            logger.error(f"Ошибка в очистке заданий: {e}")