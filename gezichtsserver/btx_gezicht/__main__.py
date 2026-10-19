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
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .beslissing import Beslisser, Instellingen
from .ha import HA, wacht_tot
from .herkenning import Herkenner

VERSIE = "0.3.0"
LOG = logging.getLogger("btx")


def env(naam, standaard, soort=str):
    w = os.environ.get(naam)
    return standaard if w in (None, "") else soort(w)


WAAKHOND_S = 10.0


def iso(t_wand: float | None) -> str | None:
    return None if not t_wand else datetime.fromtimestamp(t_wand).astimezone().replace(microsecond=0).isoformat()


class Camera(threading.Thread):
    """Leest de RTSP stroom en houdt enkel het laatste beeld bij (oude beelden vallen weg)."""

    def __init__(self, url: str, stop: threading.Event):
        super().__init__(daemon=True)
        self.url, self.stop = url, stop
        self.beeld = None
        self.t = 0.0
        self.n = 0
        self.ok = False                 # recent een beeld gekregen
        self.laatst_wand = None         # wandtijd van het laatste beeld (voor de status)
        self.herstarts = 0
        self._wacht_vanaf = time.monotonic()   # waakhond telt vanaf het laatste beeld of de laatste (her)start
        self._opnieuw = threading.Event()
        self._slot = threading.Lock()

    def run(self):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;10000000")
        while not self.stop.is_set():
            self._opnieuw.clear()
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                LOG.warning("camera niet bereikbaar: %s", self.url.split("@")[-1])
                self.stop.wait(10)
                continue
            LOG.info("camera open: %dx%d", int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            bestand = not self.url.startswith(("rtsp", "http"))   # testvideo: aan het eigen tempo afspelen
            tempo = 1 / (cap.get(cv2.CAP_PROP_FPS) or 6)
            while not self.stop.is_set() and not self._opnieuw.is_set():
                if bestand:
                    time.sleep(tempo)
                ok, b = cap.read()
                if not ok:
                    LOG.warning("camera: geen beeld meer, opnieuw verbinden")
                    break
                with self._slot:
                    self.beeld, self.t = b, time.monotonic()
                    self.n += 1
                    self.ok, self.laatst_wand = True, time.time()
            cap.release()
            self.stop.wait(3)

    def laatste(self):
        with self._slot:
            return self.beeld, self.t

    def waakhond(self, nu: float | None = None) -> bool:
        """True als er al meer dan WAAKHOND_S geen nieuw beeld kwam: opnieuw verbinden forceren en camera_ok uit.
        Daarna telt de waakhond opnieuw vanaf nu (geen herhaling bij elk beeld)."""
        nu = time.monotonic() if nu is None else nu
        with self._slot:
            if nu - max(self.t, self._wacht_vanaf) <= WAAKHOND_S:
                return False
            self._wacht_vanaf = nu
            self.ok = False
            self.beeld = None
            self.herstarts += 1
        self._opnieuw.set()
        return True

    def status(self) -> dict:
        return {"camera_ok": self.ok, "camera_laatst": iso(self.laatst_wand), "camera_beelden": self.n,
                "camera_herstarts": self.herstarts}


class Foutlog:
    """Stacktraces beperken: dezelfde sleutel hoogstens een keer per per_s seconden, de rest enkel geteld."""

    def __init__(self, per_s: float = 60.0):
        self.per_s = per_s
        self._laatst: dict[str, float] = {}
        self.overgeslagen = 0

    def fout(self, omschrijving: str, sleutel: str | None = None, nu: float | None = None) -> bool:
        nu = time.monotonic() if nu is None else nu
        tekst = traceback.format_exc()
        k = sleutel if sleutel is not None else tekst
        if nu - self._laatst.get(k, -1e9) < self.per_s:
            self.overgeslagen += 1
            return False
        self._laatst[k] = nu
        if len(self._laatst) > 100:   # geen groeiend geheugen bij steeds andere fouten
            for oud in sorted(self._laatst, key=self._laatst.get)[:50]:
                del self._laatst[oud]
        LOG.error("%s (zelfde fout hoogstens 1 keer per %d s)\n%s", omschrijving, int(self.per_s), tekst.rstrip())
        return True


def waakhond(cam: Camera, b: Beslisser, nu: float | None = None) -> bool:
    """Geen nieuw beeld meer: de sessie sluiten (geen spoor over een gat heen) en de camera opnieuw laten verbinden."""
    if not cam.waakhond(nu):
        return False
    b.sessie = None
    LOG.warning("camera: al %d s geen nieuw beeld, sessie gesloten en opnieuw verbinden", int(WAAKHOND_S))
    return True


def samenvatting_tekst(z: dict, naam: str | None) -> str:
    def f(x, fmt="{}"):
        return "-" if x is None else fmt.format(x)
    tekst = f"Proef meting: {z['duur']} s, {z['beelden']} beelden, oog max {z['oog_max']} px, recht {int(z['recht'] * 100)}%"
    if naam and "cos_max" in z:
        tekst += (f", beste {naam} cos {z['cos_max']} bij oog {z['oog_bij_max']} px (mediaan dichtbij {f(z.get('cos_mediaan_dicht'))},"
                  f" ver {f(z.get('cos_mediaan_ver'))}), echt {f(z.get('echt_mediaan'))}, scherpte {z['scherpte']:.0f}")
    else:
        tekst += ", geen afdruk gemeten"
    if z.get("meerdere"):
        tekst += ", meerdere gezichten in beeld"
    return tekst


def main():
    logging.basicConfig(level=env("LOGNIVEAU", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data = Path(env("DATA", "/data"))
    camera_url = env("CAMERA", "")
    if not camera_url:
        raise SystemExit("CAMERA ontbreekt (bv. rtsp://192.168.1.185:8554/VTO_Magazijn)")
    proef = env("PROEF", "1") not in ("0", "nee", "false", "uit")
    inst = Instellingen(drempel=env("DREMPEL", 0.70, float), marge=env("MARGE", 0.10, float), cohorte=env("COHORTE", 0.30, float),
                        echt_min=env("ECHT_MIN", 0.85, float),
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
    b = Beslisser(inst, meten=proef, fps=fps)
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
    threading.Thread(target=ha.status_lus, args=(stop, lambda: {"proef": proef, "versie": VERSIE, **b.stats, **ha.stats, **cam.status()}),
                     daemon=True).start()
    ha.log("systeem", tekst=f"Gezichtsserver gestart (v{VERSIE}){', proefmodus: meet enkel' if proef else ''}")

    fl = Foutlog(60.0)
    vorige_t = 0.0
    while not stop.is_set():
        waakhond(cam, b)
        beeld, t = cam.laatste()
        if beeld is None or t == vorige_t:
            stop.wait(0.05)
            continue
        vorige_t = t
        begin = time.monotonic()
        besluit = None
        try:
            rgb = cv2.cvtColor(beeld, cv2.COLOR_BGR2RGB)
            gs = h.gezichten(rgb, int(t * 1000))
            besluit = b.stap(h, rgb, gs[0] if gs else None, ha.personen, t, gs[1:])
        except Exception:  # noqa: BLE001
            b.stats["fouten"] += 1
            b.sessie = None   # na een fout geen half spoor verder gebruiken
            fl.fout("verwerking")
        if besluit:
            try:
                verwerk(ha, besluit, proef, b.stats)
            except Exception:  # noqa: BLE001
                b.stats["fouten"] += 1
                fl.fout("verwerk", sleutel="verwerk")
        if b.samenvatting:
            z, b.samenvatting = b.samenvatting, None
            try:
                naam = (ha.personen.get(z.get("persoon_id")) or {}).get("naam", "?") if z.get("persoon_id") else None
                tekst = samenvatting_tekst(z, naam)
                LOG.info(tekst)
                if naam or z["oog_max"] >= 30:
                    ha.log("systeem", tekst=tekst)
            except Exception:  # noqa: BLE001
                b.stats["fouten"] += 1
                fl.fout("samenvatting", sleutel="samenvatting")
        wacht = 1 / fps - (time.monotonic() - begin)
        if wacht > 0:
            stop.wait(wacht)
    LOG.info("gestopt")


def verwerk(ha: HA, besluit: dict, proef: bool, stats: dict | None = None) -> None:
    stats = {} if stats is None else stats
    pid = besluit.get("persoon_id")
    naam = (ha.personen.get(pid) or {}).get("naam", "?")
    cijfers = (f"cos {besluit.get('cos')}, tweede {besluit.get('tweede')}, echt {besluit.get('echt')}, "
               f"diepte {besluit.get('diepte')}, oog {besluit.get('oog')} px")
    if besluit.get("reden") == "te_lang":
        cijfers += ", geen besluit binnen de maximale duur"
    if besluit.get("meerdere"):
        cijfers += ", meerdere gezichten"
    actie = besluit["actie"]
    LOG.info("%s: %s (%s)", actie, naam if actie != "onbekend" else "onbekend gezicht", cijfers)
    if proef:
        if actie == "open":
            stats["proef_open"] = stats.get("proef_open", 0) + 1
        tekst = {"open": f"Proef: zou openen voor {naam}", "foto": f"Proef: vermoedelijke foto van {naam}",
                 "onzeker": f"Proef: {naam} herkend, echtheid onzeker",
                 "onbekend": "Proef: onbekend gezicht"}.get(actie, f"Proef: {actie}")
        ha.log("systeem", tekst=f"{tekst} ({cijfers})")
        return
    if actie == "open":
        geopend, reden = ha.openen(pid, besluit["cos"])
        if geopend:
            stats["geopend"] = stats.get("geopend", 0) + 1
        else:
            LOG.info("HA opende niet voor %s: %s", naam, reden)
    elif actie == "foto":
        ha.log("geweigerd", methode="gezicht", reden="vermoedelijke_foto", persoon_id=pid, score=besluit["echt"])
    elif actie == "onzeker":
        ha.log("systeem", tekst=f"{naam} herkend maar echtheid onzeker, niet geopend ({cijfers})")


if __name__ == "__main__":
    main()
