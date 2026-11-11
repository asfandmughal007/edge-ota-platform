FROM python:3.12-alpine

ARG APP_VERSION=0.0.0
ARG APP_BROKEN=false

# The agent validates this label against the version it was told to deploy.
LABEL org.opencontainers.image.title="my-edge-app" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV APP_VERSION=${APP_VERSION} \
    APP_BROKEN=${APP_BROKEN} \
    APP_PORT=8080 \
    PYTHONUNBUFFERED=1

RUN adduser -D -u 10001 app
USER app
WORKDIR /app
COPY app.py .

EXPOSE 8080
CMD ["python", "app.py"]
