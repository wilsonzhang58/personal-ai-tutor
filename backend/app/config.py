"""Load local settings before any database or model configuration is read."""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / 'backend' / '.env'
KEY_IN_PROCESS_BEFORE_DOTENV = bool(os.environ.get('OPENAI_API_KEY'))
# Explicit process settings win over the optional local file.
load_dotenv(ENV_PATH, override=False)
