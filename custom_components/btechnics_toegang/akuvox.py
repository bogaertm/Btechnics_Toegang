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
import re
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
SCHEDULE_NOOIT = "1002"    # standaardschema "nooit" op Akuvox
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
        except ValueError as e:
            raise AkuvoxFout(f"onleesbaar antwoord op {pad}") from e
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
            items = self.schema_items(p)
            datums = bool(p.get("valid_from") or p.get("valid_until"))
            relay = f"{SCHEDULE_NOOIT}-1;" if datums and not items else f"{SCHEDULE_ALTIJD}-1;"
            uit[p["id"]] = {"naam": p["naam"][:63], "pin": p["pin"], "schemas": items, "relay": relay, "_p": p}
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

    @classmethod
    def _buiten(cls, p: dict, t: datetime) -> bool:
        """Volgens HA buiten de uren of datums van deze persoon op tijdstip t."""
        def dag(iso):
            d = cls._lokaal(iso)
            return d.date().isoformat() if d else None
        vu = cls._lokaal(p.get("valid_until"))
        if vu is not None and dt_util.as_local(t) >= vu:
            return True
        return bool(reden_geweigerd({"schema": p.get("schema") or [], "van": dag(p.get("valid_from")), "tot": dag(p.get("valid_until"))},
                                    dt_util.as_local(t)))

    @staticmethod
    def _lokaal(iso: str | None):
        t = dt_util.parse_datetime(iso) if iso else None
        return dt_util.as_local(t) if t else None

    @classmethod
    def schema_items(cls, p: dict) -> list[dict]:
        """Gewenste S532 schema's voor een persoon, zonder naam/ID. Leeg = altijd (1001).
        Datums: het toestel kent enkel dagen; eindigt valid_until niet op 23:59, dan loopt het gewone bereik tot de dag
        ervoor en krijgt de laatste dag eigen stukken die stoppen op het einduur (ook bij uitval van HA exact)."""
        blokken = p.get("schema") or []
        datums = bool(p.get("valid_from") or p.get("valid_until"))
        if not blokken and not datums:
            return []
        stukken = cls._stukken(blokken)
        if not datums:
            return [{"Type": "1", "Week": w, "Daily": f"{v}-{t}"} for w, v, t in stukken]
        van_d = cls._lokaal(p.get("valid_from"))
        tot_t = cls._lokaal(p.get("valid_until"))
        begin = van_d.strftime("%Y%m%d") if van_d else "00000000"
        uit = []
        if tot_t is None or tot_t.strftime("%H:%M") >= "23:59":
            einde = tot_t.strftime("%Y%m%d") if tot_t else "00000000"
            return [{"Type": "0", "Week": w, "Daily": f"{v}-{t}", "Date": f"{begin}-{einde}"} for w, v, t in stukken]
        from datetime import timedelta
        laatste = tot_t.date()
        dag_ervoor = laatste - timedelta(days=1)
        if van_d is None or van_d.date() <= dag_ervoor:
            uit += [{"Type": "0", "Week": w, "Daily": f"{v}-{t}", "Date": f"{begin}-{dag_ervoor:%Y%m%d}"} for w, v, t in stukken]
        if van_d is None or van_d.date() <= laatste:
            wd, eind = str(laatste.isoweekday() % 7), tot_t.strftime("%H:%M")
            for w, v, t in stukken:
                if wd in w and v < eind:
                    uit.append({"Type": "0", "Week": wd, "Daily": f"{v}-{min(t, eind)}", "Date": f"{laatste:%Y%m%d}-{laatste:%Y%m%d}"})
        return uit   # leeg = niets meer over (einduur 00:00 of voor elk blok): _gewenst zet dan Never (1002)

    @staticmethod
    def _stukken(blokken: list[dict]) -> list[tuple[str, str, str]]:
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
        return stukken

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
                info["DisplayID"] = str(dev["DisplayID"])
            if ids:
                relay[pid] = "".join(f"{i}-1;" for i in ids)
        weg = [str(info["id"]) for k, info in eigen.items() if k not in nodig]
        return relay, weg

    async def _schemas_opruimen(self, weg: list[str], gebruikers: list[dict] | None, verslag: dict) -> int:
        """Eigen schema's die niemand meer nodig heeft wissen. Enkel op ID uit onze eigen administratie (of een wees met
        onze naamvorm), met naamcontrole, en NOOIT zolang een gebruiker op het toestel er nog naar verwijst (anders kan
        een vrijgekomen DisplayID later bij iemand anders terechtkomen). Geeft het aantal gewiste schema's."""
        eigen: dict = self._staat.setdefault("schemas", {})
        if gebruikers is None:
            return 0   # gebruikers niet terug gelezen: niets wissen
        if not weg and not eigen:
            return 0   # geen eigen schema's in gebruik: toestel niet onnodig bevragen
        try:
            schemas = await self.schemas()
        except AkuvoxFout as e:
            verslag["fouten"].append(f"schema's lezen: {e}")
            return 0
        op_toestel = {str(x.get("ID")): x for x in schemas}
        in_gebruik = {deel.split("-", 1)[0] for u in gebruikers
                      for deel in str(u.get("ScheduleRelay") or u.get("Schedule-Relay") or "").split(";") if deel}
        bekend = {str(i["id"]) for i in eigen.values()}
        kandidaten = [(k, info) for k, info in eigen.items() if str(info["id"]) in weg]
        # wezen: schema's met onze naamvorm die niet (meer) in de administratie staan (bv. herstart vlak na add)
        kandidaten += [(None, {"id": str(x.get("ID")), "naam": str(x.get("Name"))}) for x in schemas
                       if str(x.get("ID")) not in bekend and str(x.get("Source", "Local")) == "Local"
                       and re.fullmatch(r"BTX [0-9a-z]+-\d+", str(x.get("Name") or ""))]
        gewist = 0
        for k, info in kandidaten:
            dev = op_toestel.get(str(info["id"]))
            if dev is not None and str(dev.get("DisplayID")) in in_gebruik:
                continue   # nog in gebruik: laten staan, volgende sync opnieuw
            try:
                if dev is not None and str(dev.get("Name")) == info["naam"] and str(dev.get("Name")).startswith(self.SCHEMA_PREFIX):
                    await self._vraag("POST", "/api/schedule/del", {"target": "schedule", "action": "del", "data": {"item": [{"ID": str(info["id"])}]}})
                    gewist += 1
                if k is not None:
                    del eigen[k]
            except AkuvoxFout as e:
                verslag["fouten"].append(f"schema {info['naam']} wissen: {e}")
        return gewist

    # ------------------------------------------------------------------ sync

    async def async_sync(self) -> dict:
        """Brengt het toestel in lijn en CONTROLEERT daarna op het toestel zelf of alles echt staat zoals nodig.
        Status ok is enkel waar als die controle slaagt. Verslag: toegevoegd, gewijzigd, gewist, fouten.
        Na het wissen van schema's volgt een tweede ronde: verschuift het toestel DisplayID's, dan zet die alles recht."""
        if not self.aan:
            return {"aan": False}
        async with self._lock:
            verslag = await self._sync_ronde()
            if verslag.pop("_opnieuw", False):
                tweede = await self._sync_ronde()
                tweede.pop("_opnieuw", None)
                for k in ("toegevoegd", "gewijzigd", "gewist"):
                    verslag[k] += [x for x in tweede.get(k, []) if x not in verslag[k]]
                verslag["fouten"] = tweede.get("fouten", [])
            return verslag

    async def _sync_ronde(self) -> dict:
        verslag: dict[str, Any] = {"toegevoegd": [], "gewijzigd": [], "gewist": [], "fouten": []}
        try:
            lijst = await self.gebruikers()
        except AkuvoxFout as e:
            self.status.update(ok=False, fout=str(e), laatst=dt_util.utcnow().isoformat())
            return {"fout": str(e), "fouten": [str(e)], "toegevoegd": [], "gewijzigd": [], "gewist": []}
        staat = self._staat
        eigen = staat["gebruikers"]
        per_userid = {str(x.get("UserID")): x for x in lijst}
        self.status["velden"] = sorted({k for x in lijst for k in x})[:40]
        gewenst = self._gewenst(dt_util.now())
        geschreven = False
        if any(str(u.get("UserID")) == self.PROEF_USERID and str(u.get("Name")) == self.PROEF_NAAM for u in lijst):
            # achtergebleven proefgebruiker (proef onderbroken): opruimen, hij kan een vrijgekomen DisplayID erven
            await self._proef_opruimen(verslag)
            verslag.pop("opruimfouten", None)

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

        # 1. weg: van ons, maar niet meer gewenst (EERST, voor er schema's bij komen)
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

        # 2. schema's zetten en terug lezen. Lukt dat voor iemand niet, dan blijft die persoon op het toestel zoals hij
        #    stond (nooit terug naar 'altijd') en wie er nog niet op staat, komt er niet bij.
        relay, schema_weg = await self._schemas_zetten({k: v for k, v in gewenst.items() if v["schemas"]}, verslag)
        onaangeroerd: set[str] = set()
        eigen_ids = {str(i.get("DisplayID")) for i in staat.get("schemas", {}).values() if i.get("DisplayID")}
        for pid, w in list(gewenst.items()):
            if not w["schemas"]:
                continue
            if pid in relay:
                w["relay"] = relay[pid]
                continue
            info = eigen.get(pid)
            dev = per_userid.get(str(info["userid"])) if info else None
            huidig = [x.split("-", 1)[0] for x in str((dev or {}).get("ScheduleRelay") or "").split(";") if x]
            if huidig and all(x in eigen_ids for x in huidig):
                # stond al op eigen schema's (die het toestel ook zonder HA bewaakt): niets aanraken tot het weer lukt
                onaangeroerd.add(pid)
                del gewenst[pid]
            else:
                # nog geen werkend schema: HA bewaakt de uren zelf, zoals voor v0.15 (binnen = altijd, buiten = nooit)
                w["relay"] = f"{SCHEDULE_NOOIT}-1;" if self._buiten(w["_p"], dt_util.now()) else f"{SCHEDULE_ALTIJD}-1;"

        # 3. bij of aanpassen, enkel waar het toestel afwijkt
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

        # 4. controle op het toestel zelf
        terug: list[dict] | None = lijst
        if geschreven:
            try:
                lijst = await self.gebruikers()
                terug = lijst
                per_userid = {str(x.get("UserID")): x for x in lijst}
            except AkuvoxFout as e:
                terug = None
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
            if pid not in gewenst and pid not in onaangeroerd and dev is not None and self._is_van_ons(dev, info):
                verslag["fouten"].append(f"{info.get('naam', pid)} staat nog op het toestel maar heeft geen toegang meer")

        # 5. pas nu eigen schema's wissen die niemand nodig heeft, nooit zolang een gebruiker er nog naar verwijst
        if await self._schemas_opruimen(schema_weg, terug, verslag):
            verslag["_opnieuw"] = True   # DisplayID's kunnen verschoven zijn: tweede ronde zet en controleert alles opnieuw

        van_ons = {str(i["userid"]) for i in eigen.values()}
        fout = "; ".join(verslag["fouten"])[:400] or None
        self.status.update(ok=not verslag["fouten"], fout=fout, laatst=dt_util.utcnow().isoformat(), eigen=in_orde,
                           schemas=len(self._staat.get("schemas", {})),
                           gewenst=len(gewenst), toestel=len(lijst),
                           vreemd_boven_start=sum(1 for u in per_userid if u.isdigit() and int(u) >= USER_ID_START
                                                  and u not in van_ons))
        self.reg._bewaar()  # noqa: SLF001  enkel opslag, de tabletlijst verandert hier niet
        if fout and fout != staat.get("laatste_fout") and not verslag.get("_opnieuw"):
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.hass, f"De Akuvox ({self.ip}) staat niet zoals HA het wil: {fout}",
                title="Btechnics Toegang: Akuvox klopt niet", notification_id=f"btechnics_toegang_akuvox{self.nr}")
            _LOGGER.error("Akuvox %s: %s", self.nr, fout)
        if not verslag.get("_opnieuw"):
            staat["laatste_fout"] = fout
        if any(verslag[k] for k in ("toegevoegd", "gewijzigd", "gewist")):
            _LOGGER.info("Akuvox: %s", {k: v for k, v in verslag.items() if v and not k.startswith("_")})
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
            buiten = bool(p) and self._buiten(p, t)
            if pid and gelukt and laatste and (not p or p.get("status") != "active" or self.deur_id not in p.get("deuren", []) or buiten):
                # het toestel liet iemand binnen die volgens HA geen toegang had: altijd melden
                from homeassistant.components import persistent_notification
                waarom = ("niet meer gekend" if not p else "niet actief" if p.get("status") != "active"
                          else "geen recht op deze deur" if self.deur_id not in p.get("deuren", []) else "buiten de uren of datums")
                melding = f"De Akuvox liet {naam or pid} binnen om {tijd}, terwijl die volgens HA geen toegang had ({waarom}). HA zet het toestel opnieuw recht."
                persistent_notification.async_create(self.hass, melding, title="Btechnics Toegang: Akuvox liet iemand binnen",
                                                     notification_id=f"btechnics_toegang_akuvox{self.nr}_binnen")
                _LOGGER.error("Akuvox %s: %s", self.nr, melding)
                self.status.update(ok=False, fout=f"toestel liet {naam or pid} binnen om {tijd} zonder recht")
                for info in staat["gebruikers"].values():
                    if str(info.get("userid")) == str(x.get("UserID")):
                        info["dev_id"] = None
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

    PROEF_USERID = "799990"
    PROEF_NAAM = "BTX proef"

    async def _proef_opruimen(self, verslag: dict) -> int:
        """Alles van de proeven weg: eerst de proefgebruiker 799990 (naam 'BTX proef'), dan de schema's 'BTX proef ...'.
        Elke wissing apart in try, zodat één fout de rest niet tegenhoudt. Geeft het aantal gewiste schema's."""
        fouten = verslag.setdefault("opruimfouten", [])
        try:
            for u in await self.gebruikers():
                if str(u.get("UserID")) == self.PROEF_USERID and str(u.get("Name")) == self.PROEF_NAAM and u.get("ID"):
                    try:
                        await self._vraag("POST", "/api/user/del", {"target": "user", "action": "del", "data": {"item": [{"ID": str(u["ID"])}]}})
                    except AkuvoxFout as e:
                        fouten.append(f"proefgebruiker: {str(e)[:150]}")
        except AkuvoxFout as e:
            fouten.append(f"gebruikers lezen: {str(e)[:150]}")
        n = 0
        try:
            for x in await self.schemas():
                if str(x.get("Name", "")).startswith(self.PROEF_NAAM + " ") and x.get("ID") and str(x.get("Source", "Local")) == "Local":
                    try:
                        await self._vraag("POST", "/api/schedule/del", {"target": "schedule", "action": "del", "data": {"item": [{"ID": str(x["ID"])}]}})
                        n += 1
                    except AkuvoxFout as e:
                        fouten.append(f"schema {x.get('Name')}: {str(e)[:150]}")
        except AkuvoxFout as e:
            fouten.append(f"schema's lezen: {str(e)[:150]}")
        if not fouten:
            verslag.pop("opruimfouten", None)
        return n

    def _proef_toegelaten(self) -> None:
        if not self.aan:
            raise AkuvoxFout("Akuvox sync staat uit: geen proef op dit toestel")

    async def async_schema_proef(self, items: list[dict] | None = None, gebruiker: bool = True) -> dict:
        self._proef_toegelaten()
        async with self._lock:   # nooit tegelijk met een sync
            return await self._schema_proef(items, gebruiker)

    async def _schema_proef(self, items: list[dict] | None = None, gebruiker: bool = True) -> dict:
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

        verslag["voor"] = await self.schemas()
        await self._proef_opruimen(verslag)
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
                    verslag["gebruiker"] = g
                    terug = [u for u in await self.gebruikers() if str(u.get("UserID")) == "799990"]
                    g["teruggelezen"] = [{k: v for k, v in u.items() if k not in ("PrivatePIN", "CardCode", "BLE_AuthCode", "PhoneNum", "QrCodeUrl")} for u in terug]
                else:
                    verslag["gebruiker"] = "UserID 799990 bestaat al, niet aangeraakt"
        finally:
            # altijd, ook bij een fout halverwege: eerst de proefgebruiker, dan de schema's
            verslag["opgeruimd"] = await self._proef_opruimen(verslag)
            try:
                verslag["na"] = await self.schemas()
                if isinstance(verslag.get("gebruiker"), dict):
                    verslag["gebruiker"]["weg"] = not [u for u in await self.gebruikers() if str(u.get("UserID")) == self.PROEF_USERID]
            except AkuvoxFout as e:
                verslag.setdefault("opruimfouten", []).append(str(e)[:150])
        return verslag

    async def async_schema_volgorde(self) -> dict:
        self._proef_toegelaten()
        async with self._lock:
            return await self._schema_volgorde()

    async def _schema_volgorde(self) -> dict:
        """v0.15.1 meting: wat doet de S532 met DisplayID's bij wissen en toevoegen, en werkt schedule/set?
        Zet 'BTX proef A/B/C', wijzigt B, wist A, zet D, leest telkens terug en ruimt alles weer op."""
        P = "BTX proef "
        def eigen(lijst):
            return {x["Name"]: {k: x.get(k) for k in ("ID", "DisplayID", "Type", "Week", "Daily", "Date")}
                    for x in lijst if str(x.get("Name", "")).startswith(P)}
        async def add(n):
            j = await self._vraag("POST", "/api/schedule/add", {"target": "schedule", "action": "add",
                                                                 "data": {"item": [{"Name": P + n, "Type": "1", "Week": "12345", "Daily": "08:00-09:00"}]}})
            return ((j.get("data") or {}).get("item") or [{}])[0]
        stappen: dict[str, Any] = {}
        try:
            await self._proef_opruimen(stappen)
            for n in ("A", "B", "C"):
                await add(n)
            stappen["1_na_A_B_C"] = eigen(await self.schemas())
            b = stappen["1_na_A_B_C"].get(P + "B") or {}
            a = stappen["1_na_A_B_C"].get(P + "A") or {}
            if not b.get("ID") or not a.get("ID"):
                raise AkuvoxFout("proefschema's niet aangemaakt, meting gestopt")
            try:
                stappen["2_set_antwoord"] = await self._vraag("POST", "/api/schedule/set", {"target": "schedule", "action": "set", "data": {"item": [
                    {"ID": str(b.get("ID")), "DisplayID": str(b.get("DisplayID")), "Name": P + "B", "Type": "1", "Week": "06", "Daily": "10:00-11:00"}]}})
            except AkuvoxFout as e:
                stappen["2_set_antwoord"] = str(e)
            stappen["2_na_set_B"] = eigen(await self.schemas())
            await self._vraag("POST", "/api/schedule/del", {"target": "schedule", "action": "del", "data": {"item": [{"ID": str(a.get("ID"))}]}})
            stappen["3_na_wissen_A"] = eigen(await self.schemas())
            await add("D")
            stappen["4_na_D"] = eigen(await self.schemas())
        finally:
            await self._proef_opruimen(stappen)
            try:
                stappen["5_opgeruimd"] = not eigen(await self.schemas())
            except AkuvoxFout as e:
                stappen["5_opgeruimd"] = f"niet na te kijken: {e}"
        return stappen

    async def async_test(self) -> dict:
        """Enkel lezen: wat ziet HA op het toestel (voor de instellingen en de diagnose)."""
        lijst = await self.gebruikers()
        eigen = {i["userid"] for i in self._staat["gebruikers"].values()}
        try:
            log = await self._vraag("GET", "/api/doorlog/get?page=1")
            ruw_log = [{k: v for k, v in x.items() if k not in ("PicUrl", "Image", "Code")} for x in ((log.get("data") or {}).get("item") or [])[:6]]
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
