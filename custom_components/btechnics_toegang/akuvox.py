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
            # v0.15.0: uren en datums gaan als schema mee naar het toestel; de persoon blijft erop staan (ook buiten de uren)
            uit[p["id"]] = {"naam": p["naam"][:63], "pin": p["pin"], "schemas": self.schema_items(p), "relay": f"{SCHEDULE_ALTIJD}-1;",
                            "_p": p}
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
        delen = sorted(x for x in sr.split(";") if x)
        verwacht = sorted(x for x in w.get("relay", f"{SCHEDULE_ALTIJD}-1;").split(";") if x)
        if delen != verwacht:
            fout.append(f"schema/relais {sr or 'leeg'} in plaats van {w.get('relay', '')}")
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

    # ------------------------------------------------------------------ schema's (v0.15.0)
    # Formaat volgens de webinterface van de S532 zelf (app.js): Type 0 = datumbereik + dagen + uren, 1 = wekelijks,
    # 2 = dagelijks; Date "JJJJMMDD-JJJJMMDD" ("00000000" = geen grens), Week = cijfers 0 (zondag) tot 6 (zaterdag),
    # Daily "HH:MM-HH:MM". Een gebruiker verwijst ernaar met ScheduleRelay "<DisplayID>-1;".
    SCHEMA_PREFIX = "BTX "

    @staticmethod
    def _datum(iso: str | None) -> str:
        if not iso:
            return "00000000"
        t = dt_util.parse_datetime(iso)
        if t is None:
            return "00000000"
        return dt_util.as_local(t).strftime("%Y%m%d")

    @classmethod
    def schema_items(cls, p: dict) -> list[dict]:
        """Gewenste S532 schema's voor een persoon, zonder naam/ID. Leeg = altijd (1001)."""
        blokken = p.get("schema") or []
        datums = bool(p.get("valid_from") or p.get("valid_until"))
        if not blokken and not datums:
            return []
        stukken: list[tuple[str, str, str]] = []   # (week, van, tot)
        for b in blokken or [{"dagen": [1, 2, 3, 4, 5, 6, 7], "van": "00:00", "tot": "23:59"}]:
            dagen = sorted({d % 7 for d in b["dagen"]})
            if b["tot"] > b["van"]:
                stukken.append(("".join(map(str, dagen)), b["van"], b["tot"]))
            else:
                # over middernacht (of 24 uur): de startdag tot 23:59, de volgende dag vanaf 00:00
                stukken.append(("".join(map(str, dagen)), b["van"], "23:59"))
                if b["tot"] != "00:00":
                    stukken.append(("".join(map(str, sorted({(d + 1) % 7 for d in dagen}))), "00:00", b["tot"]))
        uit = []
        for week, van, tot in stukken:
            it = {"Type": "0" if datums else "1", "Week": week, "Daily": f"{van}-{tot}"}
            if datums:
                it["Date"] = f"{cls._datum(p.get('valid_from'))}-{cls._datum(p.get('valid_until'))}"
            uit.append(it)
        return uit

    @staticmethod
    def schema_gelijk(dev: dict, w: dict) -> bool:
        def kort(t: str) -> str:   # "08:00:00-17:00:00" en "08:00-17:00" zijn gelijk
            return "-".join(x[:5] for x in str(t or "").split("-"))
        return (str(dev.get("Type")) == w["Type"] and "".join(sorted(str(dev.get("Week") or ""))) == "".join(sorted(w["Week"]))
                and kort(dev.get("Daily")) == kort(w["Daily"]) and (w["Type"] != "0" or str(dev.get("Date") or "") == w["Date"]))

    async def _schemas_zetten(self, gewenst: dict[str, dict], verslag: dict) -> tuple[dict[str, str], list]:
        """Zet de eigen schema's (naam 'BTX <userid>-<n>') op het toestel en leest terug. Geeft per persoon de ScheduleRelay
        tekst, enkel voor wie alle schema's echt goed op het toestel staan, en de lijst eigen schema-ID's die weg mogen."""
        staat = self._staat
        eigen: dict = staat.setdefault("schemas", {})   # sleutel "pid:n" -> {"id", "naam"}
        if not gewenst and not eigen:
            return {}, []
        try:
            op_toestel = {str(x.get("ID")): x for x in await self.schemas()}
        except AkuvoxFout as e:
            verslag["fouten"].append(f"schema's lezen: {e}")
            return {}, []
        nodig: dict[str, dict] = {}
        for pid, w in gewenst.items():
            for n, it in enumerate(w["schemas"]):
                nodig[f"{pid}:{n}"] = {**it, "_pid": pid}
        # eigen koppelingen die niet meer naar ons schema wijzen loslaten (iemand anders gaf dat ID een andere naam)
        for k, info in list(eigen.items()):
            dev = op_toestel.get(str(info["id"]))
            if dev is not None and (str(dev.get("Name")) != info["naam"] or str(dev.get("Source", "Local")) != "Local"):
                del eigen[k]
        geschreven = False
        for k, it in nodig.items():
            pid = it["_pid"]
            item = {x: it[x] for x in ("Type", "Week", "Daily", "Date") if x in it}
            info = eigen.get(k)
            dev = op_toestel.get(str(info["id"])) if info else None
            try:
                if dev is not None:
                    if not self.schema_gelijk(dev, item):
                        await self._vraag("POST", "/api/schedule/set", {"target": "schedule", "action": "set", "data": {"item": [
                            {"ID": str(dev.get("ID")), "DisplayID": str(dev.get("DisplayID")), "Name": info["naam"], **item}]}})
                        geschreven = True
                    continue
                naam = f"{self.SCHEMA_PREFIX}{self._staat['gebruikers'].get(pid, {}).get('userid') or pid[-6:]}-{k.split(':')[1]}"
                j = await self._vraag("POST", "/api/schedule/add", {"target": "schedule", "action": "add", "data": {"item": [{"Name": naam, **item}]}})
                nieuw = ((j.get("data") or {}).get("item") or [{}])[0]
                if str(nieuw.get("Ret", "0")) != "0" or not nieuw.get("ID"):
                    verslag["fouten"].append(f"schema {naam}: toestel weigerde ({str(nieuw)[:120]})")
                    continue
                eigen[k] = {"id": str(nieuw["ID"]), "naam": naam}
                geschreven = True
            except AkuvoxFout as e:
                verslag["fouten"].append(f"schema voor {gewenst[pid]['naam']}: {e}")
        if geschreven:
            try:
                op_toestel = {str(x.get("ID")): x for x in await self.schemas()}
            except AkuvoxFout as e:
                verslag["fouten"].append(f"schema's terug lezen: {e}")
                return {}, []
        # terug lezen: per persoon enkel een ScheduleRelay als ALLE schema's kloppen
        relay: dict[str, str] = {}
        for pid, w in gewenst.items():
            if not w["schemas"]:
                continue
            ids = []
            for n, it in enumerate(w["schemas"]):
                info = eigen.get(f"{pid}:{n}")
                dev = op_toestel.get(str(info["id"])) if info else None
                if dev is None or str(dev.get("Name")) != info["naam"] or not self.schema_gelijk(dev, it) or not dev.get("DisplayID"):
                    verslag["fouten"].append(f"schema {n + 1} van {w['naam']} staat niet goed op het toestel")
                    ids = None
                    break
                ids.append(str(dev["DisplayID"]))
            if ids:
                relay[pid] = "".join(f"{i}-1;" for i in ids)
        weg = [str(info["id"]) for k, info in eigen.items() if k not in nodig]
        return relay, weg

    async def _schemas_opruimen(self, weg: list[str], verslag: dict) -> None:
        """Eigen schema's die niemand meer nodig heeft wissen. Enkel op ID uit onze eigen administratie, met naamcontrole."""
        if not weg:
            return
        eigen: dict = self._staat.setdefault("schemas", {})
        try:
            op_toestel = {str(x.get("ID")): x for x in await self.schemas()}
        except AkuvoxFout as e:
            verslag["fouten"].append(f"schema's lezen: {e}")
            return
        for k, info in list(eigen.items()):
            if str(info["id"]) not in weg:
                continue
            dev = op_toestel.get(str(info["id"]))
            try:
                if dev is not None and str(dev.get("Name")) == info["naam"] and str(dev.get("Name")).startswith(self.SCHEMA_PREFIX):
                    await self._vraag("POST", "/api/schedule/del", {"target": "schedule", "action": "del", "data": {"item": [{"ID": str(info["id"])}]}})
                del eigen[k]
            except AkuvoxFout as e:
                verslag["fouten"].append(f"schema {info['naam']} wissen: {e}")

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
            relay, schema_weg = await self._schemas_zetten({k: v for k, v in gewenst.items() if v["schemas"]}, verslag)
            for pid, w in list(gewenst.items()):
                if not w["schemas"]:
                    continue
                if pid in relay:
                    w["relay"] = relay[pid]
                elif reden_geweigerd({"schema": w["_p"].get("schema") or []}, dt_util.now()):
                    # schema's niet goed op het toestel: veilig terugvallen op de oude regel (enkel binnen de uren, door HA)
                    del gewenst[pid]

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
                          "Schedule-Relay": w["relay"], "ScheduleRelay": w["relay"],
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

            # pas nu (gebruikers verwijzen er niet meer naar) eigen schema's wissen die niemand nodig heeft
            await self._schemas_opruimen(schema_weg, verslag)
            van_ons = {str(i["userid"]) for i in eigen.values()}
            fout = "; ".join(verslag["fouten"])[:400] or None
            self.status.update(ok=not verslag["fouten"], fout=fout, laatst=dt_util.utcnow().isoformat(), eigen=in_orde,
                               schemas=len(self._staat.get("schemas", {})),
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
            p = self.reg.data["personen"].get(pid, {}) if pid else {}

            def _dag(iso):
                d = dt_util.parse_datetime(iso) if iso else None
                return dt_util.as_local(d).date().isoformat() if d else None
            buiten = bool(p) and bool(reden_geweigerd({"schema": p.get("schema") or [], "van": _dag(p.get("valid_from")),
                                                       "tot": _dag(p.get("valid_until"))}, dt_util.as_local(t)))
            if pid and not gelukt and laatste and buiten:
                pass   # buiten de uren of datums: het toestel weigert terecht (schema op de S532)
            elif pid and not gelukt and laatste:
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
                          "opened": gelukt, "persoon_id": pid or "",
                          "tekst": (f"Akuvox {x.get('Type', '')}".strip() + (" (buiten de uren)" if buiten and not gelukt else ""))})
        hoogste = max((int(str(x.get("ID"))) for x in items if str(x.get("ID") or "").isdigit()), default=None)
        if hoogste is not None:
            staat["laatste_log"] = str(hoogste)
            self.reg._bewaar()  # noqa: SLF001
        return rijen

    async def schemas(self) -> list[dict]:
        """Alle schema's op het toestel (pagina per pagina)."""
        uit: dict[str, dict] = {}
        for pagina in range(1, 21):
            j = await self._vraag("GET", f"/api/schedule/get?page={pagina}")
            items = [x for x in ((j.get("data") or {}).get("item") or []) if isinstance(x, dict)]
            nieuw = [x for x in items if str(x.get("ID") or x.get("DisplayID")) not in uit]
            for x in nieuw:
                uit[str(x.get("ID") or x.get("DisplayID"))] = x
            if not nieuw:
                break
        return list(uit.values())

    async def async_schema_proef(self, items: list[dict] | None = None, gebruiker: bool = True) -> dict:
        """v0.14.3 proef: welk schemaformaat aanvaardt dit toestel? Zet tijdelijke schema's met naam 'BTX proef',
        leest ze terug, koppelt er een tijdelijke eigen gebruiker 799990 aan, leest die terug en ruimt alles weer op.
        Raakt niets anders aan: wissen enkel op ID van items met die naam."""
        PROEF = "BTX proef"
        dagen = {"Mon": "1", "Tue": "1", "Wed": "1", "Fri": "1", "Sat": "0", "Sun": "0"}
        varianten = {
            "A": {"Name": PROEF + " A", "Type": "0", **dagen, "Thur": "1", "DateStart": "20261102", "DateEnd": "20261231",
                  "TimeStart": "08:00", "TimeEnd": "17:00"},
            "B": {"Name": PROEF + " B", "Type": "1", **dagen, "Thu": "1", "Thur": "1", "DateStart": "2026-11-02", "DateEnd": "2026-12-31",
                  "TimeStart": "08:00:00", "TimeEnd": "17:00:00"},
        }
        if items:
            # v0.14.4: varianten via de dienst; naam altijd 'BTX proef n' zodat opruimen enkel die raakt
            varianten = {str(i + 1): {**{k: str(v) for k, v in it.items() if k not in ("ID", "DisplayID", "Name")}, "Name": f"{PROEF} {i + 1}"}
                         for i, it in enumerate(items[:6])}
        verslag: dict[str, Any] = {"voor": [], "varianten": {}, "gebruiker": None, "na": []}

        async def opruimen() -> list:
            weg = [x for x in await self.schemas() if str(x.get("Name", "")).startswith(PROEF)]
            for x in weg:
                try:
                    await self._vraag("POST", "/api/schedule/del", {"target": "schedule", "action": "del", "data": {"item": [{"ID": str(x.get("ID"))}]}})
                except AkuvoxFout as e:
                    verslag.setdefault("opruimfouten", []).append(str(e)[:200])
            return weg

        verslag["voor"] = await self.schemas()
        await opruimen()
        try:
            for naam, item in varianten.items():
                r: dict[str, Any] = {"verstuurd": item}
                try:
                    r["antwoord"] = await self._vraag("POST", "/api/schedule/add", {"target": "schedule", "action": "add", "data": {"item": [item]}})
                except AkuvoxFout as e:
                    r["fout"] = str(e)[:300]
                r["teruggelezen"] = [x for x in await self.schemas() if x.get("Name") == item["Name"]]
                verslag["varianten"][naam] = r
            # tijdelijke eigen gebruiker aan het eerste gelukte schema koppelen (code willekeurig, meteen weer weg)
            gelukt = next((v["teruggelezen"][0] for v in verslag["varianten"].values() if v["teruggelezen"]), None)
            if gelukt and gebruiker:
                sid = str(gelukt.get("DisplayID") or gelukt.get("ID"))
                bestaand = [u for u in await self.gebruikers() if str(u.get("UserID")) == "799990"]
                if not bestaand:
                    import secrets
                    gb = {"Name": PROEF, "UserID": "799990", "PrivatePIN": str(secrets.randbelow(9 * 10 ** 7) + 10 ** 7),
                          "Schedule-Relay": f"{sid}-1;", "ScheduleRelay": f"{sid}-1;", "RelayScheduleActivationPermission": "1",
                          "Group": "Default", "WebRelay": "0", "LiftFloorNum": "0"}
                    g: dict[str, Any] = {"schema": sid}
                    try:
                        g["antwoord"] = await self._vraag("POST", "/api/user/add", {"target": "user", "action": "add", "data": {"item": [gb]}})
                    except AkuvoxFout as e:
                        g["fout"] = str(e)[:300]
                    terug = [u for u in await self.gebruikers() if str(u.get("UserID")) == "799990"]
                    g["teruggelezen"] = [{k: v for k, v in u.items() if k not in ("PrivatePIN", "CardCode", "BLE_AuthCode", "PhoneNum", "QrCodeUrl")} for u in terug]
                    for u in terug:
                        if u.get("Name") == PROEF:
                            await self._vraag("POST", "/api/user/del", {"target": "user", "action": "del", "data": {"item": [{"ID": str(u.get("ID"))}]}})
                    g["weg"] = not [u for u in await self.gebruikers() if str(u.get("UserID")) == "799990"]
                    verslag["gebruiker"] = g
                else:
                    verslag["gebruiker"] = "UserID 799990 bestaat al, niet aangeraakt"
        finally:
            verslag["opgeruimd"] = len(await opruimen())
            verslag["na"] = await self.schemas()
        return verslag

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
            schemas = await self.schemas()
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
