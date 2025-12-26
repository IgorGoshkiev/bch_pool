from app.jobs.manager import JobManager
from app.utils.config import settings

# Создаём глобальный JobManager
job_manager = JobManager(
    use_cookie=settings.bch_use_cookie,
    rpc_port=settings.bch_rpc_port
)

def get_job_manager():
    return job_manager