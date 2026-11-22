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
    'ai-edge-litert==2.4.0' \
    'google-cloud-firestore>=2.16,<3' \
    'google-cloud-storage>=2.18,<4' \
    'pywebpush>=2.0,<3'

# Google MediaPipe's 16 kHz YAMNet model, including its class labels.
ADD --checksum=sha256:4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite /app/models/yamnet.tflite

COPY detector/config.yaml /app/config.yaml
COPY detector/src /app/src

ENV EDGE_CONFIG_PATH=/app/config.yaml
ENV EDGE_STATUS_PORT=8095
ENV PYTHONPATH=/app/src

EXPOSE 8095

CMD ["python", "/app/src/main.py"]

