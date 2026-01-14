"""
Файл для хранения глобальных зависимостей и предотвращения циклических импортов.
"""

# Импортируем настройки
from app.utils.config import settings

from app.jobs.manager import JobManager
job_manager = JobManager()

# WebSocket Stratum Server
from app.stratum.server import StratumServer
stratum_server = StratumServer()

# TCP Stratum Server
from app.stratum.tcp_server import StratumTCPServer
tcp_stratum_server = StratumTCPServer(
    host=settings.stratum_host if hasattr(settings, 'stratum_host') else "0.0.0.0",
    port=settings.stratum_port if hasattr(settings, 'stratum_port') else 3333
)

# Функции для зависимостей (для FastAPI Depends)
def get_job_manager():
    return job_manager

def get_stratum_server():
    return stratum_server

def get_tcp_stratum_server():
    return tcp_stratum_server

__all__ = [
    "job_manager",
    "stratum_server",
    "tcp_stratum_server",
    "get_job_manager",
    "get_stratum_server",
    "get_tcp_stratum_server"
]