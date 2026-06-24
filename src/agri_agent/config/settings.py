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

    # App
    log_level: str = "info"
    agents_config_dir: str = "agents/configs"


settings = Settings()
