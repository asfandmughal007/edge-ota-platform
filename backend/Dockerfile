# syntax=docker/dockerfile:1
# Build context is the repository root:  docker build -f backend/Dockerfile .

FROM python:3.15-slim AS build
WORKDIR /src
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/src ./src
# Installed into /install so the runtime image gets only the finished packages,
# with no build tooling or pip cache.
RUN pip install --no-cache-dir --prefix=/install ".[postgres]"


FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /install /usr/local
USER app
WORKDIR /app
EXPOSE 8000

# /readyz also checks the database, so an unhealthy container means "cannot serve", not just "alive".
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).status == 200 else 1)"]

# --proxy-headers makes X-Forwarded-Proto from a trusted TLS proxy visible to the app
# (trusted addresses come from FORWARDED_ALLOW_IPS, default 127.0.0.1).
CMD ["uvicorn", "device_mgmt.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
