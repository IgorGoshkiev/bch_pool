"""Единый контейнер зависимостей для всего приложения"""
from app.utils.logging_config import StructuredLogger

logger = StructuredLogger("dependencies")


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

        logger.info(
            "DependencyContainer инициализирован",
            event="dependencies_container_created"
        )

    # === AUTH SERVICE ===
    @property
    def auth_service(self):
        if self._auth_service is None:
            from app.services.auth_service import AuthService
            self._auth_service = AuthService(database_service=self.database_service)
            logger.info(
                "AuthService создан",
                event="auth_service_created"
            )
        return self._auth_service

    # === DATABASE SERVICE ===
    @property
    def database_service(self):
        if self._database_service is None:
            from app.services.database_service import DatabaseService
            self._database_service = DatabaseService()
            logger.info(
                "DatabaseService создан",
                event="database_service_created"
            )
        return self._database_service

    # === SHARE VALIDATOR ===
    @property
    def share_validator(self):
        if self._share_validator is None:
            from app.stratum.validator import ShareValidator
            self._share_validator = ShareValidator()
            logger.info(
                "ShareValidator создан",
                event="share_validator_created",
                target_difficulty=self._share_validator.target_difficulty
            )
        return self._share_validator

    # === JOB SERVICE ===
    @property
    def job_service(self):
        if self._job_service is None:
            from app.services.job_service import JobService
            self._job_service = JobService(validator=self.share_validator)
            logger.info(
                "JobService создан",
                event="job_service_created"
            )
        return self._job_service

    # === JOB MANAGER ===
    @property
    def job_manager(self):
        if self._job_manager is None:
            from app.jobs.manager import JobManager
            self._job_manager = JobManager()
            logger.info(
                "JobManager создан",
                event="job_manager_created",
                has_node_client=self._job_manager.node_client is not None
            )
        return self._job_manager

    # === STRATUM SERVER ===
    @property
    def stratum_server(self):
        if self._stratum_server is None:
            from app.stratum.websocket_server import StratumServer
            # Передаем job_manager при создании
            self._stratum_server = StratumServer(job_manager=self.job_manager)
            logger.info(
                "StratumServer создан",
                event="stratum_server_created",
                has_job_manager=self.job_manager is not None
            )
        return self._stratum_server

    # === TCP STRATUM SERVER ===
    @property
    def tcp_stratum_server(self):
        if self._tcp_stratum_server is None:
            from app.stratum.tcp_server import StratumTCPServer
            self._tcp_stratum_server = StratumTCPServer()
            logger.info(
                "TcpStratumServer создан",
                event="tcp_stratum_server_created",
                host=self._tcp_stratum_server.host,
                port=self._tcp_stratum_server.port
            )
        return self._tcp_stratum_server

    def get_stats(self) -> dict:
        """Получить статистику всех сервисов"""
        stats = {
            "auth_service": self._auth_service is not None,
            "database_service": self._database_service is not None,
            "job_service": self._job_service is not None,
            "share_validator": self._share_validator is not None,
            "job_manager": self._job_manager is not None,
            "stratum_server": self._stratum_server is not None,
            "tcp_stratum_server": self._tcp_stratum_server is not None
        }

        logger.debug(
            "Получение статистики DependencyContainer",
            event="dependencies_stats",
            services_initialized=sum(1 for v in stats.values() if v),
            total_services=len(stats)
        )

        return stats


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