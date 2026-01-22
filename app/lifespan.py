"""
Управление жизненным циклом приложения
"""
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger
from app.dependencies import job_manager, stratum_server, tcp_stratum_server, share_validator
from app.utils.config import settings

logger = StructuredLogger(__name__)


@asynccontextmanager
async def lifespan(_app):
    """
    Lifespan менеджер для управления событиями запуска/остановки приложения.
    """
    startup_time = datetime.now(UTC)

    # ========== STARTUP ==========
    logger.info(
        "Запуск BCH Solo Pool...",
        event="app_startup",
        startup_time=startup_time.isoformat(),
        settings={
            "stratum_tcp_enabled": settings.stratum_tcp_enabled,
            "job_broadcast_interval": settings.job_broadcast_interval,
            "job_cleanup_age": settings.job_cleanup_age
        }
    )

    # Фоновые задачи
    background_tasks = []

    try:
        # 1. Инициализируем JobManager
        logger.info("Инициализация JobManager...", event="job_manager_initializing")
        if await job_manager.initialize():
            logger.info(
                "JobManager готов к работе",
                event="job_manager_initialized",
                block_height=getattr(job_manager, 'block_height', 0),
                node_connection=f"{settings.bch_rpc_host}:{settings.bch_rpc_port}"
            )

            # 2. Создаем первое задание
            await job_manager.broadcast_new_job_to_all()
            logger.info(
                "Первое задание создано",
                event="first_job_created",
                job_counter=getattr(job_manager, 'job_counter', 0)
            )
        else:
            logger.error(
                "Ошибка инициализации JobManager",
                event="job_manager_init_failed",
                error_details="Не удалось подключиться к BCH ноде"
            )
            # Можно продолжить работу в fallback режиме

        # 3. Запускаем TCP Stratum сервер в фоне (если включен)
        if settings.stratum_tcp_enabled:
            try:
                tcp_task = asyncio.create_task(tcp_stratum_server.start())
                background_tasks.append(tcp_task)
                logger.info(
                    f"TCP Stratum сервер запущен на порту {tcp_stratum_server.port}",
                    event="tcp_server_started",
                    host=tcp_stratum_server.host,
                    port=tcp_stratum_server.port,
                    task_id=id(tcp_task)
                )
            except Exception as e:
                logger.error(
                    "Ошибка запуска TCP сервера",
                    event="tcp_server_start_failed",
                    error=str(e),
                    host=tcp_stratum_server.host,
                    port=tcp_stratum_server.port
                )

        # 4. Запускаем периодическую рассылку заданий
        try:
            broadcast_task = asyncio.create_task(_periodic_job_broadcaster())
            background_tasks.append(broadcast_task)
            logger.info(
                "Периодическая рассылка заданий запущена",
                event="job_broadcaster_started",
                interval_seconds=settings.job_broadcast_interval,
                task_id=id(broadcast_task)
            )
        except Exception as e:
            logger.error(
                "Ошибка запуска рассылки заданий",
                event="job_broadcaster_start_failed",
                error=str(e)
            )

        # 5. Запускаем очистку старых заданий
        try:
            cleanup_task = asyncio.create_task(_periodic_job_cleanup())
            background_tasks.append(cleanup_task)
            logger.info(
                "Периодическая очистка заданий запущена",
                event="job_cleanup_started",
                interval_seconds=60,
                task_id=id(cleanup_task)
            )
        except Exception as e:
            logger.error(
                "Ошибка запуска очистки заданий",
                event="job_cleanup_start_failed",
                error=str(e)
            )

        # Логируем успешный запуск
        startup_duration = (datetime.now(UTC) - startup_time).total_seconds()
        logger.info(
            "BCH Solo Pool успешно запущен",
            event="app_startup_completed",
            startup_duration_seconds=startup_duration,
            background_tasks_count=len(background_tasks)
        )

    except Exception as e:
        logger.error(
            "Критическая ошибка при запуске приложения",
            event="app_startup_failed",
            error=str(e),
            error_type=type(e).__name__,
            startup_duration_seconds=(datetime.now(UTC) - startup_time).total_seconds()
        )
        # Пробрасываем исключение дальше
        raise

    # Передаем управление приложению
    yield

    # ========== SHUTDOWN ==========
    shutdown_time = datetime.now(UTC)
    logger.info(
        "Остановка BCH Solo Pool...",
        event="app_shutdown_started",
        shutdown_time=shutdown_time.isoformat(),
        active_background_tasks=len([t for t in background_tasks if not t.done()])
    )

    try:
        # Останавливаем все фоновые задачи
        stopped_tasks = 0
        for task in background_tasks:
            if not task.done():
                task.cancel()
                stopped_tasks += 1
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(
                        "Фоновая задача отменена",
                        event="background_task_cancelled",
                        task_id=id(task)
                    )
                except Exception as e:
                    logger.warning(
                        "Ошибка при остановке задачи",
                        event="background_task_stop_error",
                        task_id=id(task),
                        error=str(e)
                    )

        if stopped_tasks > 0:
            logger.info(
                f"Остановлено {stopped_tasks} фоновых задач",
                event="background_tasks_stopped",
                stopped_count=stopped_tasks
            )

        # Останавливаем TCP сервер
        if settings.stratum_tcp_enabled:
            try:
                await tcp_stratum_server.stop()
                logger.info(
                    "TCP Stratum сервер остановлен",
                    event="tcp_server_stopped",
                    host=tcp_stratum_server.host,
                    port=tcp_stratum_server.port
                )
            except Exception as e:
                logger.warning(
                    "Ошибка остановки TCP сервера",
                    event="tcp_server_stop_error",
                    error=str(e)
                )

        # Очищаем данные WebSocket сервера
        try:
            stratum_server.cleanup_all()
            logger.info(
                "WebSocket сервер очищен",
                event="websocket_server_cleaned",
                active_connections_before=len(stratum_server.active_connections)
            )
        except Exception as e:
            logger.warning(
                "Ошибка очистки WebSocket сервера",
                event="websocket_server_cleanup_error",
                error=str(e)
            )

        shutdown_duration = (datetime.now(UTC) - shutdown_time).total_seconds()
        logger.info(
            "Остановка завершена",
            event="app_shutdown_completed",
            shutdown_duration_seconds=shutdown_duration,
            total_runtime_seconds=(shutdown_time - startup_time).total_seconds()
        )

    except Exception as e:
        logger.error(
            "Ошибка при остановке приложения",
            event="app_shutdown_failed",
            error=str(e),
            shutdown_duration_seconds=(datetime.now(UTC) - shutdown_time).total_seconds()
        )


async def _periodic_job_broadcaster():
    """Периодическая рассылка новых заданий"""
    iteration = 0
    task_id = id(asyncio.current_task())

    logger.debug(
        "Запуск периодической рассылки заданий",
        event="job_broadcaster_loop_started",
        task_id=task_id
    )

    while True:
        iteration += 1
        try:
            await asyncio.sleep(settings.job_broadcast_interval)

            # Проверяем есть ли активные майнеры
            ws_miners = len(stratum_server.active_connections)
            tcp_miners = len(tcp_stratum_server.connections)
            active_miners = ws_miners + tcp_miners

            if active_miners > 0:
                await job_manager.broadcast_new_job_to_all()
                logger.debug(
                    f"Задание разослано {active_miners} майнерам",
                    event="job_broadcasted",
                    iteration=iteration,
                    active_miners=active_miners,
                    ws_miners=ws_miners,
                    tcp_miners=tcp_miners
                )
            else:
                logger.debug(
                    "Нет активных майнеров, пропускаем рассылку",
                    event="job_broadcast_skipped",
                    iteration=iteration,
                    reason="no_active_miners"
                )

        except asyncio.CancelledError:
            logger.info(
                "Задача рассылки заданий остановлена",
                event="job_broadcaster_stopped",
                task_id=task_id,
                total_iterations=iteration
            )
            break
        except Exception as e:
            logger.error(
                "Ошибка в периодической рассылке",
                event="job_broadcaster_error",
                iteration=iteration,
                error=str(e),
                error_type=type(e).__name__
            )
            await asyncio.sleep(5)


async def _periodic_job_cleanup():
    """Периодическая очистка старых заданий"""
    iteration = 0
    task_id = id(asyncio.current_task())

    logger.debug(
        "Запуск периодической очистки заданий",
        event="job_cleanup_loop_started",
        task_id=task_id
    )

    while True:
        iteration += 1
        try:
            await asyncio.sleep(60)

            # Очищаем задания в WebSocket сервере
            ws_jobs_before = len(getattr(stratum_server, 'subscriptions', {}))
            stratum_server.cleanup_old_jobs(max_age_seconds=settings.job_cleanup_age)

            # Очищаем задания в валидаторе
            validator_jobs_before = 0
            if hasattr(share_validator, 'jobs_cache'):
                validator_jobs_before = len(share_validator.jobs_cache)
                share_validator.cleanup_old_jobs(max_age_seconds=settings.job_cleanup_age)

            logger.debug(
                "Очистка старых заданий выполнена",
                event="job_cleanup_completed",
                iteration=iteration,
                cleanup_age_seconds=settings.job_cleanup_age,
                ws_jobs_before=ws_jobs_before,
                validator_jobs_before=validator_jobs_before
            )

        except asyncio.CancelledError:
            logger.info(
                "Задача очистки заданий остановлена",
                event="job_cleanup_stopped",
                task_id=task_id,
                total_iterations=iteration
            )
            break
        except Exception as e:
            logger.error(
                "Ошибка в очистке заданий",
                event="job_cleanup_error",
                iteration=iteration,
                error=str(e)
            )