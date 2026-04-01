# API dependencies
from typing import Optional

from ..config import settings


def get_wos_api_key() -> Optional[str]:
    """Get WOS API key from settings."""
    return settings.wos_api_key or None


def get_dashscope_api_key() -> str:
    """Get Dashscope API key from settings."""
    return settings.dashscope_api_key
