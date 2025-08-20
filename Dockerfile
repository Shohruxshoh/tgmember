FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl bash libpq-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Loyiha fayllari
COPY . /app

# Entry
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

## Non-root foydalanuvchi (ixtiyoriy, xavfsizlik uchun yaxshi)
#RUN useradd -ms /bin/bash appuser
#RUN mkdir -p /app/static /app/media && chown -R appuser:appuser /app
#USER appuser

CMD ["gunicorn", "core.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8080", "--workers", "8", "--threads", "2", "--timeout", "120", "--log-level", "warning"]
ENTRYPOINT ["/app/entrypoint.sh"]
