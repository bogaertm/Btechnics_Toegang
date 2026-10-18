"""Beeldverwerking: MediaPipe punten, ArcFace uitlijning, MobileFaceNet afdruk, MiniFASNet echtheid.

Zelfde keten als de gsm en de tablet (afspraak mfn112-arcface5-v1): irissen 468 en 473, neusbasis 2, mondhoeken 61 en 291,
gelijkvormigheid naar het ArcFace sjabloon 112x112, bilineair, RGB, (p - 127.5) / 128, 192 waarden L2 genormaliseerd.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODELLEN = Path(__file__).resolve().parent.parent / "modellen"
VERSIE = "mfn112-arcface5-v1"
SJABLOON = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
                    dtype=np.float64)
OGEN, NEUS, MOND = (468, 473), 2, (61, 291)
# punten voor de vormvergelijking bij naderen (3D controle): neus, ogen, wenkbrauwen, mond, kaaklijn
VORM = [1, 2, 4, 5, 6, 10, 33, 61, 93, 133, 152, 168, 197, 234, 263, 291, 323, 362, 454, 105, 334, 70, 300, 172, 397, 58, 288]


@dataclass
class Gezicht:
    punten: np.ndarray          # (478, 3) in pixels van het volle beeld, z geschaald zoals x
    vijf: np.ndarray            # (5, 2)
    oog: float                  # afstand tussen de irissen in pixels
    draai: float                # graden links/rechts
    kantel: float               # graden op/neer
    vak: tuple[int, int, int, int]  # x, y, b, h
    midden: tuple[float, float]
    scherpte: float = 0.0
    helderheid: float = 0.0
    afdruk: np.ndarray | None = None
    echt: float | None = None
    extra: dict = field(default_factory=dict)


def gelijkvormig(bron: np.ndarray, doel: np.ndarray = SJABLOON) -> np.ndarray:
    """Kleinste kwadraten gelijkvormigheid (Umeyama) van bron naar doel, als 2x3 matrix [a, -b, tx; b, a, ty]."""
    sm, dm = bron.mean(0), doel.mean(0)
    x, d = bron - sm, doel - dm
    sxx = (x ** 2).sum()
    a = (x[:, 0] * d[:, 0] + x[:, 1] * d[:, 1]).sum() / sxx
    b = (x[:, 0] * d[:, 1] - x[:, 1] * d[:, 0]).sum() / sxx
    return np.array([[a, -b, dm[0] - (a * sm[0] - b * sm[1])], [b, a, dm[1] - (b * sm[0] + a * sm[1])]])


def uitlijnen(rgb: np.ndarray, vijf: np.ndarray) -> np.ndarray:
    """112x112 RGB uitsnede als float32 (p - 127.5) / 128."""
    m = gelijkvormig(vijf.astype(np.float64))
    uit = cv2.warpAffine(rgb, m, (112, 112), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return (uit.astype(np.float32) - 127.5) / 128.0


def scherpte(invoer: np.ndarray) -> float:
    g = invoer.mean(axis=2) * 128 + 127.5
    lap = cv2.Laplacian(g.astype(np.float32), cv2.CV_32F, ksize=1)
    return float(lap[1:-1, 1:-1].var())


def vorm_afwijking(a: np.ndarray, b: np.ndarray) -> float:
    """Restfout na gelijkvormigheid tussen twee puntenwolken (2D), relatief tot de grootte.
    Een vlakke foto die dichterbij komt blijft gelijkvormig (rest = ruis); een echt hoofd vervormt door het perspectief."""
    m = gelijkvormig(a, b)
    a2 = a @ m[:, :2].T + m[:, 2]
    schaal = np.sqrt(((b - b.mean(0)) ** 2).sum(1).mean())
    return float(np.sqrt(((a2 - b) ** 2).sum(1).mean()) / schaal)


class Herkenner:
    def __init__(self, modellen: Path = MODELLEN, threads: int = 2):
        import mediapipe as mp
        from ai_edge_litert.interpreter import Interpreter
        import onnxruntime as ort

        opties = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(modellen / "face_landmarker.task")),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=2,
            min_face_detection_confidence=0.6, min_face_presence_confidence=0.6, min_tracking_confidence=0.6)
        self._mp = mp
        self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(opties)
        self.mfn = Interpreter(model_path=str(modellen / "mobilefacenet.tflite"), num_threads=threads)
        self.mfn.allocate_tensors()
        self._in = self.mfn.get_input_details()[0]["index"]
        self._uit = self.mfn.get_output_details()[0]["index"]
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.fas = [(2.7, ort.InferenceSession(str(modellen / "2.7_80x80_MiniFASNetV2.onnx"), so, providers=["CPUExecutionProvider"])),
                    (4.0, ort.InferenceSession(str(modellen / "4_0_0_80x80_MiniFASNetV1SE.onnx"), so, providers=["CPUExecutionProvider"]))]
        self._ts = 0

    # ------------------------------------------------------------------ punten

    def gezichten(self, rgb: np.ndarray, ts_ms: int, analyse_breedte: int = 1280) -> list[Gezicht]:
        """Gezichten in een RGB beeld (volle resolutie). De punten worden gezocht op een verkleind beeld."""
        h, w = rgb.shape[:2]
        if w > analyse_breedte:
            klein = cv2.resize(rgb, (analyse_breedte, int(h * analyse_breedte / w)), interpolation=cv2.INTER_AREA)
        else:
            klein = rgb
        ts_ms = max(ts_ms, self._ts + 1)
        self._ts = ts_ms
        res = self.landmarker.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                                                              data=np.ascontiguousarray(klein)), ts_ms)
        uit = []
        for lm in res.face_landmarks or []:
            p = np.array([[q.x * w, q.y * h, q.z * w] for q in lm], dtype=np.float64)
            uit.append(self._maak(p))
        uit.sort(key=lambda g: -g.oog)
        return uit

    @staticmethod
    def _maak(p: np.ndarray) -> Gezicht:
        ogen = sorted([p[OGEN[0], :2], p[OGEN[1], :2]], key=lambda q: q[0])
        mond = sorted([p[MOND[0], :2], p[MOND[1], :2]], key=lambda q: q[0])
        vijf = np.array([ogen[0], ogen[1], p[NEUS, :2], mond[0], mond[1]])
        a, b, boven, onder = p[33], p[263], p[10], p[152]
        draai = math.degrees(math.atan2(b[2] - a[2], b[0] - a[0]))
        kantel = math.degrees(math.atan2(onder[2] - boven[2], onder[1] - boven[1]))
        x0, y0 = p[:, :2].min(0)
        x1, y1 = p[:, :2].max(0)
        return Gezicht(punten=p, vijf=vijf, oog=float(np.linalg.norm(vijf[1] - vijf[0])), draai=draai, kantel=kantel,
                       vak=(int(x0), int(y0), int(x1 - x0), int(y1 - y0)), midden=(float((x0 + x1) / 2), float((y0 + y1) / 2)))

    # ------------------------------------------------------------------ afdruk en echtheid

    def afdruk(self, rgb: np.ndarray, g: Gezicht) -> np.ndarray:
        invoer = uitlijnen(rgb, g.vijf)
        g.scherpte = scherpte(invoer)
        g.helderheid = float(invoer.mean() * 128 + 127.5)
        self.mfn.set_tensor(self._in, invoer[None])
        self.mfn.invoke()
        y = self.mfn.get_tensor(self._uit)[0].astype(np.float32)
        g.afdruk = y / (np.linalg.norm(y) + 1e-9)
        return g.afdruk

    def echtheid(self, rgb: np.ndarray, g: Gezicht) -> float:
        """Kans op een echt gezicht (0..1) volgens MiniFASNet V2 (2.7) en V1SE (4.0), zoals Silent-Face-Anti-Spoofing:
        BGR, 80x80, geen normalisatie, softmax opgeteld; klasse 1 is echt."""
        bgr = rgb[:, :, ::-1]
        h, w = bgr.shape[:2]
        x, y, bw, bh = g.vak
        som = np.zeros(3)
        for schaal, sessie in self.fas:
            s = min((h - 1) / bh, (w - 1) / bw, schaal)
            nw, nh = bw * s, bh * s
            cx, cy = x + bw / 2, y + bh / 2
            l, t, r, b = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
            if l < 0: r -= l; l = 0
            if t < 0: b -= t; t = 0
            if r > w - 1: l -= r - w + 1; r = w - 1
            if b > h - 1: t -= b - h + 1; b = h - 1
            snit = bgr[int(t):int(b) + 1, int(l):int(r) + 1]
            beeld = cv2.resize(snit, (80, 80)).astype(np.float32).transpose(2, 0, 1)[None]
            o = sessie.run(None, {"beeld": np.ascontiguousarray(beeld)})[0][0]
            e = np.exp(o - o.max())
            som += e / e.sum()
        g.echt = float(som[1] / 2)
        return g.echt

    @staticmethod
    def vorm(g: Gezicht) -> np.ndarray:
        return g.punten[VORM, :2].copy()
