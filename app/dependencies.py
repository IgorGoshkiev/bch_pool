"""
Файл для хранения глобальных зависимостей и предотвращения циклических импортов.
"""

# Импортируем настройки
from app.utils.config import settings

# Создаем JobManager (пока с мок-клиентом)
from app.jobs.manager import JobManager
job_manager = JobManager()  # Убираем параметры, так как используем мок-клиент

# Импортируем StratumServer
from app.stratum.server import StratumServer
stratum_server = StratumServer()

# Функции для зависимостей (для FastAPI Depends)
def get_job_manager():
    return job_manager

def get_stratum_server():
    return stratum_server

__all__ = [
    "job_manager", 
    "stratum_server", 
    "get_job_manager", 
    "get_stratum_server"
]