# syntax=docker/dockerfile:1
# Build context is the repository root:  docker build -f agent/Dockerfile .
# Used both for the device simulators and as the image the agent ships in.

FROM python:3.12-slim AS build
WORKDIR /src
COPY agent/pyproject.toml ./pyproject.toml
COPY agent/src ./src
RUN pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin agent
COPY --from=build /install /usr/local
USER agent
WORKDIR /home/agent

# The agent needs the Docker API to manage the application container. Access to
# the socket is root-equivalent on the host: grant it with `group_add` (the
# docker group's GID) rather than running this container as root, and in
# production put a filtering socket proxy in front of it. See the README.
ENTRYPOINT ["edge-agent"]
