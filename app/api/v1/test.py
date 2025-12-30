from fastapi import APIRouter

router = APIRouter(tags=["test"])

@router.get("/test")
async def test_endpoint():
    return {"message": "Test endpoint works!", "path": "/api/v1/test"}

@router.get("/")
async def test_root():
    return {"message": "Test root works!", "path": "/api/v1/"}

@router.get("/websocket-test")
async def websocket_test():
    """Тестовая страница для проверки WebSocket"""
    return {
        "websocket_url": "ws://localhost:8000/stratum/ws/{miner_address}",
        "test_miner_address": "test_miner_123",
        "instructions": "Используйте WebSocket клиент для подключения"
    }