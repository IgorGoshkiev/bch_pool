from datetime import datetime, UTC

from app.utils.logging_config import StructuredLogger

from fastapi import APIRouter, HTTPException, status
from app.schemas.models import ApiResponse
from app.dependencies import job_manager, stratum_server

logger = StructuredLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/stats")
async def get_job_stats():
    """Статистика JobManager"""
    try:
        logger.debug("Запрос статистики заданий")
        stats = job_manager.get_stats()

        return ApiResponse(
            status="success",
            message="Статистика заданий получена",
            data={
                "job_manager": {
                    "status": "running",
                    "current_job": stats["current_job"],
                    "total_jobs_created": stats["total_jobs_created"],
                    "job_history_size": stats["job_history_size"]
                },
                "node": stats["node_info"],
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения статистики заданий: {str(e)}"
        )


async def broadcast_new_job():
    """Принудительная рассылка нового задания"""
    try:
        await job_manager.broadcast_new_job_to_all()

        # Получаем актуальную статистику после рассылки
        active_miners = len(set(stratum_server.miner_addresses.values())) if stratum_server else 0

        return ApiResponse(
            status="success",
            message="Новое задание разослано всем майнерам",
            data={
                "status": "broadcasted",
                "active_miners": active_miners,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка рассылки задания: {str(e)}"
        )


@router.get("/history", response_model=ApiResponse)
async def get_job_history(limit: int = 10):
    """История заданий"""
    try:
        if limit < 1 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit должен быть между 1 и 100"
            )

        history = job_manager.job_history[-limit:] if job_manager.job_history else []

        jobs_list = []
        for job in reversed(history):  # Новые сверху
            jobs_list.append({
                "id": job.get("id", "unknown"),
                "created_at": job.get("created_at").isoformat() if job.get("created_at") else "unknown",
                "miner": job.get("miner_address", "broadcast"),
                "height": job.get("template", {}).get("height", "unknown"),
                "type": "personal" if job.get("miner_address") else "broadcast"
            })

        return ApiResponse(
            status="success",
            message="История заданий получена",
            data={
                "total_jobs": len(job_manager.job_history) if job_manager.job_history else 0,
                "requested_limit": limit,
                "actual_returned": len(jobs_list),
                "jobs": jobs_list,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения истории заданий: {str(e)}"
        )