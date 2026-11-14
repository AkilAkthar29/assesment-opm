FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY catalog catalog
COPY config config
COPY manage.py .
COPY tests tests
COPY scripts scripts

