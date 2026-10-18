"""Sync naar de Akuvox S532 (lokale HTTP API) en zijn deurlog naar de historiek.

Afspraak: enkel eigen gebruikers vanaf UserID 700001. HA houdt zelf bij welke gebruikers op het toestel van ons zijn
(persoon -> UserID en ID op het toestel). Gebruikers van SmartPlus of die er al stonden worden nooit gewijzigd of gewist,
ook niet als hun UserID toevallig boven 700000 ligt maar niet in onze lijst staat.

Op het toestel staan enkel de personen die NU toegang hebben (actief, recht op de deur van de S532, binnen hun uren).
Zo hoeft de S532 geen weekschema te kennen: de planner van HA zet ze elke minuut bij of haalt ze weg.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import USER_ID_START
from .geldigheid import reden_geweigerd
from .register import Register

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=15)
SCHEDULE_ALTIJD = "1001"   # standaardschema "altijd" op Akuvox
METHODE = {"privatepin": "pin", "pin": "pin", "publicpin": "pin", "card": "kaart", "rf card": "kaart", "face": "gezicht",
           "remote": "ha", "http": "ha", "button": "knop", "exit button": "knop", "call": "oproep"}


class AkuvoxFout(Exception):
    pass


VELDEN_VERSIE = 2   # verhogen als de velden naar het toestel veranderen: dan zet de sync iedereen opnieuw


def _pinhash(naam: str, pin: str) -> str:
    return hashlib.sha256(f"{naam}\n{pin}\n{VELDEN_VERSIE}".encode()).hexdigest()[:16]


def optie_prefix(nr: int) -> str:
    """Toestel 1 gebruikt de oude sleutels (akuvox_ip, ...), toestel 2 akuvox2_ip, ..."""
    return "akuvox_" if nr == 1 else f"akuvox{nr}_"


def akuvox_voor_deur(hass: HomeAssistant, deur_id: str):
    """Het Akuvox toestel (aan) dat deze deur bedient, of None."""
    from .const import DOMAIN
    for ak in hass.data.get(DOMAIN, {}).get("akuvoxen", []):
        if ak.aan and ak.deur_id == deur_id:
            return ak
    return None


class Akuvox:
    def __init__(self, hass: HomeAssistant, reg: Register, opties: dict, nr: int = 1) -> None:
        self.hass, self.reg, self.nr = hass, reg, nr
        v = optie_prefix(nr)
        self.ip = (opties.get(f"{v}ip") or "").strip()
        self.gebruiker = opties.get(f"{v}gebruiker") or ""
        self.wachtwoord = opties.get(f"{v}wachtwoord") or ""
        self.deur_id = opties.get(f"{v}deur") or ""
        self.aan = bool(opties.get(f"{v}aan")) and bool(self.ip) and bool(self.deur_id)
        self.status: dict[str, Any] = {"aan": self.aan, "ok": None, "fout": None, "laatst": None, "eigen": 0,
                                       "toestel": 0, "vreemd_boven_start": 0, "velden": None}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ opslag

    @property
    def _staat(self) -> dict:
        sleutel = "akuvox" if self.nr == 1 else f"akuvox{self.nr}"
        return self.reg.data.setdefault(sleutel, {"gebruikers": {}, "volgende": USER_ID_START, "laatste_log": ""})

    # ------------------------------------------------------------------ http

    async def _vraag(self, methode: str, pad: str, body: dict | None = None) -> dict:
        if not self.ip:
            raise AkuvoxFout("geen IP adres ingesteld")
        sessie = async_get_clientsession(self.hass, verify_ssl=False)
        url = f"https://{self.ip}{pad}"
        try:
            async with sessie.request(methode, url, json=body, auth=aiohttp.BasicAuth(self.gebruiker, self.wachtwoord),
                                      timeout=TIMEOUT, allow_redirects=False) as r:
                if r.status == 401:
                    raise AkuvoxFout("gebruiker of wachtwoord van de HTTP API klopt niet")
                if r.status != 200:
                    raise AkuvoxFout(f"HTTP {r.status} op {pad}")
                j = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise AkuvoxFout(f"toestel niet bereikbaar ({type(e).__name__})") from e
        if not isinstance(j, dict) or str(j.get("retcode", "0")) not in ("0", "1"):
            raise AkuvoxFout(f"toestel weigert {pad}: {str(j)[:200]}")
        return j

    async def gebruikers(self) -> list[dict]:
        uit, pagina = [], 1
        while pagina < 50:
            j = await self._vraag("GET", f"/api/user/get?page={pagina}")
            data = j.get("data") or {}
            items = data.get("item") or []
            uit += [x for x in items if isinstance(x, dict)]
            totaal = int(data.get("num") or len(uit))
            if not items or len(uit) >= totaal:
                break
            pagina += 1
        return uit

    async def _schrijf(self, actie: str, items: list[dict]) -> dict:
        return await self._vraag("POST", f"/api/user/{actie}", {"target": "user", "action": actie, "data": {"item": items}})

    # ------------------------------------------------------------------ gewenste stand

    def _gewenst(self, nu: datetime) -> dict[str, dict]:
        uit = {}
        for p in self.reg.data["personen"].values():
            if p["status"] != "active" or self.deur_id not in p["deuren"] or not p.get("pin"):
                continue
            if p.get("schema") and reden_geweigerd({"schema": p["schema"]}, nu):
                continue
            uit[p["id"]] = {"naam": p["naam"][:63], "pin": p["pin"]}
        return uit

    # ------------------------------------------------------------------ sync

    async def async_sync(self) -> dict:
        """Brengt het toestel in lijn. Geeft een verslag terug: toegevoegd, gewijzigd, gewist, fouten."""
        if not self.aan:
            return {"aan": False}
        async with self._lock:
            verslag = {"toegevoegd": [], "gewijzigd": [], "gewist": [], "fouten": []}
            try:
                lijst = await self.gebruikers()
            except AkuvoxFout as e:
                self.status.update(ok=False, fout=str(e), laatst=dt_util.utcnow().isoformat())
                return {"fout": str(e)}
            staat = self._staat
            eigen = staat["gebruikers"]
            per_userid = {str(x.get("UserID")): x for x in lijst}
            relay_veld = next((k for x in lijst for k in x if k.replace("-", "").lower() == "schedulerelay"), "ScheduleRelay")
            self.status["velden"] = sorted({k for x in lijst for k in x})[:40]
            gewenst = self._gewenst(dt_util.now())
            gewijzigd_register = False

            # weg: van ons, maar niet meer gewenst. De UserID blijft aan de persoon gekoppeld (zelfde nummer later en
            # in de deurlog); enkel als de persoon niet meer bestaat, verdwijnt de koppeling.
            for pid, info in list(eigen.items()):
                if pid in gewenst:
                    continue
                dev = per_userid.get(info["userid"])
                try:
                    if dev and int(info["userid"]) >= USER_ID_START:
                        await self._schrijf("del", [{"ID": str(dev.get("ID"))}])
                        verslag["gewist"].append(info.get("naam", pid))
                    info.pop("hash", None)
                    if pid not in self.reg.data["personen"]:
                        del eigen[pid]
                    gewijzigd_register = True
                except AkuvoxFout as e:
                    verslag["fouten"].append(f"wissen {info.get('naam', pid)}: {e}")

            # bij of aanpassen
            for pid, w in gewenst.items():
                info = eigen.get(pid)
                h = _pinhash(w["naam"], w["pin"])
                # zoals de gebruikers die wel werken op de S532: schema Always op relais 1, met activatierecht en groep
                velden = {"Name": w["naam"], "PrivatePIN": w["pin"], relay_veld: f"{SCHEDULE_ALTIJD}-1;",
                          "RelayScheduleActivationPermission": "1", "Group": "Default", "WebRelay": "0",
                          "LiftFloorNum": "0"}
                try:
                    if info and info["userid"] in per_userid:
                        if info.get("hash") != h:
                            dev = per_userid[info["userid"]]
                            await self._schrijf("set", [{"ID": str(dev.get("ID")), "UserID": info["userid"], **velden}])
                            info["hash"], info["naam"] = h, w["naam"]
                            verslag["gewijzigd"].append(w["naam"])
                            gewijzigd_register = True
                        continue
                    userid = info["userid"] if info else self._nieuw_userid(per_userid)
                    await self._schrijf("add", [{"UserID": userid, **velden}])
                    eigen[pid] = {"userid": userid, "naam": w["naam"], "hash": h}
                    verslag["toegevoegd"].append(w["naam"])
                    gewijzigd_register = True
                except AkuvoxFout as e:
                    verslag["fouten"].append(f"{w['naam']}: {e}")

            van_ons = {i["userid"] for i in eigen.values()}
            self.status.update(ok=not verslag["fouten"], fout="; ".join(verslag["fouten"])[:300] or None,
                               laatst=dt_util.utcnow().isoformat(), eigen=len(gewenst), toestel=len(lijst),
                               vreemd_boven_start=sum(1 for u in per_userid if u.isdigit() and int(u) >= USER_ID_START
                                                      and u not in van_ons))
            if gewijzigd_register:
                self.reg._bewaar()  # noqa: SLF001  enkel opslag, de tabletlijst verandert hier niet
            if any(verslag[k] for k in ("toegevoegd", "gewijzigd", "gewist")):
                _LOGGER.info("Akuvox: %s", {k: v for k, v in verslag.items() if v})
            return verslag

    def _nieuw_userid(self, per_userid: dict) -> str:
        staat = self._staat
        n = max(int(staat.get("volgende") or USER_ID_START), USER_ID_START)
        while str(n) in per_userid or any(i["userid"] == str(n) for i in staat["gebruikers"].values()):
            n += 1
        staat["volgende"] = n + 1
        return str(n)

    # ------------------------------------------------------------------ openen

    async def async_open(self, seconden: int = 5, relais: int = 1) -> None:
        """Relais van de S532 aansturen (deur openen). Valt na `seconden` vanzelf af (mode 0)."""
        if not self.aan:
            raise AkuvoxFout("Akuvox staat uit")
        await self._vraag("POST", "/api/relay/trig", {"target": "relay", "action": "trig",
                                                       "data": {"num": int(relais), "mode": 0, "level": 0,
                                                                "delay": max(1, min(60, int(seconden)))}})

    # ------------------------------------------------------------------ deurlog

    async def async_deurlog(self) -> list[dict]:
        """Nieuwe regels van het toestel als archiefrijen (ook van gebruikers die niet van ons zijn)."""
        if not self.aan:
            return []
        j = await self._vraag("GET", "/api/doorlog/get?page=1")
        items = [x for x in ((j.get("data") or {}).get("item") or []) if isinstance(x, dict)]
        staat = self._staat
        laatste = str(staat.get("laatste_log") or "")
        deur = self.reg.data["deuren"].get(self.deur_id, {})
        per_userid = {i["userid"]: pid for pid, i in staat["gebruikers"].items()}
        rijen = []
        for x in items:
            sleutel = str(x.get("ID") or "")
            tijd = f"{x.get('Date', '')} {x.get('Time', '')}".strip()
            uid = f"akuvox-{sleutel or tijd}"
            if laatste and sleutel.isdigit() and laatste.isdigit() and int(sleutel) <= int(laatste):
                continue
            t = dt_util.parse_datetime(tijd.replace(" ", "T")) if tijd else None
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt_util.get_default_time_zone())
            soort = str(x.get("Type") or "").strip().lower()
            gelukt = str(x.get("Status") or "").lower().startswith(("succ", "ok", "open"))
            pid = per_userid.get(str(x.get("UserID") or ""))
            naam = self.reg.data["personen"].get(pid, {}).get("naam") if pid else None
            rijen.append({"uid": uid, "door_id": self.deur_id, "door": deur.get("naam", "Akuvox"), "ts": int(t.timestamp()),
                          "name": naam or str(x.get("Name") or "Onbekend")[:60], "card": "",
                          "method": {"pin": "code", "kaart": "badge", "gezicht": "gezicht", "ha": "op afstand",
                                     "knop": "exitknop", "oproep": "oproep"}.get(METHODE.get(soort, ""), soort or "onbekend"),
                          "opened": gelukt, "persoon_id": pid or "", "tekst": f"Akuvox {x.get('Type', '')}".strip()})
        hoogste = max((int(str(x.get("ID"))) for x in items if str(x.get("ID") or "").isdigit()), default=None)
        if hoogste is not None:
            staat["laatste_log"] = str(hoogste)
            self.reg._bewaar()  # noqa: SLF001
        return rijen

    async def async_test(self) -> dict:
        """Enkel lezen: wat ziet HA op het toestel (voor de instellingen en de diagnose)."""
        lijst = await self.gebruikers()
        eigen = {i["userid"] for i in self._staat["gebruikers"].values()}
        try:
            log = await self._vraag("GET", "/api/doorlog/get?page=1")
            ruw_log = [{k: v for k, v in x.items() if k not in ("PicUrl", "Image")} for x in ((log.get("data") or {}).get("item") or [])[:6]]
        except AkuvoxFout as e:
            ruw_log = [str(e)]
        geheim = ("PrivatePIN", "CardCode", "BLE_AuthCode", "PhoneNum", "QrCodeUrl")
        try:
            schemas = (await self._vraag("GET", "/api/schedule/get?page=1")).get("data")
        except Exception as e:  # noqa: BLE001  enkel diagnose
            schemas = str(e)[:200]
        return {"eigen_gebruikers": [{k: v for k, v in x.items() if k not in geheim} for x in lijst if str(x.get("UserID")) in eigen],
                "andere_gebruikers": [{k: v for k, v in x.items() if k in ("Source", "ScheduleRelay", "RelayScheduleActivationPermission", "Group", "WebRelay")}
                                      for x in lijst if str(x.get("UserID")) not in eigen][:4],
                "schemas": schemas,
                "deurlog": ruw_log,
                "gebruikers": len(lijst), "van_ons": sum(1 for x in lijst if str(x.get("UserID")) in eigen),
                "bronnen": sorted({str(x.get("Source", "?")) for x in lijst}),
                "velden": sorted({k for x in lijst for k in x}),
                "boven_700000_niet_van_ons": [str(x.get("UserID")) for x in lijst if str(x.get("UserID", "")).isdigit()
                                               and int(str(x.get("UserID"))) >= USER_ID_START and str(x.get("UserID")) not in eigen]}
