FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY detector/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir \
    numpy \
    opencv-python-headless \
    PyYAML \
    google-cloud-storage \
    google-cloud-firestore \
    firebase-admin

COPY detector/config.yaml /app/config.yaml
COPY detector/src /app/src

ENV EDGE_CONFIG_PATH=/app/config.yaml
ENV EDGE_STATUS_PORT=8095
ENV PYTHONPATH=/app/src

EXPOSE 8095

CMD ["python", "/app/src/main.py"]

