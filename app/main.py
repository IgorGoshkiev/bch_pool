from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging

from app.models.database import get_db
from app.api.v1.miners import router as miners_router
from app.api.v1.pool import router as pool_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.tcp_stratum import router as tcp_stratum_router

from app.lifespan import lifespan
from app.dependencies import stratum_server, tcp_stratum_server

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
        # Для WebSocket запросов сразу пропускаем
        if request.scope.get("type") == "websocket":
            # Проверяем, это наш Stratum endpoint
            path = request.scope.get("path", "")
            if path.startswith("/stratum/ws/"):
                # Принимаем все соединения
                return await call_next(request)

        # Для обычных HTTP запросов
        response = await call_next(request)
        return response


# Подключаем middleware

app.add_middleware(WebSocketMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В production укажите конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роутеры v1 с префиксом /api/v1
app.include_router(miners_router, prefix="/api/v1", tags=["miners"])
app.include_router(pool_router, prefix="/api/v1", tags=["pool"])
app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
app.include_router(tcp_stratum_router, prefix="/api/v1", tags=["tcp-stratum"])


@app.get("/")
async def root():
    """Корневой эндпоинт с информацией о сервисе"""
    return {
        "service": "BCH Solo Pool",
        "version": "1.0.0",
        "api_version": "v1",
        "endpoints": {
            "v1_docs": "/docs",
            "v1_openapi": "/api/v1/openapi.json",
            "miners": "/api/v1/miners",
            "pool_stats": "/api/v1/pool/stats"
        }
    }


@app.get("/health")
async def health():
    """Базовая проверка здоровья сервиса"""
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

    # Для каждой таблицы получаем количество записей
    tables = ["miners", "shares", "blocks"]

    for table in tables:
        try:
            result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            stats[table] = {
                "records": count,
                "empty": count == 0
            }
        except:
            stats[table] = {"error": "table not found"}

    return {
        "database": "pool_db",
        "statistics": stats
    }


# ========== Stratum WebSocket ==========
@app.websocket("/stratum/ws/{miner_address}")
async def websocket_endpoint(websocket: WebSocket, miner_address: str):
    """Stratum WebSocket для подключения майнеров"""
    logger.info(f"Stratum подключение: {miner_address}")

    # Подключаем через WebSocket сервер (он сам вызовет accept)
    connection_id = await stratum_server.connect(websocket, miner_address)

    try:
        while True:
            # Получаем сообщения
            data = await websocket.receive_json()
            logger.info(f"Stratum сообщение от {miner_address}: {data}")

            # Обрабатываем через stratum_server
            await stratum_server.handle_message(websocket, connection_id, data)

    except WebSocketDisconnect:
        logger.info(f"Stratum отключился: {miner_address}")
    except Exception as e:
        logger.error(f"Ошибка в WebSocket: {type(e).__name__}: {e}")
    finally:
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