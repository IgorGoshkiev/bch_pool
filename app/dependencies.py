"""Единый контейнер зависимостей для всего приложения"""
from app.utils.logging_config import StructuredLogger

from app.utils.network_config import NetworkManager
from app.stratum.block_builder import BlockBuilder
from app.services.difficulty_service import DifficultyService
from app.services.auth_service import AuthService
from app.services.database_service import DatabaseService
from app.stratum.validator import ShareValidator
from app.utils.protocol_helpers import STRATUM_EXTRA_NONCE1, EXTRA_NONCE2_SIZE
from app.services.job_service import JobService
from app.jobs.manager import JobManager
from app.stratum.websocket_server import StratumServer
from app.stratum.tcp_server import StratumTCPServer


logger = StructuredLogger(__name__)


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
        self._difficulty_service = None
        self._network_manager = None
        self._block_builder = None

        logger.info(
            "DependencyContainer инициализирован",
            event="dependencies_container_created"
        )

        # === NETWORK MANAGER ===

    @property
    def network_manager(self):
        if self._network_manager is None:
            self._network_manager = NetworkManager()
            logger.info(
                "NetworkManager создан",
                event="network_manager_created",
                network=self._network_manager.network
            )
        return self._network_manager

        # === BLOCK BUILDER ===

    @property
    def block_builder(self):
        if self._block_builder is None:
            self._block_builder = BlockBuilder(network_manager=self.network_manager)
            logger.info(
                "BlockBuilder создан",
                event="block_builder_created",
                has_network_manager=self.network_manager is not None
            )
        return self._block_builder

    # === DIFFICULTY SERVICE ===
    @property
    def difficulty_service(self):
        if self._difficulty_service is None:
            self._difficulty_service = DifficultyService(
                network_manager=self.network_manager,
                stratum_server=self.stratum_server,
                tcp_stratum_server=self.tcp_stratum_server
            )

            logger.info(
                "DifficultyService создан",
                event="difficulty_service_created",
                current_difficulty=self._difficulty_service.current_difficulty,
                network=self._difficulty_service.network_manager.network
            )
        return self._difficulty_service

    # === AUTH SERVICE ===
    @property
    def auth_service(self):
        if self._auth_service is None:
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
            # Используем сложность из difficulty_service
            initial_difficulty = self.difficulty_service.current_difficulty

            self._share_validator = ShareValidator(
                target_difficulty=initial_difficulty,
                extra_nonce2_size=EXTRA_NONCE2_SIZE,
                extra_nonce1=STRATUM_EXTRA_NONCE1
            )

            logger.info(
                "ShareValidator создан",
                event="share_validator_created",
                target_difficulty=initial_difficulty,
                extra_nonce2_size=EXTRA_NONCE2_SIZE
            )
        return self._share_validator

    # === JOB SERVICE ===
    @property
    def job_service(self):
        if self._job_service is None:
            self._job_service = JobService(validator=self.share_validator, network_manager=self.network_manager)
            logger.info(
                "JobService создан",
                event="job_service_created",
                has_validator=self.share_validator is not None
            )
        return self._job_service

    # === JOB MANAGER ===
    @property
    def job_manager(self):
        if self._job_manager is None:
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
            "tcp_stratum_server": self._tcp_stratum_server is not None,
            "difficulty_service": self._difficulty_service is not None,
            "network_manager": self._network_manager is not None,

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
difficulty_service = container.difficulty_service
network_manager = container.network_manager
block_builder = container.block_builder
