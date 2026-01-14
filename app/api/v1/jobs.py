from datetime import datetime, UTC

from fastapi import APIRouter
from app.dependencies import job_manager, stratum_server

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/stats")
async def get_job_stats():
    """Статистика JobManager"""
    stats = job_manager.get_stats()

    return {
        "job_manager": {
            "status": "running",
            "current_job": stats["current_job"],
            "total_jobs_created": stats["total_jobs_created"],
            "job_history_size": stats["job_history_size"]
        },
        "node": stats["node_info"],
        "timestamp": datetime.now(UTC).isoformat()
    }


@router.post("/broadcast")
async def broadcast_new_job():
    """Принудительная рассылка нового задания"""
    await job_manager.broadcast_new_job_to_all()

    return {
        "status": "broadcasted",
        "message": "New job broadcasted to all miners",
        "active_miners": len(set(stratum_server.miner_addresses.values()))
    }


@router.get("/history")
async def get_job_history(limit: int = 10):
    """История заданий"""
    history = job_manager.job_history[-limit:] if job_manager.job_history else []

    return {
        "total_jobs": len(job_manager.job_history),
        "requested_limit": limit,
        "jobs": [
            {
                "id": job["id"],
                "created_at": job["created_at"].isoformat() if hasattr(job["created_at"], 'isoformat') else str(
                    job["created_at"]),
                "miner": job.get("miner_address", "broadcast"),
                "height": job["template"].get("height", "unknown")
            }
            for job in reversed(history)  # Новые сверху
        ]
    }