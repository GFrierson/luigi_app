import os
import logging
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str

    # OpenRouter
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str
    LLM_MODEL: str

    # App Config
    TIMEZONE: str
    DATABASE_DIR: str
    LOG_LEVEL: str

    @classmethod
    def load(cls) -> 'Settings':
        """Load and validate all required environment variables."""
        required_vars = [
            'TELEGRAM_BOT_TOKEN',
            'OPENROUTER_API_KEY',
            'OPENROUTER_BASE_URL',
            'LLM_MODEL',
            'TIMEZONE',
            'DATABASE_DIR',
            'LOG_LEVEL'
        ]

        missing_vars = []
        env_values = {}

        for var in required_vars:
            value = os.getenv(var)
            if value is None or value.strip() == '':
                missing_vars.append(var)
            else:
                env_values[var] = value

        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        settings = cls(**env_values)
        logger.info("Configuration loaded successfully")
        return settings

_settings_instance = None

def get_settings() -> Settings:
    """Get the global settings instance, loading it if necessary."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings.load()
    return _settings_instance
