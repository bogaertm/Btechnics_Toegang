# Bronnen gezichtsserver

| Bestand | Bron | Licentie |
|---|---|---|
| modellen/face_landmarker.task | storage.googleapis.com/mediapipe-models/face_landmarker (sha256 64184e22...), zelfde als het portaal | Apache 2.0 |
| modellen/mobilefacenet.tflite | MCarlomagno/FaceRecognitionAuth (sha256 be4bc7cf...), zelfde gewichten als gsm en tablet | BSD 3-Clause, Copyright (c) 2020 Marcos Carlomagno |
| modellen/2.7_80x80_MiniFASNetV2.onnx, modellen/4_0_0_80x80_MiniFASNetV1SE.onnx | omgezet naar ONNX uit de .pth van minivision-ai/Silent-Face-Anti-Spoofing (sha256 a5eb02e1... en 84ee1d37...), verschil met PyTorch kleiner dan 2e-8 | Apache 2.0 (LICENSE-minifasnet) |
| btx_gezicht/* | Btechnics | |

Getoetst: de keten (MediaPipe punten, uitlijning, MobileFaceNet) geeft cosinus 0,99999 tegen de afdruk die het portaal op de gsm maakt.
| modellen/face_detection_yunet_2023mar.onnx | opencv/opencv_zoo face_detection_yunet (sha256 8f2383e4...) | MIT |
