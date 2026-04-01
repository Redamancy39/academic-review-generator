# Backend configuration
import os
from pathlib import Path
from typing import Optional

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        protected_namespaces=(),  # 允许 model_ 前缀字段
    )

    # API Keys
    dashscope_api_key: str = ""
    wos_api_key: str = ""

    # Model Configuration
    model_name: str = "openai/qwen3.5-plus"
    model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # WOS API
    wos_api_base: str = "https://api.clarivate.com/apis/wos-starter/v1/documents"

    # Output Configuration
    output_dir: Path = Path("outputs")

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def current_year(self) -> int:
        from datetime import datetime
        return datetime.now().year


settings = Settings()
