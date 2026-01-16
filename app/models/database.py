from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import create_engine
from app.utils.config import settings

# ========== 1. BASE ДЛЯ МОДЕЛЕЙ ==========
class Base(DeclarativeBase):
    """Единый Base для всех моделей и миграций"""
    pass

# ========== 2. SYNC ДВИЖОК (для Alembic миграций) ==========
SYNC_DATABASE_URL = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
sync_engine = create_engine(SYNC_DATABASE_URL)

# ========== 3. ASYNC ДВИЖОК (для приложения) ==========
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ========== 4. DEPENDENCY ДЛЯ FASTAPI ==========
async def get_db():
    """Получение async сессии для FastAPI эндпоинтов"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# ========== 5. ПОЛЕЗНЫЕ ФУНКЦИИ ==========
def get_sync_engine():
    """Получение sync движка (для миграций, скриптов)"""
    return sync_engine

def get_async_engine():
    """Получение async движка (для приложения)"""
    return async_engine
