FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates docker-cli docker-compose build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements.txt /tmp/requirements.txt
COPY requirements-solver-lock.txt /tmp/requirements-solver-lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/requirements-solver-lock.txt

CMD ["sleep", "infinity"]
