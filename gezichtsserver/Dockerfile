# Btechnics gezichtsserver (container op Unraid naast Frigate)
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA=/data
WORKDIR /app
COPY requirements.txt .
# mediapipe trekt opencv-contrib-python mee (met GUI bibliotheken); vervangen door de headless versie
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y opencv-contrib-python opencv-python 2>/dev/null; \
    pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless==5.0.0.93 \
 && python -c "import cv2, mediapipe, onnxruntime; from ai_edge_litert.interpreter import Interpreter"
COPY modellen ./modellen
COPY btx_gezicht ./btx_gezicht
RUN useradd -u 99 -g 100 -M -s /usr/sbin/nologin btx 2>/dev/null || true
VOLUME /data
USER 99:100
CMD ["python", "-m", "btx_gezicht"]
