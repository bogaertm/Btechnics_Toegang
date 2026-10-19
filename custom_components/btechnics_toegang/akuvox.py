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
        self.reg.extern_pins[f"akuvox{nr}"] = set(self._staat.get("vreemde_pins") or [])

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
        """Alle gebruikers, pagina per pagina tot er niets nieuws meer komt (ook als het toestel 'num' anders invult
        of de paginaparameter negeert)."""
        uit: dict[str, dict] = {}
        for pagina in range(1, 51):
            j = await self._vraag("GET", f"/api/user/get?page={pagina}")
            items = [x for x in ((j.get("data") or {}).get("item") or []) if isinstance(x, dict)]
            nieuw = 0
            for x in items:
                k = str(x.get("ID") or x.get("UserID") or len(uit))
                if k not in uit:
                    uit[k] = x
                    nieuw += 1
            if not items or not nieuw:
                break
        return list(uit.values())

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

    @staticmethod
    def afwijkingen(dev: dict, w: dict) -> list[str]:
        """Wat er op het toestel anders staat dan nodig om met relais 1 altijd te mogen openen."""
        fout = []
        if str(dev.get("Name") or "") != w["naam"]:
            fout.append("naam")
        pin = dev.get("PrivatePIN")
        if pin not in (None, "") and "*" not in str(pin) and str(pin) != w["pin"]:
            fout.append("code")
        sr = str(dev.get("ScheduleRelay") or dev.get("Schedule-Relay") or "")
        delen = [x for x in sr.split(";") if x]
        if not any(x.startswith(f"{SCHEDULE_ALTIJD}-") and "1" in x.split("-", 1)[1] for x in delen):
            fout.append(f"schema/relais leeg of fout ({sr or 'leeg'})")
        perm = dev.get("RelayScheduleActivationPermission")
        if perm is not None and "1" not in str(perm):
            fout.append(f"relaisrecht ({perm or 'leeg'})")
        return fout

    @staticmethod
    def _is_van_ons(dev: dict, info: dict) -> bool:
        """Staat er op die UserID echt onze gebruiker? Zelfde toestel-ID of zelfde naam. Anders nooit aanraken."""
        if info.get("dev_id") and str(dev.get("ID")) == str(info["dev_id"]):
            return True
        return str(dev.get("Name") or "") == str(info.get("naam") or "")

    @staticmethod
    def _pin_h(pin: str) -> str:
        return hashlib.sha256(f"akuvox-pin\n{pin}".encode()).hexdigest()

    # ------------------------------------------------------------------ sync

    async def async_sync(self) -> dict:
        """Brengt het toestel in lijn en CONTROLEERT daarna op het toestel zelf of alles echt staat zoals nodig.
        Status ok is enkel waar als die controle slaagt. Verslag: toegevoegd, gewijzigd, gewist, fouten."""
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
            self.status["velden"] = sorted({k for x in lijst for k in x})[:40]
            gewenst = self._gewenst(dt_util.now())
            geschreven = False

            # koppelingen die niet (meer) naar onze gebruiker wijzen loslaten: nooit een vreemde wissen of overschrijven
            for pid, info in list(eigen.items()):
                dev = per_userid.get(str(info.get("userid")))
                if dev is not None and not self._is_van_ons(dev, info):
                    _LOGGER.warning("Akuvox %s: UserID %s is niet (meer) van %s, koppeling losgelaten", self.nr, info["userid"],
                                    info.get("naam", pid))
                    del eigen[pid]
                elif dev is not None:
                    info["dev_id"] = str(dev.get("ID"))

            van_ons = {str(i["userid"]) for i in eigen.values()}
            vreemd = [x for x in lijst if str(x.get("UserID")) not in van_ons]
            vreemde_pins = {self._pin_h(str(x["PrivatePIN"])) for x in vreemd
                            if x.get("PrivatePIN") not in (None, "") and "*" not in str(x.get("PrivatePIN"))}
            staat["vreemde_pins"] = sorted(vreemde_pins)
            self.reg.extern_pins[f"akuvox{self.nr}"] = vreemde_pins

            # weg: van ons, maar niet meer gewenst
            for pid, info in list(eigen.items()):
                if pid in gewenst:
                    continue
                dev = per_userid.get(str(info["userid"]))
                try:
                    if dev is not None and int(info["userid"]) >= USER_ID_START:
                        await self._schrijf("del", [{"ID": str(dev.get("ID"))}])
                        geschreven = True
                        verslag["gewist"].append(info.get("naam", pid))
                    if pid not in self.reg.data["personen"]:
                        del eigen[pid]
                except AkuvoxFout as e:
                    verslag["fouten"].append(f"wissen {info.get('naam', pid)}: {e}")

            # bij of aanpassen, enkel waar het toestel afwijkt
            for pid, w in gewenst.items():
                info = eigen.get(pid)
                velden = {"Name": w["naam"], "PrivatePIN": w["pin"],
                          # het toestel toont ScheduleRelay maar aanvaardt bij add/set enkel Schedule-Relay
                          "Schedule-Relay": f"{SCHEDULE_ALTIJD}-1;", "ScheduleRelay": f"{SCHEDULE_ALTIJD}-1;",
                          "RelayScheduleActivationPermission": "1", "Group": "Default", "WebRelay": "0", "LiftFloorNum": "0"}
                dev = per_userid.get(str(info["userid"])) if info else None
                try:
                    if dev is not None:
                        if self.afwijkingen(dev, w):
                            await self._schrijf("set", [{"ID": str(dev.get("ID")), "UserID": str(info["userid"]), **velden}])
                            info["naam"] = w["naam"]
                            geschreven = True
                            verslag["gewijzigd"].append(w["naam"])
                        continue
                    if self._pin_h(w["pin"]) in vreemde_pins:
                        verslag["fouten"].append(f"{w['naam']}: deze code staat al bij een andere gebruiker op het toestel (SmartPlus?)")
                        continue
                    userid = self._nieuw_userid(per_userid)
                    await self._schrijf("add", [{"UserID": userid, **velden}])
                    eigen[pid] = {"userid": userid, "naam": w["naam"]}
                    geschreven = True
                    verslag["toegevoegd"].append(w["naam"])
                except AkuvoxFout as e:
                    verslag["fouten"].append(f"{w['naam']}: {e}")

            # controle op het toestel zelf
            if geschreven:
                try:
                    lijst = await self.gebruikers()
                    per_userid = {str(x.get("UserID")): x for x in lijst}
                except AkuvoxFout as e:
                    verslag["fouten"].append(f"controle na schrijven: {e}")
            in_orde = 0
            for pid, w in gewenst.items():
                info = eigen.get(pid)
                dev = per_userid.get(str(info["userid"])) if info else None
                if dev is None:
                    if info:
                        verslag["fouten"].append(f"{w['naam']} staat niet op het toestel na het schrijven")
                    continue
                info["dev_id"] = str(dev.get("ID"))
                afw = self.afwijkingen(dev, w)
                if afw:
                    verslag["fouten"].append(f"toestel nam {w['naam']} niet goed over: {', '.join(afw)}")
                else:
                    in_orde += 1
            for pid, info in eigen.items():
                dev = per_userid.get(str(info["userid"]))
                if pid not in gewenst and dev is not None and self._is_van_ons(dev, info):
                    verslag["fouten"].append(f"{info.get('naam', pid)} staat nog op het toestel maar heeft geen toegang meer")

            van_ons = {str(i["userid"]) for i in eigen.values()}
            fout = "; ".join(verslag["fouten"])[:400] or None
            self.status.update(ok=not verslag["fouten"], fout=fout, laatst=dt_util.utcnow().isoformat(), eigen=in_orde,
                               gewenst=len(gewenst), toestel=len(lijst),
                               vreemd_boven_start=sum(1 for u in per_userid if u.isdigit() and int(u) >= USER_ID_START
                                                      and u not in van_ons))
            self.reg._bewaar()  # noqa: SLF001  enkel opslag, de tabletlijst verandert hier niet
            if fout and fout != staat.get("laatste_fout"):
                from homeassistant.components import persistent_notification
                persistent_notification.async_create(
                    self.hass, f"De Akuvox ({self.ip}) staat niet zoals HA het wil: {fout}",
                    title="Btechnics Toegang: Akuvox klopt niet", notification_id=f"btechnics_toegang_akuvox{self.nr}")
                _LOGGER.error("Akuvox %s: %s", self.nr, fout)
            staat["laatste_fout"] = fout
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
        try:
            j = await self._vraag("GET", "/api/doorlog/get?page=1")
        except AkuvoxFout as e:
            self.status.update(deurlog_ok=False, deurlog_fout=str(e))
            raise
        self.status.update(deurlog_ok=True, deurlog_fout=None, deurlog_laatst=dt_util.utcnow().isoformat())
        items = [x for x in ((j.get("data") or {}).get("item") or []) if isinstance(x, dict)]
        staat = self._staat
        laatste = str(staat.get("laatste_log") or "")
        hoogste_nu = max((int(str(x.get("ID"))) for x in items if str(x.get("ID") or "").isdigit()), default=None)
        if laatste.isdigit() and hoogste_nu is not None and hoogste_nu < int(laatste):
            _LOGGER.warning("Akuvox %s: deurlog begon opnieuw (hoogste ID %s < %s), teller hersteld", self.nr, hoogste_nu, laatste)
            laatste = ""
        deur = self.reg.data["deuren"].get(self.deur_id, {})
        per_userid = {i["userid"]: pid for pid, i in staat["gebruikers"].items()}
        rijen = []
        for x in items:
            sleutel = str(x.get("ID") or "")
            tijd = f"{x.get('Date', '')} {x.get('Time', '')}".strip()
            uid = f"akuvox{'' if self.nr == 1 else self.nr}-{sleutel or tijd}"
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
            if pid and not gelukt and laatste:
                # het toestel herkende onze gebruiker (juiste code) maar weigerde: rechten op het toestel kloppen niet
                from homeassistant.components import persistent_notification
                melding = (f"De Akuvox weigerde {naam or pid} ({x.get('Type', '')}) om {tijd}, hoewel het toestel de gebruiker herkende. "
                           "De toegangsrechten op het toestel kloppen niet; HA zet ze opnieuw.")
                persistent_notification.async_create(self.hass, melding, title="Btechnics Toegang: Akuvox weigert eigen gebruiker",
                                                     notification_id=f"btechnics_toegang_akuvox{self.nr}_weigert")
                _LOGGER.error("Akuvox %s: %s", self.nr, melding)
                self.status.update(ok=False, fout=f"toestel weigerde {naam or pid} om {tijd}")
                for info in staat["gebruikers"].values():
                    if str(info.get("userid")) == str(x.get("UserID")):
                        info["dev_id"] = None   # bij de volgende sync opnieuw volledig nakijken en zetten
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
