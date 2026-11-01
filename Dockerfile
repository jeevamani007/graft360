# Graft 360 web app (FastAPI + frontend) image
# Build context = the graft360 project folder (where this file lives)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

# Layout inside the container must match the project:
#   /app/backend  (python code)    /app/frontend  (html, css, js)
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

# run as a normal user, not root
RUN useradd --create-home --shell /usr/sbin/nologin appuser && chown -R appuser /app
USER appuser

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/batches', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]