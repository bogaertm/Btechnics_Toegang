# Btechnics gezichtsserver (container op Unraid naast Frigate)
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA=/data
WORKDIR /app
# MediaPipe laadt libEGL en libGLESv2 (ook op CPU)
RUN apt-get update && apt-get install -y --no-install-recommends libegl1 libgles2 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# mediapipe trekt opencv-contrib-python mee (met GUI bibliotheken); vervangen door de headless versie
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y opencv-contrib-python opencv-python 2>/dev/null; \
    pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless==5.0.0.93 \
 && python -c "import cv2, mediapipe, onnxruntime; from ai_edge_litert.interpreter import Interpreter"
COPY modellen ./modellen
COPY btx_gezicht ./btx_gezicht
# alle modellen echt laden, zodat een ontbrekende bibliotheek al bij het bouwen opvalt
RUN python -c "from btx_gezicht.herkenning import Herkenner; Herkenner(); print('modellen ok')"
VOLUME /data
CMD ["python", "-m", "btx_gezicht"]
