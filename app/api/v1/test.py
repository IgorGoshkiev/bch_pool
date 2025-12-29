from fastapi import APIRouter

router = APIRouter(tags=["test"])

@router.get("/test")
async def test_endpoint():
    return {"message": "Test endpoint works!", "path": "/api/v1/test"}

@router.get("/")
async def test_root():
    return {"message": "Test root works!", "path": "/api/v1/"}