import os

SECRET_KEY = os.getenv("SECRET_KEY", "local-development-only")
if os.getenv("PUBLIC_DEPLOYMENT") == "1":
    if not os.getenv("ACCESS_PASSWORD") or SECRET_KEY == "local-development-only":
        raise RuntimeError("Public deployment requires ACCESS_PASSWORD and a unique SECRET_KEY.")
DEBUG = False
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(",")
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = []
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
DATA_UPLOAD_MAX_MEMORY_SIZE = 100_000
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

