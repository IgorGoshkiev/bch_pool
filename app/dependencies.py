"""Единый контейнер зависимостей для всего приложения"""
from app.utils.config import settings
from app.utils.logging_config import StructuredLogger
from app.utils.network_config import NetworkManager
from app.stratum.block_builder import BlockBuilder
from app.services.difficulty_service import DifficultyService
from app.services.auth_service import AuthService
from app.services.database_service import DatabaseService
from app.stratum.validator import ShareValidator
from app.utils.protocol_helpers import EXTRA_NONCE2_SIZE
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
        self._job_manager_initialized = False

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

    # === SHARE VALIDATOR ===
    # === SHARE VALIDATOR ===
    @property
    def share_validator(self):
        if self._share_validator is None:
            self._share_validator = ShareValidator(
                # ===== VALIDATION DIFFICULTY =====
                # Это то, с чем мы ВАЛИДИРУЕМ входящие шары.
                # Очень низкая (1e-10), чтобы принимать ВСЕ шары от ASIC.
                # ASIC присылает шары со своей внутренней сложностью (~1e-9),
                # которую мы не контролируем.
                #
                # НЕ ПУТАТЬ с display_difficulty (то, что мы ОТПРАВЛЯЕМ ASIC).
                # display_difficulty управляет частотой шаров и живёт
                # в StratumTCPServer.miner_difficulties.
                pool_difficulty=settings.default_validation_difficulty,
                extra_nonce2_size=EXTRA_NONCE2_SIZE,
                extra_nonce1=None,
                block_builder=self.block_builder
            )
            logger.info(
                "ShareValidator создан",
                event="share_validator_created",
                validation_difficulty=settings.default_validation_difficulty,
                extra_nonce2_size=EXTRA_NONCE2_SIZE
            )
        return self._share_validator

    # === JOB SERVICE ===
    @property
    def job_service(self):
        if self._job_service is None:
            self._job_service = JobService(
                validator=self.share_validator,
                network_manager=self.network_manager
            )
            logger.info(
                "JobService создан",
                event="job_service_created",
                has_validator=self.share_validator is not None
            )
        return self._job_service

    # === JOB MANAGER (БЕЗ СЕРВЕРОВ) ===
    @property
    def job_manager(self):
        if self._job_manager is None:
            self._job_manager = JobManager(
                job_service=self.job_service,
                block_builder=self.block_builder
            )
            logger.info(
                "JobManager создан",
                event="job_manager_created",
                has_node_client=self._job_manager.node_client is not None
            )
        return self._job_manager

    # === ИНИЦИАЛИЗАЦИЯ JOB MANAGER С СЕРВЕРАМИ ===
    def initialize_job_manager_with_servers(self):
        """Инициализация JobManager с серверами (после создания всех зависимостей)"""
        if not self._job_manager_initialized:
            # Получаем job_manager (создается при первом обращении)
            jm = self.job_manager
            # Устанавливаем серверы
            jm.stratum_server = self.stratum_server
            jm.tcp_stratum_server = self.tcp_stratum_server
            self._job_manager_initialized = True
            logger.info(
                "JobManager инициализирован с серверами",
                event="job_manager_initialized_with_servers",
                has_stratum_server=self.stratum_server is not None,
                has_tcp_stratum_server=self.tcp_stratum_server is not None
            )
        return self._job_manager

    def initialize_difficulty_service_with_servers(self):
        """
        Инициализация DifficultyService с TCP-сервером.

        ВАЖНО: вызывается ПОСЛЕ создания tcp_stratum_server.
        Разрывает циклическую зависимость:
        - difficulty_service нужен для создания tcp_stratum_server
        - tcp_stratum_server нужен difficulty_service для чтения реальной сложности

        БЕЗ ЭТОГО ВЫЗОВА:
        - difficulty_service.tcp_stratum_server = None
        - get_miner_hashrate читает свою копию miner_difficulties (мусор)
        - хэшрейт считается неверно (1440 TH/s вместо 40-90 TH/s)
        - сложность разгоняется до 1 миллиарда
        - ASIC замолкает, потому что не может найти шар

        Returns:
            DifficultyService или None
        """
        print(f"🔴 [INIT_DIFF] ===== START =====", flush=True)

        # Проверяем, что difficulty_service уже создан
        if self._difficulty_service is None:
            print(f"🔴 [INIT_DIFF] ❌ DifficultyService is None, cannot initialize", flush=True)
            logger.warning(
                "DifficultyService не создан, нечего инициализировать",
                event="difficulty_service_not_created"
            )
            return None

        # Получаем TCP-сервер (создаётся при первом обращении к свойству)
        # Это ВАЖНО: обращение к self.tcp_stratum_server создаст его, если ещё нет
        tcp_server = self.tcp_stratum_server
        print(f"🔴 [INIT_DIFF] tcp_stratum_server = {tcp_server}", flush=True)
        print(f"🔴 [INIT_DIFF] type(tcp_server) = {type(tcp_server).__name__}", flush=True)

        # Присваиваем его difficulty_service
        self._difficulty_service.tcp_stratum_server = tcp_server

        # Проверяем, что присвоилось
        check = self._difficulty_service.tcp_stratum_server
        print(f"🔴 [INIT_DIFF] ✅ difficulty_service.tcp_stratum_server = {check}", flush=True)
        print(f"🔴 [INIT_DIFF] ✅ is None? {check is None}", flush=True)

        logger.info(
            "DifficultyService инициализирован с TCP сервером",
            event="difficulty_service_initialized_with_tcp",
            has_tcp_stratum_server=tcp_server is not None,
            tcp_server_type=type(tcp_server).__name__ if tcp_server else "None"
        )

        print(f"🔴 [INIT_DIFF] ===== END =====", flush=True)

        return self._difficulty_service

    # === STRATUM SERVER ===
    @property
    def stratum_server(self):
        if self._stratum_server is None:
            self._stratum_server = StratumServer(
                job_manager=self.job_manager,
                auth_service=self.auth_service,
                database_service=self.database_service,
                job_service=self.job_service
            )
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
            self._tcp_stratum_server = StratumTCPServer(
                auth_service=self.auth_service,
                database_service=self.database_service,
                job_service=self.job_service,
                job_manager=self.job_manager,
                difficulty_service=self.difficulty_service,
                share_validator=self.share_validator
            )
            logger.info(
                "TcpStratumServer создан",
                event="tcp_stratum_server_created",
                host=self._tcp_stratum_server.host,
                port=self._tcp_stratum_server.port,
                has_job_manager=self.job_manager is not None,
                has_share_validator=self.share_validator is not None
            )
        return self._tcp_stratum_server

    # === DIFFICULTY SERVICE ===
    @property
    def difficulty_service(self):
        if self._difficulty_service is None:
            self._difficulty_service = DifficultyService(
                network_manager=self.network_manager,
                stratum_server=self.stratum_server,
                tcp_stratum_server=None
            )

            if self._share_validator:
                self._share_validator.pool_difficulty = self._difficulty_service.current_difficulty
                logger.info(
                    "ShareValidator обновлен актуальной сложностью",
                    event="share_validator_updated",
                    new_difficulty=self._difficulty_service.current_difficulty
                )

            logger.info(
                "DifficultyService создан",
                event="difficulty_service_created",
                current_difficulty=self._difficulty_service.current_difficulty,
                network=self._difficulty_service.network_manager.network
            )
        return self._difficulty_service

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
            "job_manager_initialized": self._job_manager_initialized,
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