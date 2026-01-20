from datetime import datetime, UTC

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

import asyncio
import json
import time

from app.utils.logging_config import setup_logging, StructuredLogger

from app.schemas.models import ApiResponse
from app.models.database import get_db
from app.api.v1.miners import router as miners_router
from app.api.v1.pool import router as pool_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.tcp_stratum import router as tcp_stratum_router

from app.lifespan import lifespan
from app.dependencies import stratum_server, tcp_stratum_server, database_service, job_service, \
    job_manager, share_validator

# Настройка логов
logger = setup_logging()
api_logger = StructuredLogger("api")

app = FastAPI(
    title="BCH Solo Pool API",
    version="1.0.0",
    description="Bitcoin Cash Solo Mining Pool",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan
)


# ========== Middleware для разрешения WebSocket ==========
class WebSocketMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.scope.get("type") == "websocket":
            path = request.scope.get("path", "")
            if path.startswith("/stratum/ws/"):
                return await call_next(request)
        response = await call_next(request)
        return response


app.add_middleware(WebSocketMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роутеры
app.include_router(miners_router, prefix="/api/v1", tags=["miners"])
app.include_router(pool_router, prefix="/api/v1", tags=["pool"])
app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
app.include_router(tcp_stratum_router, prefix="/api/v1", tags=["tcp-stratum"])


# ========== Middleware для логирования запросов ==========
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware для логирования всех HTTP запросов"""
    start_time = time.time()

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        # Логируем только если это не health check или docs
        path = request.url.path
        if path not in ["/health", "/docs", "/redoc", "/favicon.ico"]:
            api_logger.info(
                f"{request.method} {path}",
                method=request.method,
                path=path,
                status_code=response.status_code,
                process_time=f"{process_time:.3f}s",
                client_ip=request.client.host if request.client else "unknown"
            )

        return response

    except Exception as e:
        process_time = time.time() - start_time
        api_logger.error(
            f"Ошибка при обработке {request.method} {request.url.path}",
            method=request.method,
            path=request.url.path,
            error=str(e),
            process_time=f"{process_time:.3f}s"
        )
        raise

@app.get("/", response_model=ApiResponse)  # ← ДОБАВЛЯЕМ response_model
async def root():
    """Корневой эндпоинт"""
    return ApiResponse(
        status="success",
        message="BCH Solo Pool API доступен",
        data={
            "service": "BCH Solo Pool",
            "version": "1.0.0",
            "api_version": "v1",
            "endpoints": {
                "v1_docs": "/docs",
                "v1_openapi": "/api/v1/openapi.json",
                "services_stats": "/services/stats",
                "services_health": "/services/health",
                "stratum_stats": "/stratum/stats",
                "miners": "/api/v1/miners",
                "pool_stats": "/api/v1/pool/stats",
                "jobs": "/api/v1/jobs",
                "tcp_stratum": "/api/v1/tcp-stratum"
            },
            "timestamp": datetime.now(UTC).isoformat()  # ← ДОБАВЛЯЕМ TIMESTAMP
        }
    )


@app.get("/health", response_model=ApiResponse)
async def health():
    """Базовая проверка здоровья"""
    import time

    return ApiResponse(
        status="healthy",
        message="Сервис работает нормально",
        data={
            "timestamp": time.time(),
            "service": "bch-pool-api",
            "version": "1.0.0",
            "uptime": "unknown"  # TODO В будущем добавить расчет uptime
        }
    )


@app.get("/database/health", response_model=ApiResponse)
async def database_health(db: AsyncSession = Depends(get_db)):
    """Проверка подключения к базе данных"""
    try:
        result = await db.execute(text("SELECT version()"))
        db_version = result.scalar()

        result = await db.execute(text("SELECT NOW()"))
        db_time = result.scalar()

        return ApiResponse(
            status="success",
            message="Подключение к базе данных установлено",
            data={
                "status": "connected",
                "database": {
                    "version": str(db_version),
                    "server_time": str(db_time),
                    "connection": "async",
                    "type": "PostgreSQL"
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка подключения к базе данных",
            data={
                "status": "disconnected",
                "database": {
                    "connected": False,
                    "error": str(e)
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )

@app.get("/database/tables", response_model=ApiResponse)
async def list_tables(db: AsyncSession = Depends(get_db)):
    """Список всех таблиц в базе данных"""
    try:
        result = await db.execute(text("""
            SELECT 
                table_name,
                (SELECT COUNT(*) FROM information_schema.columns 
                 WHERE table_schema = 'public' AND table_name = t.table_name) as columns_count
            FROM information_schema.tables t
            WHERE table_schema = 'public'
            ORDER BY table_name
        """))

        tables = []
        for row in result.fetchall():
            tables.append({
                "name": row[0],
                "columns": row[1]
            })

        return ApiResponse(
            status="success",
            message="Список таблиц получен",
            data={
                "tables_count": len(tables),
                "tables": tables,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка получения списка таблиц",
            data={
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
            }
        )


@app.get("/database/stats", response_model=ApiResponse)
async def database_stats(db: AsyncSession = Depends(get_db)):
    """Статистика по таблицам"""
    try:
        stats = {}
        tables = ["miners", "shares", "blocks"]

        for table in tables:
            try:
                result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                stats[table] = {
                    "records": count,
                    "empty": count == 0
                }
            except Exception as e:
                logger.debug(f"Ошибка получения статистики для таблицы {table}: {e}")
                stats[table] = {"error": f"table error: {type(e).__name__}"}

        return ApiResponse(
            status="success",
            message="Статистика базы данных получена",
            data={
                "database": "pool_db",
                "statistics": stats,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка получения статистики базы данных",
            data={
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
            }
        )

# ========== Stratum WebSocket ==========
@app.websocket("/stratum/ws/{miner_address}")
async def websocket_endpoint(websocket: WebSocket, miner_address: str):
    """Stratum WebSocket для подключения майнеров"""
    logger.info(f"Stratum подключение: {miner_address}")

    connection_id = None

    try:
        connection_id = await stratum_server.connect(websocket, miner_address)

        try:
            while True:
                data = await websocket.receive_json()
                logger.debug(f"Stratum сообщение от {miner_address}: {data}")
                await stratum_server.handle_message(websocket, connection_id, data)

        except WebSocketDisconnect:
            logger.info(f"Stratum отключился: {miner_address}")
        except json.JSONDecodeError as e:
            logger.error(f"Невалидный JSON от {miner_address}: {e}")
        except Exception as e:
            logger.error(f"Ошибка в WebSocket: {type(e).__name__}: {e}")

    except Exception as e:
        logger.error(f"Ошибка подключения WebSocket: {e}")
    finally:
        if 'connection_id':
            await stratum_server.disconnect(connection_id)


@app.get("/stratum/stats", response_model=ApiResponse)
async def get_stratum_stats():
    """Статистика всех Stratum серверов"""
    try:
        ws_stats = stratum_server.get_stats()
        tcp_stats = {
            "active_connections": len(tcp_stratum_server.connections),
            "active_miners": len(tcp_stratum_server.miners),
            "port": tcp_stratum_server.port
        }

        total_connections = ws_stats["active_connections"] + tcp_stats["active_connections"]
        total_miners = ws_stats["active_miners"] + len(tcp_stratum_server.miners)

        return ApiResponse(
            status="success",
            message="Статистика Stratum серверов получена",
            data={
                "webSocket_server": ws_stats,
                "tcp_server": tcp_stats,
                "totals": {
                    "total_connections": total_connections,
                    "total_miners": total_miners,
                    "protocols": ["WebSocket", "TCP"]
                },
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка получения статистики Stratum серверов",
            data={
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
            }
        )


# ========== НОВЫЕ ЭНДПОИНТЫ ДЛЯ СЕРВИСОВ (опционально) ==========

@app.get("/services/stats", response_model=ApiResponse)
async def get_services_stats():
    """Детальная статистика всех сервисов"""
    try:
        # Получаем статистику пула из database_service
        pool_stats = {}
        if hasattr(database_service, 'get_pool_stats'):
            try:
                pool_stats = await database_service.get_pool_stats()
            except Exception as e:
                logger.error(f"Ошибка получения статистики пула из database_service: {e}")
                pool_stats = {"error": "unavailable"}

        return ApiResponse(
            status="success",
            message="Статистика сервисов получена",
            data={
                "timestamp": datetime.now(UTC).isoformat(),
                "services": {
                    "database_service": {
                        "type": "DatabaseService",
                        "status": "active"
                    },
                    "auth_service": {
                        "type": "AuthService",
                        "status": "active"
                    },
                    "job_service": job_service.get_stats() if hasattr(job_service, 'get_stats') else {},
                    "stratum_server": stratum_server.get_stats(),
                    "tcp_stratum_server": {
                        "active_connections": len(tcp_stratum_server.connections),
                        "active_miners": len(tcp_stratum_server.miners),
                        "port": tcp_stratum_server.port
                    },
                    "validator": {
                        "jobs_count": len(share_validator.jobs_cache) if hasattr(share_validator, 'jobs_cache') else 0
                    },
                    "job_manager": job_manager.get_stats() if hasattr(job_manager, 'get_stats') else {}
                },
                "totals": {
                    "active_miners": len(set(stratum_server.miner_addresses.values())) + len(tcp_stratum_server.miners),
                    "total_connections": len(stratum_server.active_connections) + len(tcp_stratum_server.connections),
                    "pool_stats": pool_stats
                }
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка получения статистики сервисов",
            data={
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
            }
        )


@app.get("/services/health", response_model=ApiResponse)
async def get_services_health():
    """Проверка здоровья всех сервисов"""
    try:
        services_health = {
            "database_service": "healthy",
            "auth_service": "healthy",
            "job_service": "healthy",
            "stratum_server": "healthy" if hasattr(stratum_server, 'active_connections') else "degraded",
            "tcp_server": "healthy" if hasattr(tcp_stratum_server, 'connections') else "degraded",
            "job_manager": "connected" if hasattr(job_manager, 'block_height') and job_manager.block_height > 0 else "disconnected",
        }

        all_healthy = all(
            status == "healthy" or status == "connected"
            for status in services_health.values()
        )

        return ApiResponse(
            status="success",
            message="Проверка здоровья сервисов выполнена",
            data={
                "overall": "healthy" if all_healthy else "degraded",
                "services": services_health,
                "timestamp": datetime.now(UTC).isoformat(),
                "health_check_time": asyncio.get_event_loop().time()
            }
        )
    except Exception as e:
        return ApiResponse(
            status="error",
            message="Ошибка проверки здоровья сервисов",
            data={
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
            }
        )