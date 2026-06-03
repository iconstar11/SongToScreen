from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-2603"

    # OpenAI (Whisper API for Stage 2 + LLM fallback)
    openai_api_key: str = ""

    # Stock footage APIs
    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # YouTube
    youtube_client_secrets_path: Path = Path("secrets/client_secrets.json")

    # Pipeline behaviour
    auto_approve: bool = False
    cache_dir: Path = Path("cache")
    outputs_dir: Path = Path("outputs")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
