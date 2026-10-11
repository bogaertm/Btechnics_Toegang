"""Register: personen, deuren, tablets, koppelcodes en logboek, bewaard in .storage van HA."""
from __future__ import annotations

import asyncio
import secrets
import time
from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    KOPPELCODE_GELDIG,
    KOPPELCODE_POGINGEN,
    LOG_KEEP,
    MODI,
    MODUS_STANDAARD,
    PIN_MIN,
    SIGNAL_UPDATE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .geldigheid import controleer_blok
from .hashing import hash_waarde, kaart_norm, pin_norm, sleutel_hash, zout


class ToegangFout(Exception):
    """Fout die als leesbare melding naar de gebruiker gaat."""


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(3)}"


def _nu_iso() -> str:
    return dt_util.now().replace(microsecond=0).isoformat()


class Register:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {}
        self._koppelcodes: dict[str, dict] = {}   # enkel in geheugen, 10 minuten geldig
        self._foute_koppelpogingen = 0
        self._wijziging = asyncio.Condition()
        self.bij_nieuwe_tablet = None   # gezet door __init__: entiteiten herladen

    # ---------- opslag ----------

    async def async_laden(self) -> None:
        data = await self._store.async_load() or {}
        data.setdefault("versie", 1)
        data.setdefault("personen", {})
        data.setdefault("deuren", {})
        data.setdefault("tablets", {})
        data.setdefault("log", [])
        for t in data["tablets"].values():
            t.pop("laatst_mono", None)   # monotone klok begint opnieuw na een herstart
        self.data = data

    @callback
    def _bewaar(self) -> None:
        self._store.async_delay_save(lambda: self.data, 1)

    @callback
    def gewijzigd(self, lijst_veranderd: bool = True) -> None:
        """Bewaren, entiteiten bijwerken en wachtende tablets wekken."""
        if lijst_veranderd:
            self.data["versie"] += 1
        self._bewaar()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        self.hass.async_create_task(self._wek())

    async def _wek(self) -> None:
        async with self._wijziging:
            self._wijziging.notify_all()

    async def wacht_op_versie(self, versie: int, max_s: float) -> bool:
        """True zodra de versie verschilt van wat de tablet kent, False na max_s seconden."""
        if self.data["versie"] != versie:
            return True
        try:
            async with self._wijziging:
                await asyncio.wait_for(
                    self._wijziging.wait_for(lambda: self.data["versie"] != versie), max_s
                )
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def versie(self) -> int:
        return self.data["versie"]

    # ---------- deuren ----------

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
        raise ToegangFout(f"Deur {naam_of_id} bestaat niet")

    def deur_toevoegen(self, naam: str, open_entiteit: str | None = None) -> dict:
        naam = naam.strip()
        if not naam:
            raise ToegangFout("Geef de deur een naam")
        if any(d["naam"].lower() == naam.lower() for d in self.data["deuren"].values()):
            raise ToegangFout(f"Er bestaat al een deur {naam}")
        d = {
            "id": _id("d"), "naam": naam, "open_entiteit": open_entiteit or None,
            "modus": MODUS_STANDAARD, "modus_sinds": _nu_iso(), "modus_door": "ha",
        }
        self.data["deuren"][d["id"]] = d
        self.gewijzigd()
        return d

    def deur_wijzigen(self, deur_id: str, naam: str | None = None, open_entiteit: str | None = None) -> dict:
        d = self.deur(deur_id)
        if naam is not None and naam.strip():
            d["naam"] = naam.strip()
        if open_entiteit is not None:
            d["open_entiteit"] = open_entiteit or None
        self.gewijzigd()
        return d

    def deur_verwijderen(self, deur_id: str) -> None:
        self.deur(deur_id)
        if any(t["deur_id"] == deur_id for t in self.data["tablets"].values()):
            raise ToegangFout("Er is nog een tablet aan deze deur gekoppeld. Trek die eerst in.")
        del self.data["deuren"][deur_id]
        for p in self.data["personen"].values():
            if deur_id in p["deuren"]:
                p["deuren"].remove(deur_id)
        self.gewijzigd()

    def modus_zetten(self, deur_id: str, modus: str, door: str) -> dict:
        if modus not in MODI:
            raise ToegangFout(f"Onbekende modus {modus}, kies uit {', '.join(MODI)}")
        d = self.deur(deur_id)
        d["modus"], d["modus_sinds"], d["modus_door"] = modus, _nu_iso(), door
        self.gewijzigd()
        return d

    # ---------- personen ----------

    def persoon(self, persoon_id: str) -> dict:
        p = self.data["personen"].get(persoon_id)
        if not p:
            raise ToegangFout(f"Persoon {persoon_id} bestaat niet")
        return p

    def persoon_op_naam(self, naam_of_id: str) -> dict:
        if naam_of_id in self.data["personen"]:
            return self.data["personen"][naam_of_id]
        gevonden = [p for p in self.data["personen"].values() if p["naam"].lower() == str(naam_of_id).strip().lower()]
        if len(gevonden) == 1:
            return gevonden[0]
        if len(gevonden) > 1:
            raise ToegangFout(f"Meerdere personen heten {naam_of_id}, gebruik het id")
        raise ToegangFout(f"Persoon {naam_of_id} bestaat niet")

    def _controleer_pin(self, pin: str, behalve: str | None = None) -> str:
        pin = pin_norm(pin)
        if len(pin) < PIN_MIN:
            raise ToegangFout(f"Een pincode heeft minstens {PIN_MIN} cijfers")
        for p in self.data["personen"].values():
            if p["id"] != behalve and p.get("pin") == pin:
                raise ToegangFout(f"Deze pincode is al in gebruik bij {p['naam']}")
        return pin

    def _deuren_ids(self, deuren: list[str] | None) -> list[str]:
        return [self.deur_op_naam(x)["id"] for x in (deuren or [])]

    @staticmethod
    def _datum(s: Any) -> str | None:
        if s in (None, ""):
            return None
        return date.fromisoformat(str(s)).isoformat()

    def _schema(self, schema: Any) -> list[dict]:
        try:
            return [controleer_blok(b) for b in (schema or [])]
        except ValueError as e:
            raise ToegangFout(f"Schema klopt niet: {e}") from e

    def persoon_toevoegen(self, naam: str, pin: str | None, deuren: list[str] | None,
                          van: Any = None, tot: Any = None, schema: Any = None) -> dict:
        naam = naam.strip()
        if not naam:
            raise ToegangFout("Geef de persoon een naam")
        p = {
            "id": _id("p"), "naam": naam, "actief": True,
            "pin": self._controleer_pin(pin) if pin else None,
            "pin_zout": zout(), "kaarten": [],
            "deuren": self._deuren_ids(deuren),
            "van": self._datum(van), "tot": self._datum(tot), "schema": self._schema(schema),
            "gemaakt": _nu_iso(),
        }
        p["pin_hash"] = hash_waarde(p["pin_zout"], p["pin"]) if p["pin"] else None
        self.data["personen"][p["id"]] = p
        self.gewijzigd()
        return p

    def persoon_wijzigen(self, persoon_id: str, **v: Any) -> dict:
        p = self.persoon(persoon_id)
        if v.get("naam"):
            p["naam"] = v["naam"].strip()
        if "pin" in v and v["pin"] is not None:
            if v["pin"] == "":
                p["pin"], p["pin_hash"] = None, None
            else:
                p["pin"] = self._controleer_pin(v["pin"], behalve=p["id"])
                p["pin_zout"] = zout()
                p["pin_hash"] = hash_waarde(p["pin_zout"], p["pin"])
        if v.get("deuren") is not None:
            p["deuren"] = self._deuren_ids(v["deuren"])
        if "van" in v:
            p["van"] = self._datum(v["van"])
        if "tot" in v:
            p["tot"] = self._datum(v["tot"])
        if v.get("schema") is not None:
            p["schema"] = self._schema(v["schema"])
        if v.get("actief") is not None:
            p["actief"] = bool(v["actief"])
        self.gewijzigd()
        return p

    def persoon_verwijderen(self, persoon_id: str) -> None:
        self.persoon(persoon_id)
        del self.data["personen"][persoon_id]
        self.gewijzigd()

    def kaart_toevoegen(self, persoon_id: str, kaart: str) -> None:
        p = self.persoon(persoon_id)
        nr = kaart_norm(kaart)
        if len(nr) < 4:
            raise ToegangFout("Kaartnummer te kort")
        for q in self.data["personen"].values():
            if any(k["nr"] == nr for k in q["kaarten"]):
                raise ToegangFout(f"Deze kaart is al gekoppeld aan {q['naam']}")
        z = zout()
        p["kaarten"].append({"nr": nr, "zout": z, "hash": hash_waarde(z, nr)})
        self.gewijzigd()

    # ---------- lijst voor een tablet ----------

    def lijst_voor_deur(self, deur_id: str) -> dict:
        d = self.deur(deur_id)
        personen = []
        for p in self.data["personen"].values():
            if not p["actief"] or deur_id not in p["deuren"]:
                continue
            personen.append({
                "id": p["id"], "naam": p["naam"],
                "van": p["van"], "tot": p["tot"], "schema": p["schema"],
                "pin": ({"zout": p["pin_zout"], "hash": p["pin_hash"], "lengte": len(p["pin"])}
                        if p.get("pin") else None),
                "kaarten": [{"zout": k["zout"], "hash": k["hash"]} for k in p["kaarten"]],
                "gezicht": False,
            })
        personen.sort(key=lambda x: x["naam"].lower())
        return {
            "schema": 1,
            "versie": self.versie,
            "gemaakt": _nu_iso(),
            "deur": {k: d[k] for k in ("id", "naam", "modus", "modus_sinds", "modus_door")},
            "hash": {"algoritme": "sha256-iter", "rondes": 2000},
            "gezicht_kdf": None,
            "personen": personen,
        }

    # ---------- tablets ----------

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

    def koppelen(self, code: str, toestel_id: str, naam: str | None, app_versie: str | None) -> dict:
        nu = time.monotonic()
        info = self._koppelcodes.get(str(code).strip())
        if not info or info["tot"] < nu:
            self._foute_koppelpogingen += 1
            if self._foute_koppelpogingen >= KOPPELCODE_POGINGEN:
                self._koppelcodes.clear()
                self._foute_koppelpogingen = 0
            raise ToegangFout("Koppelcode ongeldig of vervallen")
        del self._koppelcodes[code]
        # Zelfde toestel opnieuw gekoppeld: de oude sleutel vervalt
        for tid, t in list(self.data["tablets"].items()):
            if toestel_id and t.get("toestel_id") == toestel_id:
                del self.data["tablets"][tid]
        sleutel = secrets.token_urlsafe(32)
        t = {
            "id": _id("t"), "deur_id": info["deur_id"],
            "naam": (naam or info["naam"] or "Tablet").strip(),
            "toestel_id": toestel_id or None, "sleutel_hash": sleutel_hash(sleutel),
            "gekoppeld": _nu_iso(), "app_versie": app_versie, "status": {}, "laatst": None,
        }
        self.data["tablets"][t["id"]] = t
        self.gewijzigd(lijst_veranderd=False)
        if self.bij_nieuwe_tablet:
            self.bij_nieuwe_tablet()
        return {"tablet_id": t["id"], "deur_id": t["deur_id"], "sleutel": sleutel}

    def tablet_voor_sleutel(self, sleutel: str) -> dict | None:
        h = sleutel_hash(sleutel)
        for t in self.data["tablets"].values():
            if secrets.compare_digest(t["sleutel_hash"], h):
                return t
        return None

    def tablet_intrekken(self, tablet_id: str) -> None:
        if tablet_id not in self.data["tablets"]:
            raise ToegangFout(f"Tablet {tablet_id} bestaat niet")
        del self.data["tablets"][tablet_id]
        self.gewijzigd(lijst_veranderd=False)

    def tablet_status(self, tablet: dict, status: dict) -> None:
        tablet["status"] = {k: status.get(k) for k in
                            ("app_versie", "version_code", "versie", "log_wachtrij", "batterij", "nfc")}
        if status.get("app_versie"):
            tablet["app_versie"] = status["app_versie"]
        tablet["laatst"] = _nu_iso()
        tablet["laatst_mono"] = time.monotonic()
        self._bewaar()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ---------- logboek ----------

    def log_toevoegen(self, regels: list[dict]) -> list[str]:
        """Voegt regels toe, negeert dubbele uid. Geeft de uid's terug die nu bewaard zijn."""
        bekend = {r.get("uid") for r in self.data["log"]}
        ontvangen = []
        for r in regels:
            uid = r.get("uid")
            if uid and uid not in bekend:
                self.data["log"].append(r)
                bekend.add(uid)
            if uid:
                ontvangen.append(uid)
        self.data["log"].sort(key=lambda r: r.get("tijd") or "")
        del self.data["log"][:-LOG_KEEP]
        self._bewaar()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        return ontvangen
