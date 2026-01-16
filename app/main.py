from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging
import asyncio
import json

from app.models.database import get_db
from app.api.v1.miners import router as miners_router
from app.api.v1.pool import router as pool_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.tcp_stratum import router as tcp_stratum_router

from app.lifespan import lifespan
from app.dependencies import stratum_server, tcp_stratum_server, database_service, job_service, \
    job_manager, share_validator

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
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
            "pool_stats": "/api/v1/pool/stats"
        }
    }


@app.get("/health")
async def health():
    """Базовая проверка здоровья"""
    import time
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "service": "bch-pool-api",
        "version": "1.0.0"
    }


@app.get("/database/health")
async def database_health(db: AsyncSession = Depends(get_db)):
    """Проверка подключения к базе данных"""
    try:
        result = await db.execute(text("SELECT version()"))
        db_version = result.scalar()

        result = await db.execute(text("SELECT NOW()"))
        db_time = result.scalar()

        return {
            "status": "connected",
            "database": {
                "version": db_version,
                "server_time": str(db_time),
                "connection": "async"
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "database": {
                "connected": False,
                "error": str(e)
            }
        }

@app.get("/database/tables")
async def list_tables(db: AsyncSession = Depends(get_db)):
    """Список всех таблиц в базе данных"""
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

    return {
        "tables_count": len(tables),
        "tables": tables
    }


@app.get("/database/stats")
async def database_stats(db: AsyncSession = Depends(get_db)):
    """Статистика по таблицам"""
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

    return {
        "database": "pool_db",
        "statistics": stats
    }

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


@app.get("/stratum/stats")
async def get_stratum_stats():
    """Статистика всех Stratum серверов"""
    ws_stats = stratum_server.get_stats()
    tcp_stats = {
        "active_connections": len(tcp_stratum_server.connections),
        "active_miners": len(tcp_stratum_server.miners),
        "port": tcp_stratum_server.port
    }

    return {
        "webSocket_server": ws_stats,
        "tcp_server": tcp_stats,
        "total_connections": ws_stats["active_connections"] + tcp_stats["active_connections"],
        "total_miners": ws_stats["active_miners"] + len(tcp_stratum_server.miners)
    }


# ========== НОВЫЕ ЭНДПОИНТЫ ДЛЯ СЕРВИСОВ (опционально) ==========

@app.get("/services/stats")
async def get_services_stats():
    """Детальная статистика всех сервисов"""
    return {
        "timestamp": asyncio.get_event_loop().time(),
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
            "pool_stats": await database_service.get_pool_stats() if hasattr(database_service, 'get_pool_stats') else {}
        }
    }


@app.get("/services/health")
async def get_services_health():
    """Проверка здоровья всех сервисов"""
    services_health = {
        "database_service": "healthy",
        "auth_service": "healthy",
        "job_service": "healthy",
        "stratum_server": "healthy" if hasattr(stratum_server, 'active_connections') else "degraded",
        "tcp_server": "healthy" if hasattr(tcp_stratum_server, 'connections') else "degraded",
        "job_manager": "connected" if hasattr(job_manager,
                                              'block_height') and job_manager.block_height > 0 else "disconnected",
    }

    all_healthy = all(
        status == "healthy" or status == "connected"
        for status in services_health.values()
    )

    return {
        "overall": "healthy" if all_healthy else "degraded",
        "services": services_health,
        "timestamp": asyncio.get_event_loop().time(),
    }