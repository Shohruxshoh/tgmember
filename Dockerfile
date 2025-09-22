# ======== 1️⃣ Builder Stage ========
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt --target=/install \
    && pip install --no-cache-dir -r requirements.txt


# ======== 2️⃣ Final Stage ========
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/appuser/.local/bin:/usr/local/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl bash \
  && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash appuser

# Kutubxonalarni olib kelamiz
COPY --from=builder /install /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . /app

COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && chown -R appuser:appuser /app

USER appuser
ENTRYPOINT ["/app/entrypoint.sh"]
