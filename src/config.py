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
    
    # Twilio
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    USER_PHONE_NUMBER: str
    
    # OpenRouter
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str
    LLM_MODEL: str
    
    # App Config
    TIMEZONE: str
    DATABASE_PATH: str
    LOG_LEVEL: str
    
    @classmethod
    def load(cls) -> 'Settings':
        """Load and validate all required environment variables."""
        required_vars = [
            'TWILIO_ACCOUNT_SID',
            'TWILIO_AUTH_TOKEN',
            'TWILIO_PHONE_NUMBER',
            'USER_PHONE_NUMBER',
            'OPENROUTER_API_KEY',
            'OPENROUTER_BASE_URL',
            'LLM_MODEL',
            'TIMEZONE',
            'DATABASE_PATH',
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

# Create a global settings instance
settings = Settings.load()
