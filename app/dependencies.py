"""Единый контейнер зависимостей для всего приложения"""

import logging


logger = logging.getLogger(__name__)


class DependencyContainer:
    """Контейнер для управления зависимостями"""

    def __init__(self):
        self._auth_service = None
        self._database_service = None
        self._job_service = None
        self._share_validator = None
        self._job_manager = None
        self._stratum_server = None
        self._tcp_stratum_server = None

    # === AUTH SERVICE ===
    @property
    def auth_service(self):
        if self._auth_service is None:
            from app.services.auth_service import AuthService
            self._auth_service = AuthService()
            logger.debug("AuthService инициализирован")
        return self._auth_service

    # === DATABASE SERVICE ===
    @property
    def database_service(self):
        if self._database_service is None:
            from app.services.database_service import DatabaseService
            self._database_service = DatabaseService()
            logger.debug("DatabaseService инициализирован")
        return self._database_service

    # === SHARE VALIDATOR ===
    @property
    def share_validator(self):
        if self._share_validator is None:
            from app.stratum.validator import ShareValidator
            self._share_validator = ShareValidator()
            logger.debug("ShareValidator инициализирован")
        return self._share_validator

    # === JOB SERVICE ===
    @property
    def job_service(self):
        if self._job_service is None:
            from app.services.job_service import JobService
            self._job_service = JobService()
            logger.debug("JobService инициализирован")
        return self._job_service

    # === JOB MANAGER ===
    @property
    def job_manager(self):
        if self._job_manager is None:
            from app.jobs.manager import JobManager
            self._job_manager = JobManager()
            logger.debug("JobManager инициализирован")
        return self._job_manager

    # === STRATUM SERVER ===
    @property
    def stratum_server(self):
        if self._stratum_server is None:
            from app.stratum.websocket_server import StratumServer
            # Передаем job_manager при создании
            self._stratum_server = StratumServer(job_manager=self.job_manager)
            logger.debug("StratumServer инициализирован")
        return self._stratum_server

    # === TCP STRATUM SERVER ===
    @property
    def tcp_stratum_server(self):
        if self._tcp_stratum_server is None:
            from app.stratum.tcp_server import StratumTCPServer
            self._tcp_stratum_server = StratumTCPServer()
            logger.debug("TcpStratumServer инициализирован")
        return self._tcp_stratum_server


# Глобальный экземпляр контейнера
container = DependencyContainer()

# Удобные алиасы
auth_service = container.auth_service
database_service = container.database_service
share_validator = container.share_validator
job_service = container.job_service
job_manager = container.job_manager
stratum_server = container.stratum_server
tcp_stratum_server = container.tcp_stratum_server