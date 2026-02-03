### Phase 1: Project Scaffolding

**Step 1.1 — Create directory structure:** Create the following empty files to establish the project skeleton:

- `src/__init__.py` (empty)
- `tests/__init__.py` (empty)
- `data/.gitkeep` (empty, ensures `data/` directory exists in git)

**Step 1.2 — Create `requirements.txt`:** Write to `requirements.txt`:

```
fastapi>=0.109.0
uvicorn>=0.27.0
openai>=1.10.0
twilio>=8.10.0
apscheduler>=3.10.0
python-dotenv>=1.0.0
httpx>=0.26.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

**Step 1.3 — Create `.env.example`:** Write to `.env.example`:

```bash
# Twilio
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1234567890

# Recipient
USER_PHONE_NUMBER=+1234567890

# OpenRouter
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4o-mini

# App Config
TIMEZONE=America/New_York
DATABASE_PATH=data/health_tracker.db
LOG_LEVEL=INFO
```

**[COMMIT]:** "Scaffold project structure, requirements, and env template"
