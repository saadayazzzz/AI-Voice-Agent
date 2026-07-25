FROM python:3.12-slim

# Hugging Face Spaces runs containers as UID 1000 and mounts the app directory
# read-only, so create that user up front and keep writable state in /tmp.
# Running as non-root is the right default on every other host too.
RUN useradd -m -u 1000 appuser

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////tmp/voice_agent.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

USER appuser

EXPOSE 8000

# Shell form so ${PORT} expands: hosts assign the port at runtime and expect
# the app to bind it. Falls back to 8000, which matches the Space's app_port.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
