"""Register: personen (codes), deuren, tablets, uitnodigingen, wijzigingen en tabletmeldingen.

Statussen en geldigheid werken zoals in Btechnics VTO (Trefpunt):
  active   werkt op de tablets
  blocked  tijdelijk niet, tot `until` of tot deblokkeren; "wacht op begin" als until == valid_from
  retired  uit dienst, bewaard en herstelbaar
Een planner kijkt elke minuut: einde geldigheid = uit dienst, einde blokkering of begin geldigheid = actief.
Enkel personen met status active staan in de lijst voor de tablets.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    AUDIT_MAX,
    GEBEURTENISSEN_MAX,
    GEZICHT_AAD,
    KOPPELCODE_GELDIG,
    KOPPELCODE_POGINGEN,
    MODI,
    MODUS_STANDAARD,
    PULS_STANDAARD,
    PIN_MAX,
    PIN_MIN,
    PORTAAL_FOUTEN_MAX,
    SIGNAL_UPDATE,
    STORAGE_KEY,
    STORAGE_VERSION,
    UITNODIGING_DAGEN,
)
from .geldigheid import controleer_blok
from .gezicht import GezichtFout, b64, controleer_pakket, publieke_sleutel
from .hashing import hash_waarde, kaart_norm, pin_norm, sleutel_hash, zout


class ToegangFout(Exception):
    """Fout die als leesbare melding naar de gebruiker gaat."""


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(3)}"


def _nu() -> datetime:
    return dt_util.now().replace(microsecond=0)


def _iso(t: datetime | None) -> str | None:
    return dt_util.as_local(t).replace(microsecond=0).isoformat() if t else None


def tijdstip(v: Any) -> datetime | None:
    """Tijdstip uit tekst of datetime; zonder tijdzone = tijdzone van Home Assistant."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        t = v
    else:
        t = dt_util.parse_datetime(str(v))
        if t is None:
            try:
                d = date.fromisoformat(str(v))
            except ValueError as e:
                raise ToegangFout(f"ongeldig tijdstip: {v}") from e
            t = datetime(d.year, d.month, d.day)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(t)


def _kort(t: datetime | str | None) -> str:
    t = tijdstip(t) if not isinstance(t, datetime) else t
    return t.strftime("%d/%m/%Y %H:%M") if t else ""


def botst(a: str, b: str) -> bool:
    """Twee verschillende codes waarvan de ene het begin is van de andere: zonder bevestigingstoets
    zou de kortere al openen tijdens het typen van de langere."""
    return a != b and (a.startswith(b) or b.startswith(a))


def zwak(code: str) -> bool:
    """Makkelijk te raden (zelfde regels als Btechnics VTO)."""
    d = [int(c) for c in code]
    stappen = {d[i + 1] - d[i] for i in range(len(d) - 1)}
    return (len(set(d)) <= 2 or stappen in ({1}, {-1}, {0})
            or code[:2] * (len(code) // 2) == code or code[:3] * 2 == code or code[:3] == code[3:][::-1])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Register:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {}
        self._koppelcodes: dict[str, dict] = {}
        self._foute_koppelpogingen = 0
        self._wijziging = asyncio.Condition()
        self.bij_nieuwe_tablet = None
        self.bij_gewijzigd = None   # bv. Akuvox sync (met vertraging)
        self.bij_toegang = None          # callback(regel) naar het archief

    # ------------------------------------------------------------------ opslag

    async def async_laden(self) -> None:
        data = await self._store.async_load() or {}
        for k, v in (("versie", 1), ("personen", {}), ("deuren", {}), ("tablets", {}), ("audit", []),
                     ("gebeurtenissen", []), ("uitnodigingen", {})):
            data.setdefault(k, v)
        oud_log = data.pop("log", None)
        for t in data["tablets"].values():
            t.pop("laatst_mono", None)
        for d in data["deuren"].values():
            d.setdefault("puls_s", PULS_STANDAARD)
            d.setdefault("camera", None)
        for p in data["personen"].values():
            self._migreer(p)
        self.data = data
        self._oud_log = oud_log or []

    @staticmethod
    def _migreer(p: dict) -> None:
        """Personen van v0.2 en v0.3 (actief, van/tot als datum) naar het model van v0.4."""
        if "status" not in p:
            p["status"] = "active" if p.pop("actief", True) else "blocked"
            p.setdefault("until", None)
        for oud, nieuw, uur in (("van", "valid_from", (0, 0)), ("tot", "valid_until", (23, 59))):
            if oud in p:
                v = p.pop(oud)
                if v and not p.get(nieuw):
                    d = date.fromisoformat(v)
                    p[nieuw] = _iso(datetime(d.year, d.month, d.day, *uur, tzinfo=dt_util.get_default_time_zone()))
        for k, v in (("valid_from", None), ("valid_until", None), ("until", None), ("soort", "vast"),
                     ("voornaam", ""), ("achternaam", ""), ("telefoon", ""), ("email", ""), ("foto", False),
                     ("owner", None), ("bron", "ha"), ("schema", []), ("kaarten", []), ("portaal_hash", None),
                     ("gezicht", None), ("werk_id", None)):
            p.setdefault(k, v)
        p.setdefault("gewijzigd", p.get("gemaakt"))

    @callback
    def _bewaar(self) -> None:
        self._store.async_delay_save(lambda: self.data, 1)

    @callback
    def gewijzigd(self, lijst_veranderd: bool = True) -> None:
        if lijst_veranderd:
            self.data["versie"] += 1
        self._bewaar()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        # Btechnics IOT stuurt dan (na 10 s rust) een nieuwe status naar Work
        async_dispatcher_send(self.hass, "btechnics_branding_status_changed")
        if self.bij_gewijzigd:
            self.bij_gewijzigd()
        self.hass.async_create_task(self._wek())

    async def _wek(self) -> None:
        async with self._wijziging:
            self._wijziging.notify_all()

    async def wacht_op_versie(self, versie: int, max_s: float, tablet_id: str | None = None) -> bool:
        def klaar() -> bool:
            return self.data["versie"] != versie or bool(tablet_id and self._open_fotovragen(tablet_id))
        if klaar():
            return True
        try:
            async with self._wijziging:
                await asyncio.wait_for(self._wijziging.wait_for(klaar), max_s)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------ foto vragen aan de tablet

    def foto_vragen(self, deur_id: str, uid: str) -> int:
        """HA opende de deur zelf (op afstand): vraag de tablets van die deur om een foto met deze uid (30 s geldig)."""
        if not hasattr(self, "_fotovragen"):
            self._fotovragen = {}
        tot = time.time() + 30
        n = 0
        for t in self.data["tablets"].values():
            if t["deur_id"] == deur_id:
                self._fotovragen.setdefault(t["id"], []).append({"uid": uid, "tot": tot})
                self._fotovragen[t["id"]] = self._fotovragen[t["id"]][-5:]
                n += 1
        if n:
            self.hass.async_create_task(self._wek())
        return n

    def _open_fotovragen(self, tablet_id: str) -> list:
        lijst = [v for v in getattr(self, "_fotovragen", {}).get(tablet_id, []) if v["tot"] > time.time()]
        if hasattr(self, "_fotovragen"):
            self._fotovragen[tablet_id] = lijst
        return lijst

    def fotovragen_ophalen(self, tablet_id: str) -> list[str]:
        lijst = self._open_fotovragen(tablet_id)
        if hasattr(self, "_fotovragen"):
            self._fotovragen[tablet_id] = []
        return [v["uid"] for v in lijst]

    @property
    def versie(self) -> int:
        return self.data["versie"]

    # ------------------------------------------------------------------ wijzigingen

    def audit(self, wie: str, actie: str, naam: str, deuren: list[str] | None = None, detail: str = "",
              soort: str = "code") -> None:
        namen = [self.data["deuren"].get(d, {}).get("naam", d) for d in (deuren or [])]
        self.data["audit"].append({"ts": _iso(_nu()), "user": wie or "automatisch", "action": actie, "kind": soort,
                                   "name": naam, "doors": namen, "detail": detail})
        del self.data["audit"][:-AUDIT_MAX]

    # ------------------------------------------------------------------ deuren

    def deur(self, deur_id: str) -> dict:
        d = self.data["deuren"].get(deur_id)
        if not d:
            raise ToegangFout(f"Deur {deur_id} bestaat niet")
        return d

    def deur_op_naam(self, naam_of_id: str) -> dict:
        if naam_of_id in self.data["deuren"]:
            return self.data["deuren"][naam_of_id]
        for d in self.data["deuren"].values():
            if d["naam"].lower() == str(naam_of_id).strip().lower():
                return d
        raise ToegangFout(f"onbekende deur: {naam_of_id}")

    def deur_toevoegen(self, naam: str, open_entiteit: str | None = None, wie: str = "") -> dict:
        naam = naam.strip()
        if not naam:
            raise ToegangFout("Geef de deur een naam")
        if any(d["naam"].lower() == naam.lower() for d in self.data["deuren"].values()):
            raise ToegangFout(f"Er bestaat al een deur {naam}")
        d = {"id": _id("d"), "naam": naam, "open_entiteit": open_entiteit or None, "puls_s": PULS_STANDAARD,
             "modus": MODUS_STANDAARD, "modus_sinds": _iso(_nu()), "modus_door": "ha"}
        self.data["deuren"][d["id"]] = d
        self.audit(wie, "toegevoegd", naam, soort="deur")
        self.gewijzigd()
        return d

    def deur_wijzigen(self, deur_id: str, naam: str | None = None, open_entiteit: str | None = None, wie: str = "",
                      puls_s: int | None = None, camera: str | None = None) -> dict:
        d = self.deur(deur_id)
        detail = []
        if camera is not None and (camera or None) != d.get("camera"):
            if camera and not camera.startswith("camera."):
                raise ToegangFout("kies een camera entiteit")
            d["camera"] = camera or None
            detail.append(f"foto met {camera or 'niets'}")
        if puls_s is not None:
            if not isinstance(puls_s, int) or not 0 <= puls_s <= 60:
                raise ToegangFout("pulsduur is 0 tot 60 seconden")
            if puls_s != d.get("puls_s", PULS_STANDAARD):
                detail.append(f"pulsduur {puls_s} s")
                d["puls_s"] = puls_s
        if naam is not None and naam.strip() and naam.strip() != d["naam"]:
            detail.append(f"naam {d['naam']} naar {naam.strip()}")
            d["naam"] = naam.strip()
        if open_entiteit is not None and (open_entiteit or None) != d.get("open_entiteit"):
            d["open_entiteit"] = open_entiteit or None
            detail.append(f"openen met {open_entiteit or 'niets'}")
        if detail:
            self.audit(wie, "aangepast", d["naam"], soort="deur", detail=", ".join(detail))
        self.gewijzigd()
        return d

    def deur_verwijderen(self, deur_id: str, wie: str = "") -> None:
        d = self.deur(deur_id)
        if any(t["deur_id"] == deur_id for t in self.data["tablets"].values()):
            raise ToegangFout("Er is nog een tablet aan deze deur gekoppeld. Ontkoppel die eerst.")
        del self.data["deuren"][deur_id]
        for p in self.data["personen"].values():
            if deur_id in p["deuren"]:
                p["deuren"].remove(deur_id)
        self.audit(wie, "verwijderd", d["naam"], soort="deur")
        self.gewijzigd()

    def klavier_blokkeren(self, deur_id: str, seconden: int) -> str:
        """Klavier van de tablets van deze deur tijdelijk dicht (na te veel foute codes). Code, badge en gezicht wachten tot dan."""
        from datetime import timedelta
        d = self.deur(deur_id)
        d["klavier_tot"] = _iso(_nu() + timedelta(seconds=seconden))
        self.audit("toegang", "klavier geblokkeerd", d["naam"], soort="deur", detail=f"te veel foute pogingen, tot {d['klavier_tot'][11:16]}")
        self.gewijzigd()
        return d["klavier_tot"]

    def modus_zetten(self, deur_id: str, modus: str, door: str) -> dict:
        if modus not in MODI:
            raise ToegangFout(f"Onbekende modus {modus}, kies uit {', '.join(MODI)}")
        d = self.deur(deur_id)
        d["modus"], d["modus_sinds"], d["modus_door"] = modus, _iso(_nu()), door
        self.gewijzigd()
        return d

    # ------------------------------------------------------------------ personen: hulp

    def persoon(self, persoon_id: str) -> dict:
        p = self.data["personen"].get(persoon_id)
        if not p:
            raise ToegangFout("onbekende code")
        return p

    def persoon_op_naam(self, naam_of_id: str) -> dict:
        if naam_of_id in self.data["personen"]:
            return self.data["personen"][naam_of_id]
        hits = [p for p in self.data["personen"].values() if p["naam"].lower() == str(naam_of_id).strip().lower()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ToegangFout(f"Meerdere personen heten {naam_of_id}, gebruik het id")
        raise ToegangFout(f"Persoon {naam_of_id} bestaat niet")

    def _pins(self, behalve: str | None = None) -> set[str]:
        return {p["pin"] for p in self.data["personen"].values() if p.get("pin") and p["id"] != behalve}

    def controleer_pin(self, pin: str, behalve: str | None = None, streng: bool = False) -> str:
        """streng (portaal): ook zwakke codes weigeren, en één algemene melding zodat niemand kan
        afleiden welke codes van anderen zijn."""
        pin = pin_norm(pin)
        if not (PIN_MIN <= len(pin) <= PIN_MAX):
            raise ToegangFout(f"Een code heeft {PIN_MIN} tot {PIN_MAX} cijfers.")
        if streng and (zwak(pin) or pin in self._pins(behalve)):
            raise ToegangFout("Deze code kan niet gebruikt worden. Kies een andere, minder voor de hand liggende code.")
        if streng and any(botst(pin, a) for a in self._pins(behalve)):
            raise ToegangFout("Deze code kan niet gebruikt worden. Kies een andere, minder voor de hand liggende code.")
        for p in self.data["personen"].values():
            if p["id"] == behalve or not p.get("pin"):
                continue
            st = {"blocked": " (geblokkeerd)", "retired": " (uit dienst)"}.get(p["status"], "")
            if p["pin"] == pin:
                raise ToegangFout(f"code is in gebruik bij {p['naam']}{st}")
            if botst(pin, p["pin"]):
                raise ToegangFout(f"code begint met dezelfde cijfers als de code van {p['naam']}{st}; "
                                  "het klavier heeft geen bevestigingstoets, kies een andere code")
        return pin

    def nieuwe_code(self, lengte: int = 6) -> str:
        bezet = self._pins()
        for _ in range(1000):
            c = "".join(secrets.choice("0123456789") for _ in range(lengte))
            if not zwak(c) and not any(c == b or botst(c, b) for b in bezet):
                return c
        raise ToegangFout("kon geen vrije code maken")

    def _deuren_ids(self, deuren: list[str] | None) -> list[str]:
        uit = []
        for x in deuren or []:
            i = self.deur_op_naam(x)["id"]
            if i not in uit:
                uit.append(i)
        return uit

    def _schema(self, schema: Any) -> list[dict]:
        try:
            return [controleer_blok(b) for b in (schema or [])]
        except ValueError as e:
            raise ToegangFout(f"Toegangsuren kloppen niet: {e}") from e

    @staticmethod
    def wacht(p: dict) -> bool:
        return p["status"] == "blocked" and bool(p.get("valid_from")) and p.get("until") == p.get("valid_from")

    def _uniek(self, naam: str) -> str:
        bestaand = {p["naam"].casefold() for p in self.data["personen"].values()}
        if naam.casefold() not in bestaand:
            return naam
        n = 2
        while f"{naam} ({n})".casefold() in bestaand:
            n += 1
        return f"{naam} ({n})"

    @staticmethod
    def _naam(voornaam: str, achternaam: str, naam: str | None) -> str:
        samen = f"{(voornaam or '').strip()} {(achternaam or '').strip()}".strip()
        return samen or (naam or "").strip()

    @staticmethod
    def geldig_detail(p: dict) -> str:
        vf, vu = p.get("valid_from"), p.get("valid_until")
        if vf and vu:
            return f"geldig van {_kort(vf)} tot {_kort(vu)}"
        if vf:
            return f"geldig vanaf {_kort(vf)}"
        if vu:
            return f"geldig tot {_kort(vu)}"
        return "altijd geldig"

    def _pin_zetten(self, p: dict, pin: str | None) -> None:
        p["pin"] = pin or None
        p["pin_zout"] = zout()
        p["pin_hash"] = hash_waarde(p["pin_zout"], pin) if pin else None

    # ------------------------------------------------------------------ personen: acties

    def toevoegen(self, wie: str, *, naam: str = "", voornaam: str = "", achternaam: str = "", pin: str | None = None,
                  deuren: list[str] | None = None, valid_from: Any = None, valid_until: Any = None, schema: Any = None,
                  telefoon: str = "", email: str = "", soort: str | None = None, owner: str | None = None,
                  owner_limit: int | None = None, bron: str = "ha", streng: bool = False) -> dict:
        volledig = self._naam(voornaam, achternaam, naam)
        if not volledig:
            raise ToegangFout("naam mag niet leeg zijn")
        ids = self._deuren_ids(deuren)
        if not ids:
            raise ToegangFout("kies minstens een deur")
        nu = _nu()
        vf, vu = tijdstip(valid_from), tijdstip(valid_until)
        if vu and vu <= nu:
            raise ToegangFout("het einde van de geldigheid ligt in het verleden")
        if vf and vu and vu <= vf:
            raise ToegangFout("het einde van de geldigheid moet na het begin liggen")
        if vf and vf <= nu:
            vf = None
        if owner and owner_limit is not None:
            actief = sum(1 for q in self.data["personen"].values() if q.get("owner") == owner and q["status"] != "retired")
            if actief >= owner_limit:
                raise ToegangFout(f"je hebt al {owner_limit} actieve tijdelijke codes; stop er eerst een")
        code = self.controleer_pin(pin, streng=streng) if pin else self.nieuwe_code()
        tijdelijk = soort == "tijdelijk" or (soort is None and not pin)
        if tijdelijk or owner:
            volledig = self._uniek(volledig)
        p = {"id": _id("p"), "naam": volledig, "voornaam": (voornaam or "").strip(), "achternaam": (achternaam or "").strip(),
             "soort": "tijdelijk" if tijdelijk else "vast", "status": "active", "until": None,
             "valid_from": _iso(vf), "valid_until": _iso(vu), "deuren": ids, "schema": self._schema(schema),
             "telefoon": (telefoon or "").strip(), "email": (email or "").strip(), "foto": False, "kaarten": [],
             "owner": owner, "bron": bron, "portaal_hash": None, "gemaakt": _iso(nu), "gewijzigd": _iso(nu)}
        self._pin_zetten(p, code)
        if vf:
            p["status"], p["until"] = "blocked", p["valid_from"]
        self.data["personen"][p["id"]] = p
        self.audit(wie, "toegevoegd", p["naam"], ids, self.geldig_detail(p) if (vf or vu) else "")
        self.gewijzigd()
        return p

    def bijwerken(self, wie: str, pid: str, *, naam: str | None = None, voornaam: str | None = None,
                  achternaam: str | None = None, pin: str | None = None, deuren: list[str] | None = None,
                  schema: Any = None, telefoon: str | None = None, email: str | None = None) -> dict:
        p = self.persoon(pid)
        detail = []
        if voornaam is not None or achternaam is not None:
            p["voornaam"] = (voornaam if voornaam is not None else p["voornaam"]).strip()
            p["achternaam"] = (achternaam if achternaam is not None else p["achternaam"]).strip()
            naam = self._naam(p["voornaam"], p["achternaam"], naam or p["naam"])
        if naam is not None and naam.strip() and naam.strip() != p["naam"]:
            detail.append(f"naam {p['naam']} naar {naam.strip()}")
            p["naam"] = naam.strip()
        if pin:
            nieuw = self.controleer_pin(pin, behalve=pid)
            if nieuw != p.get("pin"):
                self._pin_zetten(p, nieuw)
                detail.append("code gewijzigd")
        if deuren is not None:
            if p["status"] == "retired":
                raise ToegangFout("deuren aanpassen kan pas na herstellen")
            ids = self._deuren_ids(deuren)
            if not ids:
                raise ToegangFout("minstens één deur nodig; gebruik Uit dienst om de code overal weg te halen")
            if sorted(ids) != sorted(p["deuren"]):
                p["deuren"] = ids
                detail.append("deuren: " + ", ".join(self.data["deuren"][i]["naam"] for i in ids))
        if schema is not None:
            s = self._schema(schema)
            if s != p["schema"]:
                p["schema"] = s
                detail.append("toegangsuren gewijzigd")
        if telefoon is not None and telefoon.strip() != p.get("telefoon", ""):
            p["telefoon"] = telefoon.strip()
            detail.append("telefoon gewijzigd")
        if email is not None and email.strip() != p.get("email", ""):
            p["email"] = email.strip()
            detail.append("e-mail gewijzigd")
        if detail:
            p["gewijzigd"] = _iso(_nu())
            self.audit(wie, "aangepast", p["naam"], p["deuren"], ", ".join(detail))
            self.gewijzigd()
        return p

    def geldigheid(self, wie: str, pid: str, valid_from: Any = None, valid_until: Any = None) -> dict:
        p = self.persoon(pid)
        if p["status"] == "retired":
            raise ToegangFout(f"'{p['naam']}' is uit dienst: eerst herstellen")
        nu = _nu()
        vf, vu = tijdstip(valid_from), tijdstip(valid_until)
        oud_vu = tijdstip(p.get("valid_until"))
        if vu and vu <= nu and vu != oud_vu:
            raise ToegangFout("het einde van de geldigheid ligt in het verleden")
        if vf and vu and vu <= vf:
            raise ToegangFout("het einde van de geldigheid moet na het begin liggen")
        if vf and vf <= nu:
            vf = None
        wachtte = self.wacht(p)
        p["valid_from"], p["valid_until"] = _iso(vf), _iso(vu)
        actie = "geldigheid ingesteld"
        if vf:
            if p["status"] == "active" or wachtte:
                p["status"], p["until"] = "blocked", p["valid_from"]
            elif p["status"] == "blocked" and p.get("until") and tijdstip(p["until"]) < vf:
                p["until"] = p["valid_from"]
        elif wachtte:
            p["status"], p["until"] = "active", None
            actie = "geldig vanaf nu"
        p["gewijzigd"] = _iso(nu)
        self.audit(wie, actie, p["naam"], p["deuren"], self.geldig_detail(p))
        self.gewijzigd()
        return p

    def blokkeren(self, wie: str, pid: str, until: Any = None) -> dict:
        p = self.persoon(pid)
        if p["status"] == "retired":
            raise ToegangFout(f"'{p['naam']}' is uit dienst: eerst herstellen")
        u = tijdstip(until)
        if u and u <= _nu():
            raise ToegangFout("het einde van de blokkering ligt in het verleden")
        al = p["status"] == "blocked"
        p["status"], p["until"] = "blocked", _iso(u)
        p["gewijzigd"] = _iso(_nu())
        self.audit(wie, "blokkering aangepast" if al else "geblokkeerd", p["naam"], p["deuren"],
                   f"tot {_kort(u)}" if u else "tot deblokkeren")
        self.gewijzigd()
        return p

    def deblokkeren(self, wie: str, pid: str, actie: str = "gedeblokkeerd", respect_start: bool = False) -> dict:
        p = self.persoon(pid)
        nu = _nu()
        vf = tijdstip(p.get("valid_from"))
        if respect_start and vf and vf > nu:
            p["status"], p["until"] = "blocked", p["valid_from"]
            self.audit(wie, actie, p["naam"], p["deuren"], f"wacht op het begin, tot {_kort(vf)}")
        else:
            p["valid_from"] = None
            vu = tijdstip(p.get("valid_until"))
            if vu and vu <= nu:
                p["valid_until"] = None
            p["status"], p["until"] = "active", None
            self.audit(wie, actie, p["naam"], p["deuren"])
        p["gewijzigd"] = _iso(nu)
        self.gewijzigd()
        return p

    def uit_dienst(self, wie: str, pid: str, reden: str = "") -> dict:
        p = self.persoon(pid)
        p["status"], p["until"] = "retired", None
        p["gewijzigd"] = _iso(_nu())
        if p.get("gezicht"):
            p["gezicht"] = None
            reden = ", ".join(x for x in (reden, "gezichtsafdruk gewist") if x)
        self.audit(wie, "uit dienst", p["naam"], p["deuren"], reden)
        self.gewijzigd()
        return p

    def herstellen(self, wie: str, pid: str) -> dict:
        return self.deblokkeren(wie, pid, actie="hersteld", respect_start=True)

    def vergeten(self, wie: str, pid: str) -> None:
        p = self.persoon(pid)
        if p["status"] != "retired":
            raise ToegangFout("enkel wat uit dienst is, kan uit de lijst")
        del self.data["personen"][pid]
        self.audit(wie, "definitief verwijderd", p["naam"], p["deuren"])
        self.gewijzigd()

    def verwijderen(self, wie: str, pid: str) -> None:
        p = self.persoon(pid)
        del self.data["personen"][pid]
        self.audit(wie, "verwijderd", p["naam"], p["deuren"])
        self.gewijzigd()

    def kaart_toevoegen(self, wie: str, pid: str, kaart: str) -> None:
        p = self.persoon(pid)
        nr = kaart_norm(kaart)
        if not 4 <= len(nr) <= 16:
            raise ToegangFout("badgenummer: 4 tot 16 tekens 0-9 en A-F")
        for q in self.data["personen"].values():
            if any(k["nr"] == nr for k in q["kaarten"]):
                raise ToegangFout(f"badge {nr} is al gekend bij {q['naam']}")
        z = zout()
        p["kaarten"].append({"nr": nr, "zout": z, "hash": hash_waarde(z, nr)})
        self.audit(wie, "aangepast", p["naam"], p["deuren"], "badge toegevoegd", soort="badge")
        self.gewijzigd()

    # ------------------------------------------------------------------ planner

    def planner(self) -> list[str]:
        """Elke minuut: einde geldigheid, einde blokkering en begin geldigheid. Geeft wat gebeurde."""
        nu = _nu()
        gedaan = []
        for p in list(self.data["personen"].values()):
            vu = tijdstip(p.get("valid_until"))
            if vu and vu <= nu and p["status"] != "retired":
                self.uit_dienst("planner", p["id"], f"einde geldigheid {_kort(vu)}")
                gedaan.append(f"{p['naam']}: uit dienst")
                continue
            u = tijdstip(p.get("until"))
            if p["status"] == "blocked" and u and u <= nu:
                actie = "begin geldigheid" if p.get("until") == p.get("valid_from") else "automatisch gedeblokkeerd"
                self.deblokkeren("planner", p["id"], actie=actie, respect_start=True)
                gedaan.append(f"{p['naam']}: {actie}")
        # vervallen uitnodigingen opruimen (30 dagen na het verlopen)
        grens = _iso(nu - timedelta(days=30))
        for i, u in list(self.data["uitnodigingen"].items()):
            if (u.get("verloopt") or "") < grens:
                del self.data["uitnodigingen"][i]
        return gedaan

    # ------------------------------------------------------------------ lijst voor een tablet

    def lijst_voor_deur(self, deur_id: str, tablet: dict | None = None) -> dict:
        d = self.deur(deur_id)
        kid = (tablet or {}).get("pubkey_id")
        personen = []
        for p in self.data["personen"].values():
            if p["status"] != "active" or deur_id not in p["deuren"]:
                continue
            vf, vu = tijdstip(p.get("valid_from")), tijdstip(p.get("valid_until"))
            personen.append({
                "id": p["id"], "naam": p["naam"],
                "van": vf.date().isoformat() if vf else None, "tot": vu.date().isoformat() if vu else None,
                "geldig_van": p.get("valid_from"), "geldig_tot": p.get("valid_until"),
                "schema": p["schema"],
                "pin": {"zout": p["pin_zout"], "hash": p["pin_hash"], "lengte": len(p["pin"])} if p.get("pin") else None,
                "kaarten": [{"zout": k["zout"], "hash": k["hash"]} for k in p["kaarten"]],
                "gezicht": self._gezicht_voor(p, kid),
            })
        personen.sort(key=lambda x: x["naam"].lower())
        return {"schema": 1, "versie": self.versie, "gemaakt": _iso(_nu()),
                "deur": {**{k: d[k] for k in ("id", "naam", "modus", "modus_sinds", "modus_door")},
                         "klavier_tot": d.get("klavier_tot") if (d.get("klavier_tot") or "") > _iso(_nu()) else None},
                "hash": {"algoritme": "sha256-iter", "rondes": 2000}, "gezicht_kdf": None, "personen": personen}

    @staticmethod
    def _gezicht_voor(p: dict, kid: str | None) -> dict | bool:
        g = p.get("gezicht")
        if not g or not kid or kid not in g["sleutels"]:
            return False
        return {"versie": g["versie"], "iv": g["iv"], "data": g["data"], "sleutel": g["sleutels"][kid],
                "pubkey_id": kid, "aad": GEZICHT_AAD + p["id"], "aantal": g["aantal"],
                "toestemming": g["toestemming"], "sinds": g["sinds"]}

    # ------------------------------------------------------------------ gezicht

    def gezicht_tablets(self, p: dict) -> list[dict]:
        """Toestellen waarvoor de gsm de afdruk versleutelt: tablets en gezichtsservers aan de deuren van deze persoon,
        plus elke gezichtsserver als bewaarder. Die zet de afdruk later door naar nieuwe toestellen of deuren,
        zodat niemand opnieuw moet scannen."""
        uit, gezien = [], set()
        for t in self.data["tablets"].values():
            if not t.get("pubkey") or t["pubkey_id"] in gezien:
                continue
            server = t.get("soort") == "server"
            if t["deur_id"] in p["deuren"]:
                deur = self.data["deuren"].get(t["deur_id"], {}).get("naam", "?")
            elif server:
                deur = "Nieuwe toestellen"
            else:
                continue
            gezien.add(t["pubkey_id"])
            uit.append({"pubkey_id": t["pubkey_id"], "pubkey": t["pubkey"], "tablet": t["naam"], "soort": t.get("soort", "tablet"),
                        "deur": deur})
        return uit

    # ------------------------------------------------------------------ doorzetten door de gezichtsserver

    def herverpak_taken(self, server: dict) -> list[dict]:
        """Afdrukken die de server kan lezen maar die nog ontbreken voor een toestel aan een deur van die persoon."""
        kid = server.get("pubkey_id")
        if server.get("soort") != "server" or not kid:
            return []
        taken = []
        for p in self.data["personen"].values():
            g = p.get("gezicht")
            if not g or p["status"] == "retired" or kid not in g["sleutels"]:
                continue
            doel = [{"pubkey_id": t["pubkey_id"], "pubkey": t["pubkey"]} for t in self.gezicht_tablets(p) if t["pubkey_id"] not in g["sleutels"]]
            if doel:
                taken.append({"persoon_id": p["id"], "sleutel": g["sleutels"][kid], "aad": GEZICHT_AAD + p["id"], "doel": doel})
        return taken

    def herverpak_zetten(self, server: dict, persoon_id: str, sleutels: dict) -> int:
        """De server stuurt de AES sleutel van een afdruk, verpakt voor nieuwe toestellen. HA ziet enkel verpakte sleutels."""
        kid = server.get("pubkey_id")
        p = self.data["personen"].get(persoon_id)
        if server.get("soort") != "server" or not p or not p.get("gezicht") or kid not in p["gezicht"]["sleutels"]:
            raise ToegangFout("onbekende afdruk")
        mag = {t["pubkey_id"] for t in self.gezicht_tablets(p)}
        nieuw = 0
        for doel, s in (sleutels or {}).items():
            if doel not in mag or doel in p["gezicht"]["sleutels"]:
                continue
            try:
                b64(s, "sleutel", 256, 512)
            except GezichtFout as e:
                raise ToegangFout(str(e)) from e
            p["gezicht"]["sleutels"][doel] = s
            nieuw += 1
        if nieuw:
            self.audit("gezichtsserver", "aangepast", p["naam"], p["deuren"], f"gezichtsafdruk doorgezet naar {nieuw} toestel(len)")
            self.gewijzigd()
        return nieuw

    def gezicht_zetten(self, p: dict, huidige_pin: str, pakket: dict, vertrouwd: bool = False) -> dict:
        if not vertrouwd:
            self._portaal_controle(p, huidige_pin)
        if p["status"] == "retired":
            raise ToegangFout("Je toegang is stopgezet.")
        try:
            g = controleer_pakket(pakket, {t["pubkey_id"] for t in self.gezicht_tablets(p)})
        except GezichtFout as e:
            raise ToegangFout(str(e)) from e
        g["sinds"] = _iso(_nu())
        p["gezicht"] = g
        self.audit(f"{p['naam']} (portaal)", "aangepast", p["naam"], p["deuren"],
                   f"gezichtsafdruk ingesteld ({g['aantal']} afdrukken, {len(g['sleutels'])} tablet(s))")
        self.gewijzigd()
        return g

    def gezicht_wissen(self, wie: str, pid: str, huidige_pin: str | None = None) -> None:
        p = self.persoon(pid)
        if huidige_pin is not None:
            self._portaal_controle(p, huidige_pin)
        if not p.get("gezicht"):
            return
        p["gezicht"] = None
        self.audit(wie, "aangepast", p["naam"], p["deuren"], "gezichtsafdruk gewist")
        self.gewijzigd()

    def _portaal_controle(self, p: dict, huidige_pin: str) -> None:
        if p.get("fouten", 0) >= PORTAAL_FOUTEN_MAX:
            raise ToegangFout("Je portaal is geblokkeerd na te veel foute codes. Vraag een nieuwe link.")
        if not p.get("pin") or not secrets.compare_digest(pin_norm(huidige_pin or ""), p["pin"]):
            self.portaal_fout("persoon", p)
            raise ToegangFout("Je huidige code klopt niet.")
        p["fouten"] = 0

    def _pubkey_zetten(self, t: dict, waarde) -> None:
        try:
            der, kid = publieke_sleutel(waarde)
        except GezichtFout as e:
            raise ToegangFout(str(e)) from e
        if t.get("pubkey_id") == kid:
            return
        t["pubkey"], t["pubkey_id"], t["pubkey_sinds"] = der, kid, _iso(_nu())
        # wie al een gezicht had, mist het nu op deze tablet: melden (Work mailt dan de portaallink om opnieuw te scannen)
        servers = {x["pubkey_id"] for x in self.data["tablets"].values() if x.get("soort") == "server" and x.get("pubkey_id") and x is not t}
        # wie een afdruk heeft die een gezichtsserver kan lezen, krijgt ze automatisch doorgezet: niet opnieuw scannen
        missen = [p for p in self.data["personen"].values()
                  if p.get("gezicht") and p["status"] != "retired" and t["deur_id"] in p["deuren"] and kid not in p["gezicht"]["sleutels"]
                  and not servers & set(p["gezicht"]["sleutels"])]
        if missen:
            self.hass.bus.async_fire(f"{DOMAIN}_gezicht_ontbreekt", {"tablet": t["naam"], "deur_id": t["deur_id"],
                                                                     "personen": [{"persoon_id": p["id"], "naam": p["naam"],
                                                                                   "werk_id": p.get("werk_id")} for p in missen]})
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.hass, f"{'Gezichtsserver' if t.get('soort') == 'server' else 'Tablet'} **{t['naam']}** heeft een nieuwe sleutel. "
                f"Gezichtsherkenning werkt daar nog niet voor: "
                f"{', '.join(p['naam'] for p in missen)}. Zij scannen opnieuw via hun portaallink "
                "(Btechnics Work stuurt die automatisch voor gekoppelde medewerkers).",
                title="Btechnics Toegang: opnieuw scannen", notification_id=f"{DOMAIN}_gezicht_{t['id']}")
        self.audit("tablet", "tablet sleutel", self.data["deuren"].get(t["deur_id"], {}).get("naam", "?"),
                   soort="deur", detail=f"{t['naam']}: nieuwe sleutel voor gezichtsafdrukken")
        self.gewijzigd()

    # ------------------------------------------------------------------ tablets

    def koppelcode_maken(self, deur_id: str, naam: str | None = None) -> str:
        self.deur(deur_id)
        nu = time.monotonic()
        self._koppelcodes = {c: v for c, v in self._koppelcodes.items() if v["tot"] > nu}
        while True:
            code = f"{secrets.randbelow(1_000_000):06d}"
            if code not in self._koppelcodes:
                break
        self._koppelcodes[code] = {"deur_id": deur_id, "naam": naam, "tot": nu + KOPPELCODE_GELDIG}
        self._foute_koppelpogingen = 0
        return code

    def koppelen(self, code: str, toestel_id: str, naam: str | None, app_versie: str | None, pubkey=None,
                 soort: str | None = None) -> dict:
        """soort "server": de gezichtsserver (container op Unraid) voor een deur met camera. Die gebruikt dezelfde
        endpoints als een tablet (/lijst, /wacht, /status, /open met methode gezicht, /foto)."""
        nu = time.monotonic()
        info = self._koppelcodes.get(str(code).strip())
        if not info or info["tot"] < nu:
            self._foute_koppelpogingen += 1
            if self._foute_koppelpogingen >= KOPPELCODE_POGINGEN:
                self._koppelcodes.clear()
                self._foute_koppelpogingen = 0
            raise ToegangFout("Koppelcode ongeldig of vervallen")
        del self._koppelcodes[code]
        for tid, t in list(self.data["tablets"].items()):
            if toestel_id and t.get("toestel_id") == toestel_id:
                del self.data["tablets"][tid]
        sleutel = secrets.token_urlsafe(32)
        soort = "server" if soort == "server" else "tablet"
        standaard = "Gezichtsserver" if soort == "server" else "Tablet"
        t = {"id": _id("t"), "soort": soort, "deur_id": info["deur_id"], "naam": (naam or info["naam"] or standaard).strip()[:60],
             "toestel_id": toestel_id or None, "sleutel_hash": sleutel_hash(sleutel), "gekoppeld": _iso(_nu()),
             "app_versie": app_versie, "status": {}, "laatst": None}
        self.data["tablets"][t["id"]] = t
        if pubkey:
            try:
                self._pubkey_zetten(t, pubkey)
            except ToegangFout:
                pass  # koppelen lukt toch; de tablet krijgt de fout bij /status
        self.audit("tablet", "gezichtsserver gekoppeld" if soort == "server" else "tablet gekoppeld",
                   self.data["deuren"][t["deur_id"]]["naam"], soort="deur", detail=t["naam"])
        self.gewijzigd(lijst_veranderd=False)
        if self.bij_nieuwe_tablet:
            self.bij_nieuwe_tablet()
        return {"tablet_id": t["id"], "deur_id": t["deur_id"], "sleutel": sleutel, "pubkey_id": t.get("pubkey_id")}

    def tablet_voor_sleutel(self, sleutel: str) -> dict | None:
        h = sleutel_hash(sleutel)
        for t in self.data["tablets"].values():
            if secrets.compare_digest(t["sleutel_hash"], h):
                return t
        return None

    def tablet_intrekken(self, tablet_id: str, wie: str = "") -> None:
        t = self.data["tablets"].get(tablet_id)
        if not t:
            raise ToegangFout(f"Tablet {tablet_id} bestaat niet")
        del self.data["tablets"][tablet_id]
        self.audit(wie, "tablet ontkoppeld", self.data["deuren"].get(t["deur_id"], {}).get("naam", "?"), soort="deur", detail=t["naam"])
        self.gewijzigd(lijst_veranderd=False)

    def tablet_hernoemen(self, tablet_id: str, naam: str, wie: str = "") -> None:
        t = self.data["tablets"].get(tablet_id)
        naam = (naam or "").strip()[:60]
        if not t or not naam:
            raise ToegangFout("Tablet of naam ontbreekt")
        oud, t["naam"] = t["naam"], naam
        self.audit(wie, "tablet hernoemd", self.data["deuren"].get(t["deur_id"], {}).get("naam", "?"), soort="deur",
                   detail=f"{oud} wordt {naam}")
        self.gewijzigd(lijst_veranderd=False)

    def tablet_status(self, tablet: dict, status: dict) -> str | None:
        """Geeft een foutmelding over de publieke sleutel terug, of None."""
        tablet["status"] = {k: status.get(k) for k in ("app_versie", "version_code", "versie", "log_wachtrij", "batterij", "nfc",
                                                       "schema", "personen", "gezichten", "laatste_lijst", "echtheid")}
        if status.get("app_versie"):
            tablet["app_versie"] = status["app_versie"]
        tablet["laatst"] = _iso(_nu())
        tablet["laatst_mono"] = time.monotonic()
        self._bewaar()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        if status.get("pubkey"):
            try:
                self._pubkey_zetten(tablet, status["pubkey"])
            except ToegangFout as e:
                return str(e)
        return None

    def gezichten_verwacht(self, t: dict) -> int:
        """Aantal personen waarvoor deze tablet een gezichtspakket in zijn lijst krijgt."""
        kid = t.get("pubkey_id")
        if not kid:
            return 0
        return sum(1 for p in self.data["personen"].values() if p["status"] == "active" and t["deur_id"] in p["deuren"]
                   and p.get("gezicht") and kid in p["gezicht"]["sleutels"])

    def tablet_online(self, t: dict, binnen: float) -> bool:
        m = t.get("laatst_mono")
        return bool(m and time.monotonic() - m < binnen)

    # ------------------------------------------------------------------ tabletmeldingen (geen toegangen)

    def gebeurtenis(self, regel: dict) -> None:
        self.data["gebeurtenissen"].append(regel)
        del self.data["gebeurtenissen"][:-GEBEURTENISSEN_MAX]
        self._bewaar()

    # ------------------------------------------------------------------ uitnodigingen en portaal

    def uitnodiging_maken(self, wie: str, *, deuren: list[str], valid_from: Any = None, valid_until: Any = None,
                          schema: Any = None, naam: str = "", dagen: int = UITNODIGING_DAGEN) -> tuple[dict, str]:
        ids = self._deuren_ids(deuren)
        if not ids:
            raise ToegangFout("kies minstens een deur")
        vf, vu = tijdstip(valid_from), tijdstip(valid_until)
        if vu and vu <= _nu():
            raise ToegangFout("het einde van de geldigheid ligt in het verleden")
        if vf and vu and vu <= vf:
            raise ToegangFout("het einde van de geldigheid moet na het begin liggen")
        token = secrets.token_urlsafe(24)
        u = {"id": _id("u"), "hash": token_hash(token), "deuren": ids, "valid_from": _iso(vf), "valid_until": _iso(vu),
             "schema": self._schema(schema), "naam": (naam or "").strip(), "gemaakt": _iso(_nu()),
             "verloopt": _iso(_nu() + timedelta(days=dagen)), "door": wie, "gebruikt": None, "fouten": 0}
        self.data["uitnodigingen"][u["id"]] = u
        self.audit(wie, "uitnodiging gemaakt", u["naam"] or "nieuwe medewerker", ids, f"link geldig tot {_kort(u['verloopt'])}")
        self.gewijzigd(lijst_veranderd=False)
        return u, token

    def uitnodiging_intrekken(self, wie: str, uid: str) -> None:
        u = self.data["uitnodigingen"].pop(uid, None)
        if not u:
            raise ToegangFout("onbekende uitnodiging")
        self.audit(wie, "uitnodiging ingetrokken", u["naam"] or "nieuwe medewerker", u["deuren"])
        self.gewijzigd(lijst_veranderd=False)

    # ------------------------------------------------------------------ link vanuit Btechnics Work (zonder code)

    WERK_LINK_MAX = 600      # een link van Work is hoogstens 10 minuten geldig
    WERK_SESSIE = 1800       # daarna 30 minuten werken in het portaal

    def werk_geheim_zetten(self, wie: str, geheim: str) -> None:
        """Gedeeld geheim met Work (komt via het IOT kanaal, verschijnt nergens in beeld)."""
        if not isinstance(geheim, str) or len(geheim) < 32 or len(geheim) > 128:
            raise ToegangFout("geheim moet 32 tot 128 tekens zijn")
        self.data["werk_geheim"] = geheim
        self.audit(wie, "aangepast", "Work koppeling", soort="systeem", detail="geheim voor links vanuit Work gezet")
        self.gewijzigd(lijst_veranderd=False)

    def werk_sessie(self, werk_id: str, t: str, s: str) -> str:
        """Controleert een link van Work (HMAC-SHA256 van "werk_id.t" met het gedeelde geheim) en geeft een
        portaalsessie terug waarin de medewerker zonder huidige code kan wijzigen. Elke link werkt een keer."""
        geheim = self.data.get("werk_geheim")
        if not geheim:
            raise ToegangFout("De koppeling met Work is nog niet ingesteld.")
        try:
            tot = int(t)
        except (TypeError, ValueError) as e:
            raise ToegangFout("Deze link is niet geldig.") from e
        nu = time.time()
        verwacht = hmac.new(geheim.encode(), f"{werk_id}.{tot}".encode(), hashlib.sha256).hexdigest()
        if not s or not secrets.compare_digest(verwacht, str(s).lower()):
            raise ToegangFout("Deze link is niet geldig.")
        if not nu <= tot <= nu + self.WERK_LINK_MAX:
            raise ToegangFout("Deze link is verlopen. Open hem opnieuw vanuit Work.")
        gebruikt = self.__dict__.setdefault("_werk_gebruikt", {})
        for k in [k for k, v in gebruikt.items() if v < nu]:
            del gebruikt[k]
        if s in gebruikt:
            raise ToegangFout("Deze link werd al gebruikt. Open hem opnieuw vanuit Work.")
        p = next((x for x in self.data["personen"].values() if x.get("werk_id") == werk_id and x["status"] != "retired"), None)
        if not p:
            raise ToegangFout("Je hebt nog geen toegang. Vraag het aan je verantwoordelijke.")
        gebruikt[s] = tot
        token = secrets.token_urlsafe(24)
        self.__dict__.setdefault("_sessies", {})[token_hash(token)] = {"pid": p["id"], "tot": nu + self.WERK_SESSIE}
        p["fouten"] = 0
        return token

    def portaal_vertrouwd(self, token: str) -> bool:
        """True als de link van een sessie uit Work komt: dan geen huidige code nodig."""
        x = self.__dict__.get("_sessies", {}).get(token_hash(token))
        return bool(x and x["tot"] > time.time())

    def portaal_zoek(self, token: str) -> tuple[str, dict] | None:
        """('uitnodiging', u) of ('persoon', p) voor een portaallink, anders None."""
        h = token_hash(token)
        sessie = self.__dict__.get("_sessies", {}).get(h)
        if sessie and sessie["tot"] > time.time() and sessie["pid"] in self.data["personen"]:
            return "persoon", self.data["personen"][sessie["pid"]]
        for u in self.data["uitnodigingen"].values():
            if secrets.compare_digest(u["hash"], h):
                return "uitnodiging", u
        for p in self.data["personen"].values():
            if p.get("portaal_hash") and secrets.compare_digest(p["portaal_hash"], h):
                return "persoon", p
        return None

    def uitnodiging_bruikbaar(self, u: dict) -> str | None:
        if u.get("gebruikt"):
            return "Deze uitnodiging is al gebruikt."
        if (u.get("verloopt") or "") < _iso(_nu()):
            return "Deze uitnodiging is verlopen. Vraag een nieuwe link."
        if u.get("fouten", 0) >= PORTAAL_FOUTEN_MAX:
            return "Deze uitnodiging is geblokkeerd na te veel pogingen. Vraag een nieuwe link."
        return None

    def portaal_fout(self, soort: str, obj: dict) -> None:
        obj["fouten"] = obj.get("fouten", 0) + 1
        self._bewaar()

    def portaal_token(self, wie: str, pid: str) -> str:
        p = self.persoon(pid)
        token = secrets.token_urlsafe(24)
        p["portaal_hash"], p["portaal_token"], p["fouten"] = token_hash(token), token, 0
        self.audit(wie, "portaallink gemaakt", p["naam"], p["deuren"])
        self.gewijzigd(lijst_veranderd=False)
        return token

    def aanmelden(self, token: str, *, voornaam: str, achternaam: str, telefoon: str, email: str, pin: str) -> tuple[dict, str]:
        gevonden = self.portaal_zoek(token)
        if not gevonden or gevonden[0] != "uitnodiging":
            raise ToegangFout("Deze link is niet (meer) geldig.")
        u = gevonden[1]
        reden = self.uitnodiging_bruikbaar(u)
        if reden:
            raise ToegangFout(reden)
        try:
            p = self.toevoegen("portaal", voornaam=voornaam, achternaam=achternaam, pin=pin, deuren=u["deuren"],
                               valid_from=u.get("valid_from"), valid_until=u.get("valid_until"), schema=u.get("schema"),
                               telefoon=telefoon, email=email, soort="vast", bron="portaal", streng=True)
        except ToegangFout:
            self.portaal_fout("uitnodiging", u)
            raise
        u["gebruikt"] = p["id"]
        if u.get("werk_id") and not any(q.get("werk_id") == u["werk_id"] for q in self.data["personen"].values()):
            p["werk_id"] = u["werk_id"]
        nieuw = secrets.token_urlsafe(24)
        p["portaal_hash"], p["portaal_token"], p["fouten"] = token_hash(nieuw), nieuw, 0
        self.data["audit"][-1]["user"] = f"{p['naam']} (portaal)"
        self.gewijzigd()
        return p, nieuw

    def portaal_wijzigen(self, p: dict, huidige_pin: str, *, telefoon: str | None = None, email: str | None = None,
                         nieuwe_pin: str | None = None, vertrouwd: bool = False) -> dict:
        """Wijziging vanuit het eigen portaal: met de huidige code, of zonder als de medewerker via Work kwam."""
        if not vertrouwd:
            self._portaal_controle(p, huidige_pin)
        wie = f"{p['naam']} (portaal)"
        if nieuwe_pin:
            nieuw = self.controleer_pin(nieuwe_pin, behalve=p["id"], streng=True)
            if nieuw != p["pin"]:
                self._pin_zetten(p, nieuw)
                self.audit(wie, "aangepast", p["naam"], p["deuren"], "code gewijzigd")
        self.bijwerken(wie, p["id"], telefoon=telefoon, email=email)
        self.gewijzigd()
        return p

    def foto_gezet(self, wie: str, pid: str) -> None:
        p = self.persoon(pid)
        p["foto"] = True
        p["foto_v"] = int(time.time())
        self.audit(wie, "aangepast", p["naam"], p["deuren"], "foto gewijzigd")
        self.gewijzigd(lijst_veranderd=False)
