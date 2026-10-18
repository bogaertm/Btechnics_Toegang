"""Btechnics gezichtsserver: herkent gezichten op de camera van een deur en vraagt HA om te openen.

Instellingen via omgevingsvariabelen (zie README.md). Start: python -m btx_gezicht
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .beslissing import Beslisser, Instellingen
from .ha import HA, wacht_tot
from .herkenning import Herkenner

VERSIE = "0.2.0"
LOG = logging.getLogger("btx")


def env(naam, standaard, soort=str):
    w = os.environ.get(naam)
    return standaard if w in (None, "") else soort(w)


class Camera(threading.Thread):
    """Leest de RTSP stroom en houdt enkel het laatste beeld bij (oude beelden vallen weg)."""

    def __init__(self, url: str, stop: threading.Event):
        super().__init__(daemon=True)
        self.url, self.stop = url, stop
        self.beeld = None
        self.t = 0.0
        self.n = 0
        self._slot = threading.Lock()

    def run(self):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;10000000")
        while not self.stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                LOG.warning("camera niet bereikbaar: %s", self.url.split("@")[-1])
                self.stop.wait(10)
                continue
            LOG.info("camera open: %dx%d", int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            bestand = not self.url.startswith(("rtsp", "http"))   # testvideo: aan het eigen tempo afspelen
            tempo = 1 / (cap.get(cv2.CAP_PROP_FPS) or 6)
            while not self.stop.is_set():
                if bestand:
                    time.sleep(tempo)
                ok, b = cap.read()
                if not ok:
                    LOG.warning("camera: geen beeld meer, opnieuw verbinden")
                    break
                with self._slot:
                    self.beeld, self.t = b, time.monotonic()
                    self.n += 1
            cap.release()
            self.stop.wait(3)

    def laatste(self):
        with self._slot:
            return self.beeld, self.t


def main():
    logging.basicConfig(level=env("LOGNIVEAU", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data = Path(env("DATA", "/data"))
    camera_url = env("CAMERA", "")
    if not camera_url:
        raise SystemExit("CAMERA ontbreekt (bv. rtsp://192.168.1.185:8554/VTO_Magazijn)")
    proef = env("PROEF", "1") not in ("0", "nee", "false", "uit")
    inst = Instellingen(drempel=env("DREMPEL", 0.70, float), echt_min=env("ECHT_MIN", 0.85, float),
                        echt_hoog=env("ECHT_HOOG", 0.97, float), diepte_min=env("DIEPTE_MIN", 2.5, float),
                        oog_min=env("OOG_MIN", 45.0, float), hoek_max=env("HOEK_MAX", 15.0, float), stil_s=env("STIL_S", 2.0, float))
    fps = env("FPS", 8.0, float)
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())

    ha = HA(env("HA_URL", "https://192.168.1.117:8443"), data, env("NAAM", "Gezichtsserver"), VERSIE)
    if not ha.gekoppeld:
        code = env("KOPPELCODE", "")
        if not code:
            raise SystemExit("Nog niet gekoppeld: zet KOPPELCODE (Deuren, Tablet of gezichtsserver koppelen in HA)")
        ha.koppelen(code, env("TOESTEL_ID", f"gezichtsserver-{socket.gethostname()}"))
    LOG.info("start v%s, %s, sleutel %s", VERSIE, "PROEFMODUS (meet enkel, opent niet)" if proef else "LIVE", ha.pubkey_id[:16])

    h = Herkenner(threads=env("THREADS", 2, int))
    b = Beslisser(inst, meten=proef)
    cam = Camera(camera_url, stop)

    def fotovraag(uid):
        beeld, _ = cam.laatste()
        if beeld is None:
            return
        k = cv2.resize(beeld, (640, int(beeld.shape[0] * 640 / beeld.shape[1])), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", k, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            ha.foto(uid, jpg.tobytes())

    ha.bij_fotovraag = fotovraag
    wacht_tot(stop, ha.lijst_halen, "eerste lijst")
    cam.start()
    threading.Thread(target=ha.wacht_lus, args=(stop,), daemon=True).start()
    threading.Thread(target=ha.status_lus, args=(stop, lambda: {"proef": proef, **b.stats, "camera_beelden": cam.n}), daemon=True).start()
    ha.log("systeem", tekst=f"Gezichtsserver gestart (v{VERSIE}){', proefmodus: meet enkel' if proef else ''}")

    vorige_t = 0.0
    while not stop.is_set():
        beeld, t = cam.laatste()
        if beeld is None or t == vorige_t:
            stop.wait(0.05)
            continue
        vorige_t = t
        rgb = cv2.cvtColor(beeld, cv2.COLOR_BGR2RGB)
        begin = time.monotonic()
        try:
            gs = h.gezichten(rgb, int(t * 1000))
            g = gs[0] if gs else None
            besluit = b.stap(h, rgb, g, ha.personen, t)
        except Exception:  # noqa: BLE001
            LOG.exception("verwerking")
            besluit, g = None, None
        if besluit:
            verwerk(ha, besluit, proef)
        if b.samenvatting:
            z, b.samenvatting = b.samenvatting, None
            naam = (ha.personen.get(z.get("persoon_id")) or {}).get("naam", "?") if z.get("persoon_id") else None
            tekst = (f"Proef meting: {z['duur']} s, {z['beelden']} beelden, oog max {z['oog_max']} px, recht {int(z['recht'] * 100)}%"
                     + (f", beste {naam} cos {z['cos_max']} (mediaan {z['cos_mediaan']}) bij oog {z['oog_bij_max']} px, echt {z['echt_mediaan']}, scherpte {z['scherpte']:.0f}"
                        if naam else ", geen afdruk gemeten"))
            LOG.info(tekst)
            if naam or z["oog_max"] >= 30:
                ha.log("systeem", tekst=tekst)
        wacht = 1 / fps - (time.monotonic() - begin)
        if wacht > 0:
            stop.wait(wacht)
    LOG.info("gestopt")


def verwerk(ha: HA, besluit: dict, proef: bool) -> None:
    pid = besluit.get("persoon_id")
    naam = (ha.personen.get(pid) or {}).get("naam", "?")
    cijfers = f"cos {besluit['cos']}, tweede {besluit['tweede']}, echt {besluit['echt']}, diepte {besluit['diepte']}, oog {besluit['oog']} px"
    actie = besluit["actie"]
    LOG.info("%s: %s (%s)", actie, naam if actie != "onbekend" else "onbekend gezicht", cijfers)
    if proef:
        tekst = {"open": f"Proef: zou openen voor {naam}", "foto": f"Proef: vermoedelijke foto van {naam}",
                 "onzeker": f"Proef: {naam} herkend, echtheid onzeker", "geblokkeerd": f"Proef: {naam} tijdelijk geblokkeerd",
                 "onbekend": "Proef: onbekend gezicht"}[actie]
        ha.log("systeem", tekst=f"{tekst} ({cijfers})")
        return
    if actie == "open":
        geopend, reden = ha.openen(pid, besluit["cos"])
        if not geopend:
            LOG.info("HA opende niet voor %s: %s", naam, reden)
    elif actie == "foto":
        ha.log("geweigerd", methode="gezicht", reden="vermoedelijke_foto", persoon_id=pid, score=besluit["echt"])
    elif actie == "onzeker":
        ha.log("systeem", tekst=f"{naam} herkend maar echtheid onzeker, niet geopend ({cijfers})")


if __name__ == "__main__":
    main()
