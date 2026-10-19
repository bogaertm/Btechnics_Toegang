"""Beslissen of de deur open mag: groot recht gezicht, 2 s stil, dezelfde persoon, echt (MiniFASNet) EN 3D bij naderen.

Een sessie is een ononderbroken spoor van een gezicht. Een sprong in plaats, grootte of tijd start een nieuwe sessie,
zodat diepte nooit over twee verschillende gezichten (of een foto die wordt omgewisseld) gemeten wordt.
Per sessie valt hoogstens een besluit; wie na 6 s dichtbij en recht nog geen besluit kreeg, wordt "onbekend".
Alles gebeurt zonder actie van de persoon (geen knipperen). In proefmodus wordt enkel gemeten en gemeld.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .herkenning import Gezicht, Herkenner, vorm_afwijking


@dataclass
class Instellingen:
    drempel: float = 0.70        # cosinus met de beste afdruk van de persoon (mediaan over het venster); live nooit lager
    marge: float = 0.10          # verschil met de tweede beste persoon (of met de cohorte)
    cohorte: float = 0.30        # ondergrens voor de tweede: typische cosinus van een vreemde, ook bij een persoon in de lijst
    echt_min: float = 0.85       # MiniFASNet, mediaan over het venster; lager = vermoedelijke foto
    echt_hoog: float = 0.97      # niet meer gebruikt voor beslissen (open vraagt altijd 3D bewijs)
    diepte_min: float = 2.5      # vervorming bij naderen gedeeld door de ruis (verplicht om te openen)
    oog_min: float = 70.0        # pixels tussen de irissen in het volle beeld (dichtbij)
    hoek_max: float = 15.0       # graden draai en kanteling
    stil_s: float = 2.0          # zoveel seconden stil en herkend
    beweging_max: float = 0.15   # verplaatsing van het midden in het venster, als deel van de oogafstand
    scherpte_min: float = 40.0
    wacht_na_open: float = 20.0  # zelfde persoon niet opnieuw openen binnen zoveel seconden
    max_beslis_s: float = 6.0    # zo lang dichtbij en recht zonder besluit: "onbekend"
    sprong_midden: float = 1.0   # nieuwe sessie bij een sprong van het midden groter dan zoveel keer de oogafstand
    sprong_schaal: float = 0.30  # nieuwe sessie bij een schaalsprong groter dan 30 %
    sprong_tijd: float = 1.0     # nieuwe sessie bij meer dan zoveel seconden tussen twee beelden met een gezicht
    meerdere: float = 0.6        # tweede gezicht groter dan zoveel keer het grootste: geen besluit


@dataclass(eq=False)
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
    meerdere: bool = False


@dataclass
class Sessie:
    begin: float
    maxlen: int = 200
    metingen: deque = None
    gemeld: bool = False
    teller: int = 0                 # beelden in deze sessie (eigen teller, de deque is begrensd)
    meerdere: bool = False          # ooit een tweede groot gezicht gezien
    dicht_sinds: float | None = None  # eerste beeld dichtbij en recht

    def __post_init__(self):
        if self.metingen is None:
            self.metingen = deque(maxlen=self.maxlen)


class Beslisser:
    def __init__(self, inst: Instellingen, meten: bool = False, fps: float = 8.0):
        self.inst = inst
        self.meten = meten          # proefmodus: ook verder dan oog_min meten en per sessie een samenvatting geven
        self.maxlen = max(200, int(inst.stil_s * fps * 3))
        self.samenvatting: dict | None = None
        self.sessie: Sessie | None = None
        self.laatst_gezien = 0.0
        self.geopend: dict[str, float] = {}
        self.stats = {"sessies": 0, "geopend": 0, "proef_open": 0, "foto": 0, "onzeker": 0, "onbekend": 0,
                      "meerdere": 0, "fouten": 0}

    def match(self, afdruk: np.ndarray, personen: dict) -> tuple[str | None, float, float]:
        """Beste persoon, zijn cosinus en de tweede. De tweede is minstens de cohorte, ook met een persoon in de lijst,
        zodat cos - tweede >= marge iets betekent."""
        beste, b1, b2 = None, -1.0, -1.0
        for pid, p in personen.items():
            c = float((p["afdrukken"] @ afdruk).max())
            if c > b1:
                beste, b2, b1 = pid, b1, c
            elif c > b2:
                b2 = c
        return beste, b1, max(b2, self.inst.cohorte)

    # ------------------------------------------------------------------ spoor

    def _sprong(self, vorig: Meting, g: Gezicht, nu: float) -> bool:
        inst = self.inst
        if nu - vorig.t > inst.sprong_tijd:
            return True
        oog = max((vorig.oog + g.oog) / 2, 1e-6)
        if np.hypot(g.midden[0] - vorig.midden[0], g.midden[1] - vorig.midden[1]) > inst.sprong_midden * oog:
            return True
        a, b = max(vorig.oog, 1e-6), max(g.oog, 1e-6)
        return max(a, b) / min(a, b) - 1 > inst.sprong_schaal

    def _sluit(self) -> None:
        if self.sessie is not None and self.meten:
            self.samenvatting = self._samenvatten(self.sessie)
        self.sessie = None

    def stap(self, h: Herkenner, rgb, g: Gezicht | None, personen: dict, nu: float | None = None,
             anderen: list[Gezicht] | None = None) -> dict | None:
        """Een beeld verwerken. g is het grootste gezicht, anderen de overige.
        Geeft een besluit terug: {"actie": "open"|"foto"|"onzeker"|"onbekend", ...} of None."""
        nu = time.monotonic() if nu is None else nu
        inst = self.inst
        if g is None:
            if self.sessie and nu - self.laatst_gezien > inst.sprong_tijd:
                self._sluit()
            return None
        if self.sessie and self.sessie.metingen and self._sprong(self.sessie.metingen[-1], g, nu):
            self._sluit()
        self.laatst_gezien = nu
        if self.sessie is None:
            self.sessie = Sessie(begin=nu, maxlen=self.maxlen)
            self.stats["sessies"] += 1
        s = self.sessie
        s.teller += 1
        recht = abs(g.draai) <= inst.hoek_max and abs(g.kantel) <= inst.hoek_max
        dichtbij = g.oog >= inst.oog_min
        meerdere = any(x is not None and x.oog > inst.meerdere * g.oog for x in (anderen or []))
        if meerdere and not s.meerdere:
            s.meerdere = True
            self.stats["meerdere"] += 1
        if recht and dichtbij and s.dicht_sinds is None:
            s.dicht_sinds = nu
        m = Meting(t=nu, oog=g.oog, midden=g.midden, vorm=h.vorm(g), recht=recht, dichtbij=dichtbij, meerdere=meerdere)
        # verre beelden: elk derde een afdruk, zodat de beelden voor de diepte bij dezelfde persoon moeten passen
        meet_ver = recht and not dichtbij and g.oog >= 20 and s.teller % 3 == 0
        if recht and (dichtbij or meet_ver) and personen and not meerdere:
            h.afdruk(rgb, g)
            m.scherpte = g.scherpte
            if g.scherpte >= inst.scherpte_min:
                m.persoon, m.cos, m.tweede = self.match(g.afdruk, personen)
                if dichtbij or self.meten:
                    m.echt = h.echtheid(rgb, g)
        s.metingen.append(m)
        return self._besluit(nu)

    # ------------------------------------------------------------------ samenvatting (proef)

    def _samenvatten(self, s: Sessie) -> dict | None:
        ms = list(s.metingen)
        if len(ms) < 4:
            return None
        gemeten = [x for x in ms if x.persoon]
        uit = {"duur": round(ms[-1].t - ms[0].t, 1), "beelden": s.teller, "oog_max": round(max(x.oog for x in ms), 1),
               "recht": round(sum(x.recht for x in ms) / len(ms), 2), "gemeten": len(gemeten), "meerdere": s.meerdere}
        if gemeten:
            best = max(gemeten, key=lambda x: x.cos)
            pers = [x for x in gemeten if x.persoon == best.persoon]
            dicht = [x.cos for x in pers if x.oog >= self.inst.oog_min]
            ver = [x.cos for x in pers if x.oog < self.inst.oog_min]
            echt = [x.echt for x in pers if x.echt is not None]
            uit.update({"persoon_id": best.persoon, "cos_max": round(best.cos, 3),
                        "cos_mediaan_dicht": round(float(np.median(dicht)), 3) if dicht else None,
                        "cos_mediaan_ver": round(float(np.median(ver)), 3) if ver else None,
                        "echt_mediaan": round(float(np.median(echt)), 3) if echt else None, "oog_bij_max": round(best.oog, 1),
                        "scherpte": round(float(np.median([x.scherpte for x in gemeten])), 0)})
        return uit

    # ------------------------------------------------------------------ diepte

    def diepte(self, pid: str | None = None) -> float | None:
        """3D bij naderen: vorm van ver (oog <= 70 % van nu) tegenover dichtbij, gedeeld door de ruis tussen opeenvolgende
        beelden. Enkel binnen deze sessie (een spoor). Hebben de verre beelden een afdruk, dan moet die bij pid passen."""
        ms = list(self.sessie.metingen)
        if len(ms) < 6:
            return None
        nu = ms[-3:]
        grens = 0.7 * np.mean([y.oog for y in nu])
        ver_i = [i for i, x in enumerate(ms) if x.oog <= grens][-3:]
        if not ver_i:
            return None
        ver = [ms[i] for i in ver_i]
        if pid is not None and any(v.persoon is not None and v.persoon != pid for v in ver):
            return None
        verschil = np.median([vorm_afwijking(v.vorm, n.vorm) for v in ver for n in nu])
        # ruis van de punten, zowel ver (kleiner gezicht, relatief meer ruis) als dichtbij
        ruis_nu = np.median([vorm_afwijking(a.vorm, b.vorm) for a, b in zip(ms[-6:-1], ms[-5:])])
        i = ver_i[-1]
        buren = [(ms[j], ms[j + 1]) for j in range(max(0, i - 3), min(len(ms) - 1, i + 2))]
        ruis_ver = np.median([vorm_afwijking(a.vorm, b.vorm) for a, b in buren]) if buren else ruis_nu
        if not all(np.isfinite([verschil, ruis_nu, ruis_ver])):
            return None
        ruis = max(ruis_nu, ruis_ver)
        return float(verschil / max(ruis, 1e-4))

    # ------------------------------------------------------------------ besluit

    def _besluit(self, nu: float) -> dict | None:
        s = self.sessie
        if s.gemeld:
            return None
        uit = self._probeer(nu)
        if uit is None and s.dicht_sinds is not None and nu - s.dicht_sinds >= self.inst.max_beslis_s:
            s.gemeld = True
            self.stats["onbekend"] += 1
            return {"actie": "onbekend", "persoon_id": None, "cos": None, "tweede": None, "echt": None, "diepte": None,
                    "oog": round(float(s.metingen[-1].oog), 1), "beelden": s.teller, "eens": None, "reden": "te_lang",
                    "meerdere": s.meerdere}
        return uit

    def _probeer(self, nu: float) -> dict | None:
        inst, s = self.inst, self.sessie
        venster = [x for x in s.metingen if x.t >= nu - inst.stil_s]
        if not venster or nu - s.metingen[0].t < inst.stil_s or venster[0].t > nu - inst.stil_s + 0.6:
            return None
        if any(x.meerdere for x in venster):
            return None   # twee gezichten in beeld: geen besluit
        if not all(x.recht and x.dichtbij for x in venster):
            return None
        mid = np.array([x.midden for x in venster])
        if np.linalg.norm(mid.max(0) - mid.min(0)) > inst.beweging_max * np.mean([x.oog for x in venster]):
            return None
        gemeten = [x for x in venster if x.persoon and x.echt is not None]
        if len(gemeten) < max(3, 0.6 * len(venster)):
            return None
        personen = [x.persoon for x in gemeten]
        pid = max(set(personen), key=personen.count)
        eigen = [x for x in gemeten if x.persoon == pid]
        cos = float(np.median([x.cos for x in eigen]))
        tweede = float(np.median([x.tweede for x in eigen]))
        echt = float(np.median([x.echt for x in eigen]))
        diepte = self.diepte(pid)
        meting = {"persoon_id": pid, "cos": round(cos, 3), "tweede": round(tweede, 3), "echt": round(echt, 3),
                  "diepte": None if diepte is None else round(diepte, 2), "oog": round(float(np.mean([x.oog for x in venster])), 1),
                  "beelden": len(venster), "eens": round(len(eigen) / len(gemeten), 2), "meerdere": s.meerdere}
        if len(eigen) < 0.8 * len(gemeten) or cos < inst.drempel or cos - tweede < inst.marge:
            if cos < inst.drempel - 0.15:
                s.gemeld = True
                self.stats["onbekend"] += 1
                return {"actie": "onbekend", **meting}
            return None
        s.gemeld = True
        if echt < inst.echt_min:
            self.stats["foto"] += 1
            return {"actie": "foto", **meting}
        if diepte is None or diepte < inst.diepte_min:
            # geen 3D bewijs: nooit open, maar ook geen beschuldiging van een foto (enkel lage echtheid is dat)
            self.stats["onzeker"] += 1
            return {"actie": "onzeker", **meting}
        if pid in self.geopend and self.geopend[pid] > nu - inst.wacht_na_open:
            return None
        self.geopend[pid] = nu
        return {"actie": "open", **meting}
