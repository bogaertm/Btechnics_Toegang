"""Koppeling met Btechnics Work (work.btechnics.be) via Btechnics IOT.

Work stuurt opdrachten via het bestaande IOT kanaal (HA haalt ze elke minuut op). De IOT integratie roept voor elke
opdracht van het type "toegang" de dienst btechnics_toegang.werk op met {actie, velden}. Enkel de acties hieronder
bestaan; elk veld wordt nagekeken en onbekende velden worden geweigerd.

Medewerkers van Work worden gekoppeld via werk_id (id van portaal_medewerker in Work). HA blijft de baas over codes,
foto's en gezichten: die gaan nooit naar Work. Work krijgt enkel de portaallink van een medewerker, op vraag.
"""
from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.core import HomeAssistant

from .const import MODI, OFFLINE_NA
from .register import Register, ToegangFout
from .websocket import portaal_url

SCHEMA_VERSIE = 1
WERK_ID_MAX = 40


def _tekst(v: Any, naam: str, maximum: int = 80, leeg: bool = True) -> str:
    if v is None and leeg:
        return ""
    if not isinstance(v, str):
        raise ToegangFout(f"{naam} moet tekst zijn")
    v = v.strip()
    if len(v) > maximum:
        raise ToegangFout(f"{naam} is te lang")
    if not v and not leeg:
        raise ToegangFout(f"{naam} ontbreekt")
    return v


def _werk_id(v: Any) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    w = _tekst(v, "werk_id", WERK_ID_MAX, leeg=False)
    if not all(c.isalnum() or c in "_-:." for c in w):
        raise ToegangFout("werk_id bevat ongeldige tekens")
    return w


def _lijst(v: Any, naam: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ToegangFout(f"{naam} moet een lijst van tekst zijn")
    return v


def _portaal_v(p: dict) -> str | None:
    h = p.get("portaal_hash")
    return hashlib.sha256(h.encode()).hexdigest()[:12] if h else None


class Werk:
    """Uitvoerder van de acties van Work. Elke actie: (toegelaten velden, verplichte velden, functie)."""

    def __init__(self, hass: HomeAssistant, reg: Register) -> None:
        self.hass, self.reg = hass, reg
        self.acties = {
            "overzicht": ((), (), self.overzicht),
            "koppelen": (("werk_id", "persoon_id"), ("werk_id", "persoon_id"), self.koppelen),
            "ontkoppelen": (("werk_id",), ("werk_id",), self.ontkoppelen),
            "uitnodigen": (("werk_id", "voornaam", "achternaam", "telefoon", "email", "deuren", "schema", "geldig_van",
                            "geldig_tot", "dagen"), ("werk_id", "voornaam", "achternaam", "deuren"), self.uitnodigen),
            "uitnodiging_intrekken": (("uitnodiging_id",), ("uitnodiging_id",), self.uitnodiging_intrekken),
            "wijzigen": (("werk_id", "voornaam", "achternaam", "telefoon", "email", "deuren", "schema"), ("werk_id",), self.wijzigen),
            "geldigheid": (("werk_id", "geldig_van", "geldig_tot"), ("werk_id",), self.geldigheid),
            "blokkeren": (("werk_id", "tot"), ("werk_id",), self.blokkeren),
            "deblokkeren": (("werk_id",), ("werk_id",), self.deblokkeren),
            "uit_dienst": (("werk_id",), ("werk_id",), self.uit_dienst),
            "herstellen": (("werk_id",), ("werk_id",), self.herstellen),
            "portaal_link": (("werk_id", "nieuw"), ("werk_id",), self.portaal_link),
            "gezicht_wissen": (("werk_id",), ("werk_id",), self.gezicht_wissen),
            "tijdelijke_code": (("naam", "deuren", "geldig_van", "geldig_tot"), ("naam", "deuren", "geldig_tot"), self.tijdelijke_code),
            "modus": (("deur", "modus"), ("deur", "modus"), self.modus),
            "deur_toevoegen": (("naam",), ("naam",), self.deur_toevoegen),
            "deur_hernoemen": (("deur", "naam"), ("deur", "naam"), self.deur_hernoemen),
            "tablet_koppelcode": (("deur", "naam"), ("deur",), self.tablet_koppelcode),
        }

    def uitvoeren(self, actie: str, velden: dict, door: str = "") -> dict:
        if actie not in self.acties:
            raise ToegangFout(f"onbekende actie: {actie}")
        toegelaten, verplicht, fn = self.acties[actie]
        if not isinstance(velden, dict):
            raise ToegangFout("velden moet een object zijn")
        vreemd = sorted(set(velden) - set(toegelaten))
        if vreemd:
            raise ToegangFout(f"onbekende velden voor {actie}: {', '.join(vreemd)}")
        ontbreekt = [k for k in verplicht if velden.get(k) in (None, "", [])]
        if ontbreekt:
            raise ToegangFout(f"verplicht voor {actie}: {', '.join(ontbreekt)}")
        wie = f"{_tekst(door, 'door', 40) or 'Work'} via Work"
        return {"actie": actie, **(fn(wie, velden) or {})}

    # ------------------------------------------------------------------ hulp

    def _persoon(self, werk_id: Any) -> dict:
        w = _werk_id(werk_id)
        for p in self.reg.data["personen"].values():
            if p.get("werk_id") == w:
                return p
        raise ToegangFout(f"geen toegang gekend voor medewerker {w}")

    def _medewerker(self, p: dict) -> dict:
        g = p.get("gezicht")
        return {"werk_id": p.get("werk_id"), "persoon_id": p["id"], "naam": p["naam"], "voornaam": p.get("voornaam", ""),
                "achternaam": p.get("achternaam", ""), "status": p["status"], "until": p.get("until"),
                "geldig_van": p.get("valid_from"), "geldig_tot": p.get("valid_until"), "deuren": p["deuren"],
                "schema": p.get("schema") or [], "pincode": bool(p.get("pin")), "foto": bool(p.get("foto")),
                "portaal": bool(p.get("portaal_hash")), "portaal_v": _portaal_v(p), "gezicht": bool(g),
                "gezicht_sinds": g["sinds"] if g else None, "telefoon": p.get("telefoon", ""), "email": p.get("email", ""),
                "bron": p.get("bron")}

    # ------------------------------------------------------------------ acties

    def overzicht(self, wie: str, v: dict) -> dict:
        d = self.reg.data
        deuren = []
        for deur in sorted(d["deuren"].values(), key=lambda x: x["naam"].lower()):
            tabs = [t for t in d["tablets"].values() if t["deur_id"] == deur["id"]]
            deuren.append({"id": deur["id"], "naam": deur["naam"], "modus": deur["modus"], "modus_sinds": deur.get("modus_sinds"),
                           "kan_openen": bool(deur.get("open_entiteit")),
                           "tablets": [{"id": t["id"], "naam": t["naam"], "online": self.reg.tablet_online(t, OFFLINE_NA),
                                        "app_versie": t.get("app_versie"), "laatst": t.get("laatst"),
                                        "gezicht_sleutel": bool(t.get("pubkey"))} for t in tabs]})
        gekoppeld = [self._medewerker(p) for p in d["personen"].values() if p.get("werk_id")]
        andere = [{"persoon_id": p["id"], "naam": p["naam"], "soort": p.get("soort"), "status": p["status"]}
                  for p in d["personen"].values() if not p.get("werk_id")]
        uitn = [{"id": u["id"], "werk_id": u.get("werk_id"), "naam": u.get("naam", ""), "deuren": u["deuren"],
                 "verloopt": u.get("verloopt"), "gebruikt": bool(u.get("gebruikt")),
                 "bruikbaar": self.reg.uitnodiging_bruikbaar(u) is None} for u in d["uitnodigingen"].values()]
        return {"schema": SCHEMA_VERSIE, "deuren": deuren, "medewerkers": sorted(gekoppeld, key=lambda x: x["naam"].lower()),
                "andere": sorted(andere, key=lambda x: x["naam"].lower()), "uitnodigingen": uitn, "modi": list(MODI)}

    def koppelen(self, wie: str, v: dict) -> dict:
        w = _werk_id(v["werk_id"])
        p = self.reg.persoon(_tekst(v["persoon_id"], "persoon_id", 40, leeg=False))
        for q in self.reg.data["personen"].values():
            if q.get("werk_id") == w and q["id"] != p["id"]:
                raise ToegangFout(f"medewerker {w} is al gekoppeld aan {q['naam']}")
        if p.get("werk_id") and p["werk_id"] != w:
            raise ToegangFout(f"{p['naam']} is al gekoppeld aan een andere medewerker")
        p["werk_id"] = w
        self.reg.audit(wie, "aangepast", p["naam"], p["deuren"], "gekoppeld aan Work")
        self.reg.gewijzigd(lijst_veranderd=False)
        return {"medewerker": self._medewerker(p)}

    def ontkoppelen(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        p["werk_id"] = None
        self.reg.audit(wie, "aangepast", p["naam"], p["deuren"], "losgekoppeld van Work")
        self.reg.gewijzigd(lijst_veranderd=False)
        return {"persoon_id": p["id"]}

    def uitnodigen(self, wie: str, v: dict) -> dict:
        w = _werk_id(v["werk_id"])
        if any(p.get("werk_id") == w for p in self.reg.data["personen"].values()):
            raise ToegangFout("deze medewerker heeft al toegang; gebruik portaal_link of wijzigen")
        # een vorige, nog open uitnodiging voor dezelfde medewerker vervalt
        for u in list(self.reg.data["uitnodigingen"].values()):
            if u.get("werk_id") == w and not u.get("gebruikt"):
                self.reg.uitnodiging_intrekken(wie, u["id"])
        vn, an = _tekst(v.get("voornaam"), "voornaam", leeg=False), _tekst(v.get("achternaam"), "achternaam", leeg=False)
        dagen = v.get("dagen", 7)
        if not isinstance(dagen, int) or isinstance(dagen, bool) or not 1 <= dagen <= 30:
            raise ToegangFout("dagen moet 1 tot 30 zijn")
        u, token = self.reg.uitnodiging_maken(wie, deuren=_lijst(v["deuren"], "deuren"), valid_from=v.get("geldig_van") or None,
                                              valid_until=v.get("geldig_tot") or None, schema=v.get("schema"),
                                              naam=f"{vn} {an}", dagen=dagen)
        u.update(werk_id=w, voornaam=vn, achternaam=an, telefoon=_tekst(v.get("telefoon"), "telefoon"),
                 email=_tekst(v.get("email"), "email"))
        return {"uitnodiging_id": u["id"], "url": portaal_url(self.hass, token), "verloopt": u["verloopt"]}

    def uitnodiging_intrekken(self, wie: str, v: dict) -> dict:
        self.reg.uitnodiging_intrekken(wie, _tekst(v["uitnodiging_id"], "uitnodiging_id", 40, leeg=False))
        return {}

    def wijzigen(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        kw = {k: _tekst(v[k], k) for k in ("voornaam", "achternaam", "telefoon", "email") if k in v}
        if "deuren" in v:
            kw["deuren"] = _lijst(v["deuren"], "deuren")
        if "schema" in v:
            kw["schema"] = v["schema"]
        self.reg.bijwerken(wie, p["id"], **kw)
        return {"medewerker": self._medewerker(p)}

    def geldigheid(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.geldigheid(wie, p["id"], v.get("geldig_van") or None, v.get("geldig_tot") or None)
        return {"medewerker": self._medewerker(p)}

    def blokkeren(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.blokkeren(wie, p["id"], v.get("tot") or None)
        return {"medewerker": self._medewerker(p)}

    def deblokkeren(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.deblokkeren(wie, p["id"])
        return {"medewerker": self._medewerker(p)}

    def uit_dienst(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.uit_dienst(wie, p["id"], "via Work")
        return {"medewerker": self._medewerker(p)}

    def herstellen(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.herstellen(wie, p["id"])
        return {"medewerker": self._medewerker(p)}

    def portaal_link(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        nieuw = v.get("nieuw", False)
        if not isinstance(nieuw, bool):
            raise ToegangFout("nieuw moet waar of onwaar zijn")
        if p["status"] == "retired":
            raise ToegangFout("deze medewerker is uit dienst")
        if nieuw or not p.get("portaal_token"):
            self.reg.portaal_token(wie, p["id"])
        return {"url": portaal_url(self.hass, p["portaal_token"]), "portaal_v": _portaal_v(p)}

    def gezicht_wissen(self, wie: str, v: dict) -> dict:
        p = self._persoon(v["werk_id"])
        self.reg.gezicht_wissen(wie, p["id"])
        return {"medewerker": self._medewerker(p)}

    def tijdelijke_code(self, wie: str, v: dict) -> dict:
        p = self.reg.toevoegen(wie, naam=_tekst(v["naam"], "naam", 40, leeg=False), deuren=_lijst(v["deuren"], "deuren"),
                               valid_from=v.get("geldig_van") or None, valid_until=v["geldig_tot"], soort="tijdelijk")
        return {"persoon_id": p["id"], "naam": p["naam"], "code": p["pin"], "geldig_tot": p["valid_until"]}

    def modus(self, wie: str, v: dict) -> dict:
        if v["modus"] not in MODI:
            raise ToegangFout(f"modus moet een van {', '.join(MODI)} zijn")
        d = self.reg.deur_op_naam(_tekst(v["deur"], "deur", leeg=False))
        self.reg.modus_zetten(d["id"], v["modus"], door=wie)
        return {"deur": d["id"], "modus": d["modus"]}

    def deur_toevoegen(self, wie: str, v: dict) -> dict:
        d = self.reg.deur_toevoegen(_tekst(v["naam"], "naam", 40, leeg=False), wie=wie)
        return {"deur": d["id"], "naam": d["naam"]}

    def deur_hernoemen(self, wie: str, v: dict) -> dict:
        d = self.reg.deur_op_naam(_tekst(v["deur"], "deur", leeg=False))
        self.reg.deur_wijzigen(d["id"], naam=_tekst(v["naam"], "naam", 40, leeg=False), wie=wie)
        return {"deur": d["id"], "naam": d["naam"]}

    def tablet_koppelcode(self, wie: str, v: dict) -> dict:
        d = self.reg.deur_op_naam(_tekst(v["deur"], "deur", leeg=False))
        code = self.reg.koppelcode_maken(d["id"], _tekst(v.get("naam"), "naam", 40) or None)
        return {"code": code, "deur": d["id"], "geldig_s": 600}
