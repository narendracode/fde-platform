from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://agri:agripass@localhost:5432/agri_agent"
    database_url_sync: str = "postgresql://agri:agripass@localhost:5432/agri_agent"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # LLM providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    tavily_api_key: str = ""

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "agri-agent-poc"

    # API security
    api_key: str = "dev-secret-key-change-in-prod"
    # Base URL the platform uses when executing approval_action HTTP calls.
    # In Docker the API calls itself via loopback; workers call via service name.
    api_base_url: str = "http://localhost:8000"

    # App
    log_level: str = "info"
    agents_config_dir: str = "agents/configs"

    # UI feature flags — comma-separated string: "sandhar,fundly" (empty = platform only)
    companies_to_show: str = "sandhar,fundly"

    # OpenTelemetry
    otel_enabled: bool = False
    otel_service_name: str = "agri-agent"
    otel_exporter_otlp_endpoint: str = "http://jaeger:4318"
    jaeger_ui_url: str = "http://localhost:16686"


settings = Settings()
