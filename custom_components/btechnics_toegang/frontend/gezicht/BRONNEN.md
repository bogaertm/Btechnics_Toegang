# Bronnen gezichtsherkenning

| Bestand | Bron | Licentie |
|---|---|---|
| vision_bundle.mjs, wasm/* | @mediapipe/tasks-vision 0.10.21 (npm) | Apache 2.0 |
| face_landmarker.task | storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1 (sha256 64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff) | Apache 2.0 |
| mfn.json, mfn.bin | omgezet uit mobilefacenet.tflite van MCarlomagno/FaceRecognitionAuth (sha256 be4bc7cfc53f7bc336d0f28b1ab92535f618c913a422b683210750f6b5354854), zelfde gewichten als de tablet | BSD 3-Clause, Copyright (c) 2020 Marcos Carlomagno |
| mfn.mjs, gezicht.mjs | Btechnics | |

mfn.mjs voert de graaf uit zonder externe runtime. Getoetst tegen TensorFlow Lite: cosinus 1,0000 (max verschil 8e-7).
De volledige keten (5 punten, uitlijning, model) geeft cosinus 0,99999 tegen OpenCV warpAffine + TensorFlow Lite.
