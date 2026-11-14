"""Shared Windows/WSL configuration. Relative storage paths follow the checkout."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_file = Path(os.environ.get('OPM_ENV_FILE', '.env'))
if not env_file.is_absolute():
    env_file = BASE_DIR / env_file
if 'OPM_ENV_FILE' in os.environ and not env_file.is_file():
    raise RuntimeError(f'Environment file not found: {env_file}')
load_dotenv(env_file, override=False)

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('Set DATABASE_URL in backend/.env (copy .env.example first). PostgreSQL is required.')
DATA_DIR = Path(os.environ.get('DATA_DIR', 'data')).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = BASE_DIR / DATA_DIR
DATA_DIR = DATA_DIR.resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / 'uploads').mkdir(exist_ok=True)
