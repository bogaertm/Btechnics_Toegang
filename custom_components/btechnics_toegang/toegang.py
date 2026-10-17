"""Toegangen verwerken: logregels van de tablets en openingen via Home Assistant naar het archief,
events en de cache van recente toegangen per deur (voor sensoren en het overzicht)."""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .archief import Archief
from .const import DOMAIN, EVENT_TOEGANG, LOG_METHODES, LOG_REDENEN, LOG_SOORTEN, PULS_STANDAARD, SIGNAL_UPDATE
from .register import Register, ToegangFout

_LOGGER = logging.getLogger(__name__)

METHODE_LABEL = {"pin": "code", "kaart": "badge", "gezicht": "gezicht", "ha": "op afstand", "knop": "exitknop"}
NAAM_ZONDER_PERSOON = {"pin": "Foute code", "kaart": "Onbekende badge", "gezicht": "Onbekend gezicht",
                       "ha": "Op afstand", "knop": "Exitknop"}
RECENT = 10


class Toegang:
    def __init__(self, hass: HomeAssistant, reg: Register, archief: Archief) -> None:
        self.hass, self.reg, self.archief = hass, reg, archief
        self.recent: dict[str, list[dict]] = {}
        self._puls: dict[str, object] = {}

    async def async_start(self) -> None:
        for did in self.reg.data["deuren"]:
            self.recent[did] = await self.hass.async_add_executor_job(self.archief.recent, did, RECENT)
        # logregels van v0.2/v0.3 (in de opslag) eenmalig naar het archief
        oud = getattr(self.reg, "_oud_log", [])
        if oud:
            rijen = [r for r in (self._archiefrij(x, x.get("deur_id"), x.get("deur", "")) for x in oud) if r]
            await self.hass.async_add_executor_job(self.archief.toevoegen, rijen)
            self.reg._oud_log = []
            self.reg.gewijzigd(lijst_veranderd=False)

    async def async_foto(self, deur_id: str, uid: str, ts: int | None = None) -> str:
        """Foto bij een toegang. Eerst een foto die de tablet al stuurde (POST /foto), anders een momentopname van de
        camera van de deur (enkel voor verse toegangen). Geeft het foto id terug (a_<hash>) of ""."""
        from .portaal import foto_pad
        fid = foto_id(uid)
        if await self.hass.async_add_executor_job(os.path.exists, foto_pad(self.hass, fid)):
            return fid
        deur = self.reg.data["deuren"].get(deur_id) or {}
        cam = deur.get("camera")
        if not cam or (ts and abs(time.time() - ts) > 120):
            return ""
        try:
            beeld = await _camera_beeld(self.hass, cam)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Foto van %s voor deur %s mislukt: %s", cam, deur.get("naam"), e)
            return ""
        await self.hass.async_add_executor_job(_schrijf_foto, foto_pad(self.hass, fid), beeld)
        return fid

    async def async_tablet_foto(self, tablet: dict, uid: str, beeld: bytes) -> str:
        """Foto die de tablet nam bij een toegang. Staat de logregel er al, dan krijgt die meteen de foto."""
        from .portaal import foto_pad
        fid = foto_id(uid)
        await self.hass.async_add_executor_job(_schrijf_foto, foto_pad(self.hass, fid), beeld)
        n = await self.hass.async_add_executor_job(self.archief.foto_zetten, uid, fid, tablet["deur_id"])
        if n:
            self.recent[tablet["deur_id"]] = await self.hass.async_add_executor_job(self.archief.recent, tablet["deur_id"], RECENT)
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        return fid

    def _archiefrij(self, r: dict, deur_id: str | None, deur: str) -> dict | None:
        if r.get("soort") not in ("toegang", "geweigerd") or not deur_id:
            return None
        t = dt_util.parse_datetime(str(r.get("tijd") or "")) or dt_util.now()
        p = self.reg.data["personen"].get(r.get("persoon_id") or "")
        methode = r.get("methode")
        naam = p["naam"] if p else NAAM_ZONDER_PERSOON.get(methode, "Onbekende code")
        if methode == "ha" and r.get("wie"):
            label = f"op afstand door {r['wie']}"
        elif r.get("reden") == "vermoedelijke_foto":
            label = "gezicht (vermoedelijke foto)"
        else:
            label = METHODE_LABEL.get(methode, "onbekend")
        return {"uid": str(r.get("uid") or uuid.uuid4()), "door_id": deur_id, "door": deur, "ts": int(t.timestamp()),
                "name": naam, "method": label, "opened": r.get("soort") == "toegang",
                "persoon_id": p["id"] if p else "", "tekst": str(r.get("tekst") or "")[:200]}

    async def async_regels(self, tablet: dict, items: list) -> list[str]:
        """Logboek van een tablet. Toegangen naar het archief, de rest naar de tabletmeldingen."""
        deur = self.reg.data["deuren"].get(tablet["deur_id"], {})
        ontvangen, rijen = [], []
        for r in items[:500]:
            if not isinstance(r, dict) or not r.get("uid"):
                continue
            uid = str(r["uid"])[:64]
            regel = {
                "uid": uid, "tijd": str(r.get("tijd") or dt_util.now().isoformat())[:40],
                "soort": r.get("soort") if r.get("soort") in LOG_SOORTEN else "systeem",
                "methode": r.get("methode") if r.get("methode") in LOG_METHODES else None,
                "reden": r.get("reden") if r.get("reden") in LOG_REDENEN else None,
                "persoon_id": r.get("persoon_id") or None, "tekst": str(r.get("tekst") or "")[:200],
                "tablet_id": tablet["id"], "deur_id": tablet["deur_id"], "deur": deur.get("naam", ""),
            }
            if isinstance(r.get("score"), (int, float)) and not isinstance(r.get("score"), bool):
                regel["score"] = round(float(r["score"]), 3)
            if regel["reden"] == "vermoedelijke_foto":
                # Meetstand van de app: soort systeem, deur niet tegengehouden. Enkel een tabletmelding, geen HA melding.
                meet = regel["soort"] == "systeem"
                if not meet:
                    regel["soort"] = "geweigerd"
                score = f", score {regel['score']}" if "score" in regel else ""
                regel["tekst"] = (("meetstand: " if meet else "") + f"vermoedelijke foto{score}"
                                  + (f": {regel['tekst']}" if regel["tekst"] else ""))[:200]
                if not meet:
                    self._melding_foto(tablet, deur, regel)
            ontvangen.append(uid)
            rij = self._archiefrij(regel, tablet["deur_id"], deur.get("naam", ""))
            if rij:
                rijen.append((rij, regel))
            elif not any(g.get("uid") == uid for g in self.reg.data["gebeurtenissen"][-200:]):
                self.reg.gebeurtenis(regel)
        for rij, _ in rijen[-10:]:
            rij["foto"] = await self.async_foto(rij["door_id"], rij["uid"], rij["ts"])
        if rijen:
            nieuw = await self.hass.async_add_executor_job(self.archief.toevoegen, [x for x, _ in rijen])
            if nieuw:
                await self._na_toevoegen(tablet["deur_id"], rijen)
        return ontvangen

    def _melding_foto(self, tablet: dict, deur: dict, regel: dict) -> None:
        """Echtheidscontrole van de tablet: iemand probeerde met een foto of scherm binnen te geraken."""
        from homeassistant.components import persistent_notification
        p = self.reg.data["personen"].get(regel.get("persoon_id") or "")
        wie = p["naam"] if p else "een onbekend gezicht"
        persistent_notification.async_create(
            self.hass, f"Tablet **{tablet['naam']}** ({deur.get('naam', '')}) weigerde een gezicht dat op een foto of scherm leek: "
            f"{wie}{', score ' + str(regel['score']) if 'score' in regel else ''}. Het gezicht van die persoon is 5 minuten "
            "geblokkeerd op die tablet; de code blijft werken.",
            title="Btechnics Toegang: vermoedelijke foto", notification_id=f"{DOMAIN}_foto_{regel['uid']}")
        self.hass.bus.async_fire(f"{DOMAIN}_vermoedelijke_foto", {"tablet": tablet["naam"], "deur_id": tablet["deur_id"],
                                                                  "persoon_id": regel.get("persoon_id"), "score": regel.get("score")})

    async def _na_toevoegen(self, deur_id: str, rijen: list[tuple[dict, dict]]) -> None:
        self.recent[deur_id] = await self.hass.async_add_executor_job(self.archief.recent, deur_id, RECENT)
        for rij, regel in rijen:
            self.hass.bus.async_fire(EVENT_TOEGANG, {**regel, "persoon": rij["name"], "opened": rij["opened"]})
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_registreer(self, deur: dict, *, methode: str, persoon: dict | None, opened: bool, tekst: str,
                               wie: str | None = None) -> None:
        """Een opening of weigering die Home Assistant zelf afhandelde (POST /open of Deur openen)."""
        regel = {"uid": f"ha-{uuid.uuid4()}", "tijd": dt_util.now().replace(microsecond=0).isoformat(),
                 "soort": "toegang" if opened else "geweigerd", "methode": methode,
                 "persoon_id": persoon["id"] if persoon else None, "tekst": tekst, "wie": wie,
                 "deur_id": deur["id"], "deur": deur["naam"]}
        rij = self._archiefrij(regel, deur["id"], deur["naam"])
        rij["foto"] = await self.async_foto(deur["id"], rij["uid"], rij["ts"])
        await self.hass.async_add_executor_job(self.archief.toevoegen, [rij])
        await self._na_toevoegen(deur["id"], [(rij, regel)])

    async def async_deur_openen(self, deur: dict) -> None:
        ent = deur.get("open_entiteit")
        ak = self.hass.data.get(DOMAIN, {}).get("akuvox")
        if not ent and ak and ak.aan and ak.deur_id == deur["id"]:
            await ak.async_open(deur.get("puls_s", PULS_STANDAARD) or 5)
            return
        if not ent:
            raise ToegangFout(f"Voor deur {deur['naam']} is geen schakelaar of script gekozen")
        domein = ent.split(".")[0]
        dienst = {"script": ("script", "turn_on"), "lock": ("lock", "unlock"), "button": ("button", "press"),
                  "input_button": ("input_button", "press")}.get(domein, (domein, "turn_on"))
        await self.hass.services.async_call(*dienst, {"entity_id": ent}, blocking=True)
        # schakelaar of input_boolean na de pulsduur zelf terug uitzetten (een relais mag niet aan blijven)
        puls = deur.get("puls_s", PULS_STANDAARD)
        if domein in ("switch", "input_boolean", "light") and puls:
            oud = self._puls.pop(deur["id"], None)
            if oud:
                oud()

            async def _uit(_now, ent=ent, domein=domein, deur_id=deur["id"]):
                self._puls.pop(deur_id, None)
                try:
                    await self.hass.services.async_call(domein, "turn_off", {"entity_id": ent}, blocking=True)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("Deur %s: %s terug uitzetten mislukt", deur_id, ent, exc_info=True)

            self._puls[deur["id"]] = async_call_later(self.hass, puls, _uit)


def foto_id(uid: str) -> str:
    return "a_" + hashlib.sha1(str(uid).encode()).hexdigest()[:20]


def _schrijf_foto(pad: str, beeld: bytes) -> None:
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    with open(pad, "wb") as f:
        f.write(beeld)


async def _camera_beeld(hass: HomeAssistant, cam: str) -> bytes:
    from homeassistant.components.camera import async_get_image
    return (await async_get_image(hass, cam, timeout=8)).content


def ts_lokaal(ts: int) -> datetime:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts))
