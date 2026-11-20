FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy opencv-python-headless

COPY assets/sample_baby_band.jpg /app/assets/sample_baby_band.jpg
COPY detector/src/cv_compat.py /app/detector/src/cv_compat.py
COPY mock-camera/mock_streamer.py /app/mock-camera/mock_streamer.py

ENV SAMPLE_IMAGE_PATH=/app/assets/sample_baby_band.jpg
ENV MOCK_CAMERA_PORT=8090

EXPOSE 8090

CMD ["python", "/app/mock-camera/mock_streamer.py"]

