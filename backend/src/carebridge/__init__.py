"""CareBridge AI backend package.

Loads .env (repo root or backend/) before any module reads os.environ, so
DATABASE_URL, LLM_PROVIDER, OLLAMA_*, ANTHROPIC_* can all live in one file.
"""

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
