"""Beslissen of de deur open mag: groot recht gezicht, 2 s stil, dezelfde persoon, echt (MiniFASNet) en 3D bij naderen.

Alles gebeurt zonder actie van de persoon (geen knipperen). In proefmodus wordt enkel gemeten en gemeld.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .herkenning import Gezicht, Herkenner, vorm_afwijking


@dataclass
class Instellingen:
    drempel: float = 0.70        # cosinus met de beste afdruk van de persoon (mediaan over het venster)
    marge: float = 0.05          # verschil met de tweede beste persoon
    echt_min: float = 0.85       # MiniFASNet, mediaan over het venster
    echt_hoog: float = 0.97      # zonder 3D bewijs bij naderen is dit nodig
    diepte_min: float = 2.5      # vervorming bij naderen gedeeld door de ruis
    oog_min: float = 70.0        # pixels tussen de irissen in het volle beeld (dichtbij)
    hoek_max: float = 15.0       # graden draai en kanteling
    stil_s: float = 2.0          # zoveel seconden stil en herkend
    beweging_max: float = 0.15   # verplaatsing van het midden in het venster, als deel van de oogafstand
    scherpte_min: float = 40.0
    wacht_na_open: float = 20.0  # zelfde persoon niet opnieuw openen binnen zoveel seconden
    foto_blok: float = 300.0     # na een vermoedelijke foto: die persoon zoveel seconden niet openen


@dataclass
class Meting:
    t: float
    oog: float
    midden: tuple[float, float]
    vorm: np.ndarray
    recht: bool
    dichtbij: bool
    persoon: str | None = None
    cos: float = 0.0
    tweede: float = 0.0
    echt: float | None = None
    scherpte: float = 0.0


@dataclass
class Sessie:
    begin: float
    metingen: deque = field(default_factory=lambda: deque(maxlen=200))
    gemeld: bool = False


class Beslisser:
    def __init__(self, inst: Instellingen, meten: bool = False):
        self.inst = inst
        self.meten = meten          # proefmodus: ook verder dan oog_min meten en per sessie een samenvatting geven
        self.samenvatting: dict | None = None
        self.sessie: Sessie | None = None
        self.laatst_gezien = 0.0
        self.geopend: dict[str, float] = {}
        self.geblokkeerd: dict[str, float] = {}
        self.stats = {"sessies": 0, "geopend": 0, "proef_open": 0, "foto": 0, "onbekend": 0}

    def match(self, afdruk: np.ndarray, personen: dict) -> tuple[str | None, float, float]:
        beste, b1, b2 = None, -1.0, -1.0
        for pid, p in personen.items():
            c = float((p["afdrukken"] @ afdruk).max())
            if c > b1:
                beste, b2, b1 = pid, b1, c
            elif c > b2:
                b2 = c
        return beste, b1, max(b2, 0.0)

    def stap(self, h: Herkenner, rgb, g: Gezicht | None, personen: dict, nu: float | None = None) -> dict | None:
        """Een beeld verwerken. Geeft een besluit terug: {"actie": "open"|"foto"|..., ...} of None."""
        nu = nu or time.monotonic()
        inst = self.inst
        if g is None:
            if self.sessie and nu - self.laatst_gezien > 2.0:
                if self.meten:
                    self.samenvatting = self._samenvatten(self.sessie)
                self.sessie = None
            return None
        self.laatst_gezien = nu
        if self.sessie is None:
            self.sessie = Sessie(begin=nu)
            self.stats["sessies"] += 1
        recht = abs(g.draai) <= inst.hoek_max and abs(g.kantel) <= inst.hoek_max
        dichtbij = g.oog >= inst.oog_min
        m = Meting(t=nu, oog=g.oog, midden=g.midden, vorm=h.vorm(g), recht=recht, dichtbij=dichtbij)
        meet_ver = self.meten and recht and g.oog >= 20 and len(self.sessie.metingen) % 3 == 0
        if recht and (dichtbij or meet_ver) and personen:
            h.afdruk(rgb, g)
            m.scherpte = g.scherpte
            if g.scherpte >= inst.scherpte_min:
                m.persoon, m.cos, m.tweede = self.match(g.afdruk, personen)
                m.echt = h.echtheid(rgb, g)
        self.sessie.metingen.append(m)
        return self._besluit(nu)

    @staticmethod
    def _samenvatten(s: Sessie) -> dict | None:
        ms = list(s.metingen)
        if len(ms) < 4:
            return None
        gemeten = [x for x in ms if x.persoon]
        uit = {"duur": round(ms[-1].t - ms[0].t, 1), "beelden": len(ms), "oog_max": round(max(x.oog for x in ms), 1),
               "recht": round(sum(x.recht for x in ms) / len(ms), 2), "gemeten": len(gemeten)}
        if gemeten:
            best = max(gemeten, key=lambda x: x.cos)
            pers = [x for x in gemeten if x.persoon == best.persoon]
            uit.update({"persoon_id": best.persoon, "cos_max": round(best.cos, 3), "cos_mediaan": round(float(np.median([x.cos for x in pers])), 3),
                        "echt_mediaan": round(float(np.median([x.echt for x in pers])), 3), "oog_bij_max": round(best.oog, 1),
                        "scherpte": round(float(np.median([x.scherpte for x in gemeten])), 0)})
        return uit

    def diepte(self) -> float | None:
        """3D bij naderen: vorm van ver (oog <= 70 % van nu) tegenover dichtbij, gedeeld door de ruis tussen opeenvolgende beelden."""
        ms = list(self.sessie.metingen)
        if len(ms) < 6:
            return None
        nu = ms[-3:]
        ver = [x for x in ms if x.oog <= 0.7 * np.mean([y.oog for y in nu])]
        if not ver:
            return None
        ver = ver[-3:]
        verschil = np.median([vorm_afwijking(v.vorm, n.vorm) for v in ver for n in nu])
        # ruis van de punten, zowel ver (kleiner gezicht, relatief meer ruis) als dichtbij
        ruis_nu = np.median([vorm_afwijking(a.vorm, b.vorm) for a, b in zip(ms[-6:-1], ms[-5:])])
        i = ms.index(ver[-1])
        buren = [(ms[j], ms[j + 1]) for j in range(max(0, i - 3), min(len(ms) - 1, i + 2))]
        ruis_ver = np.median([vorm_afwijking(a.vorm, b.vorm) for a, b in buren]) if buren else ruis_nu
        ruis = max(ruis_nu, ruis_ver)
        return float(verschil / max(ruis, 1e-4))

    def _besluit(self, nu: float) -> dict | None:
        inst, s = self.inst, self.sessie
        venster = [x for x in s.metingen if x.t >= nu - inst.stil_s]
        if not venster or nu - s.metingen[0].t < inst.stil_s or venster[0].t > nu - inst.stil_s + 0.6:
            return None
        if not all(x.recht and x.dichtbij for x in venster):
            return None
        mid = np.array([x.midden for x in venster])
        if np.linalg.norm(mid.max(0) - mid.min(0)) > inst.beweging_max * np.mean([x.oog for x in venster]):
            return None
        gemeten = [x for x in venster if x.persoon]
        if len(gemeten) < max(3, 0.6 * len(venster)):
            return None
        personen = [x.persoon for x in gemeten]
        pid = max(set(personen), key=personen.count)
        eigen = [x for x in gemeten if x.persoon == pid]
        cos = float(np.median([x.cos for x in eigen]))
        tweede = float(np.median([x.tweede for x in eigen]))
        echt = float(np.median([x.echt for x in eigen]))
        diepte = self.diepte()
        meting = {"persoon_id": pid, "cos": round(cos, 3), "tweede": round(tweede, 3), "echt": round(echt, 3),
                  "diepte": None if diepte is None else round(diepte, 2), "oog": round(float(np.mean([x.oog for x in venster])), 1),
                  "beelden": len(venster), "eens": round(len(eigen) / len(gemeten), 2)}
        if s.gemeld:
            return None
        if len(eigen) < 0.8 * len(gemeten) or cos < inst.drempel or cos - tweede < inst.marge:
            if cos < inst.drempel - 0.15:
                s.gemeld = True
                self.stats["onbekend"] += 1
                return {"actie": "onbekend", **meting}
            return None
        s.gemeld = True
        if pid in self.geblokkeerd and self.geblokkeerd[pid] > nu:
            return {"actie": "geblokkeerd", **meting}
        echt_ok = echt >= inst.echt_min and ((diepte is not None and diepte >= inst.diepte_min) or echt >= inst.echt_hoog)
        if echt < inst.echt_min:
            self.stats["foto"] += 1
            self.geblokkeerd[pid] = nu + inst.foto_blok
            return {"actie": "foto", **meting}
        if not echt_ok:
            return {"actie": "onzeker", **meting}   # echt, maar zonder 3D bewijs en niet hoog genoeg
        if pid in self.geopend and self.geopend[pid] > nu - inst.wacht_na_open:
            return None
        self.geopend[pid] = nu
        return {"actie": "open", **meting}
