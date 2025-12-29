from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.models.database import get_db

# Импортируем роутеры v1
from app.api.v1.miners import router as miners_router
from app.api.v1.pool import router as pool_router
from app.api.v1.test import router as test_router


app = FastAPI(
    title="BCH Solo Pool API",
    version="1.0.0",
    description="Bitcoin Cash Solo Mining Pool",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/api/v1/openapi.json"
)

# CORS middleware (для веб-интерфейса)
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
app.include_router(test_router, prefix="/api/v1", tags=["test"])

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