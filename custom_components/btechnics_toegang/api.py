"""HTTP endpoints voor de deurtablets (afsprakendocument schema 1).

De tablets gebruiken geen HA gebruiker maar een eigen tabletsleutel (Authorization: Bearer).
Daarom staan alle views op requires_auth = False en controleren we de sleutel zelf.
"""
from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import API, DOMAIN, EVENT_TOEGANG, LOG_METHODES, LOG_REDENEN, LOG_SOORTEN, MODI, WACHT_MAX
from .geldigheid import reden_geweigerd
from .register import Register, ToegangFout

_LOGGER = logging.getLogger(__name__)


def _reg(hass: HomeAssistant) -> Register:
    return hass.data[DOMAIN]["register"]


def _fout(status: int, tekst: str) -> web.Response:
    return web.json_response({"fout": tekst}, status=status)


class _TabletView(HomeAssistantView):
    requires_auth = False
    cors_allowed = False

    def _tablet(self, request: web.Request) -> dict | None:
        kop = request.headers.get("Authorization", "")
        if not kop.startswith("Bearer "):
            return None
        return _reg(request.app[KEY_HASS]).tablet_voor_sleutel(kop[7:].strip())

    async def _json(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


class KoppelView(_TabletView):
    url = f"{API}/koppel"
    name = f"api:{DOMAIN}:koppel"

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        data = await self._json(request)
        try:
            antw = _reg(hass).koppelen(
                str(data.get("code", "")), str(data.get("toestel_id") or ""),
                data.get("naam"), data.get("app_versie"),
            )
        except ToegangFout as e:
            _LOGGER.warning("Koppelen geweigerd vanaf %s: %s", request.remote, e)
            return _fout(HTTPStatus.FORBIDDEN, str(e))
        _LOGGER.info("Tablet %s gekoppeld aan deur %s", antw["tablet_id"], antw["deur_id"])
        return self.json(antw)


class WachtView(_TabletView):
    url = f"{API}/wacht"
    name = f"api:{DOMAIN}:wacht"

    async def get(self, request: web.Request) -> web.Response:
        if not self._tablet(request):
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        try:
            versie = int(request.query.get("versie", "0"))
        except ValueError:
            versie = 0
        reg = _reg(request.app[KEY_HASS])
        if await reg.wacht_op_versie(versie, WACHT_MAX):
            return self.json({"versie": reg.versie})
        return web.Response(status=HTTPStatus.NO_CONTENT)


class LijstView(_TabletView):
    url = f"{API}/lijst"
    name = f"api:{DOMAIN}:lijst"

    async def get(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        reg = _reg(request.app[KEY_HASS])
        etag = f'"{reg.versie}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag})
        try:
            lijst = reg.lijst_voor_deur(tablet["deur_id"])
        except ToegangFout as e:
            return _fout(HTTPStatus.NOT_FOUND, str(e))
        return web.json_response(lijst, headers={"ETag": etag})


def _log_regel(r: dict, tablet: dict, deur_naam: str) -> dict | None:
    if not isinstance(r, dict) or not r.get("uid"):
        return None
    soort = r.get("soort") if r.get("soort") in LOG_SOORTEN else "systeem"
    return {
        "uid": str(r["uid"])[:64],
        "tijd": str(r.get("tijd") or dt_util.now().isoformat())[:40],
        "soort": soort,
        "methode": r.get("methode") if r.get("methode") in LOG_METHODES else None,
        "reden": r.get("reden") if r.get("reden") in LOG_REDENEN else None,
        "persoon_id": r.get("persoon_id") or None,
        "tekst": str(r.get("tekst") or "")[:200],
        "tablet_id": tablet["id"],
        "deur_id": tablet["deur_id"],
        "deur": deur_naam,
    }


class LogView(_TabletView):
    url = f"{API}/log"
    name = f"api:{DOMAIN}:log"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        hass = request.app[KEY_HASS]
        reg = _reg(hass)
        data = await self._json(request)
        items = data.get("items")
        if not isinstance(items, list):
            return _fout(HTTPStatus.BAD_REQUEST, "items ontbreekt")
        deur_naam = reg.data["deuren"].get(tablet["deur_id"], {}).get("naam", "")
        regels = [x for x in (_log_regel(r, tablet, deur_naam) for r in items[:500]) if x]
        bekend = {r.get("uid") for r in reg.data["log"]}
        nieuw = [r for r in regels if r["uid"] not in bekend]
        ontvangen = reg.log_toevoegen(regels)
        for r in nieuw:
            p = reg.data["personen"].get(r["persoon_id"] or "")
            hass.bus.async_fire(EVENT_TOEGANG, {**r, "persoon": p["naam"] if p else None})
        return self.json({"ontvangen": ontvangen})


class ModusView(_TabletView):
    url = f"{API}/modus"
    name = f"api:{DOMAIN}:modus"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        data = await self._json(request)
        modus = data.get("modus")
        if modus not in MODI:
            return _fout(HTTPStatus.BAD_REQUEST, f"modus moet een van {', '.join(MODI)} zijn")
        d = _reg(request.app[KEY_HASS]).modus_zetten(tablet["deur_id"], modus, door=tablet["id"])
        return self.json({k: d[k] for k in ("modus", "modus_sinds", "modus_door")})


class StatusView(_TabletView):
    url = f"{API}/status"
    name = f"api:{DOMAIN}:status"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        _reg(request.app[KEY_HASS]).tablet_status(tablet, await self._json(request))
        return self.json({"ok": True})


async def deur_openen(hass: HomeAssistant, deur: dict) -> None:
    """Zet de schakelaar, het script of de knop aan die voor deze deur gekozen is."""
    ent = deur.get("open_entiteit")
    if not ent:
        raise ToegangFout(f"Voor deur {deur['naam']} is geen schakelaar of script gekozen")
    domein = ent.split(".")[0]
    dienst = {
        "script": ("script", "turn_on"),
        "lock": ("lock", "unlock"),
        "button": ("button", "press"),
        "input_button": ("input_button", "press"),
    }.get(domein, (domein, "turn_on"))
    await hass.services.async_call(*dienst, {"entity_id": ent}, blocking=True)


class OpenView(_TabletView):
    url = f"{API}/open"
    name = f"api:{DOMAIN}:open"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        hass = request.app[KEY_HASS]
        reg = _reg(hass)
        data = await self._json(request)
        methode = data.get("methode") if data.get("methode") in LOG_METHODES else None
        if not methode:
            return _fout(HTTPStatus.BAD_REQUEST, f"methode moet een van {', '.join(LOG_METHODES)} zijn")
        persoon_id = data.get("persoon_id") or None
        deur = reg.data["deuren"].get(tablet["deur_id"])
        if not deur:
            return _fout(HTTPStatus.NOT_FOUND, "deur bestaat niet meer")

        reden = None
        persoon = reg.data["personen"].get(persoon_id) if persoon_id else None
        if methode in ("pin", "kaart", "gezicht"):
            if not persoon or not persoon["actief"] or tablet["deur_id"] not in persoon["deuren"]:
                reden = "onbekend"
            else:
                reden = reden_geweigerd(persoon, dt_util.now())

        regel = {
            "uid": f"ha-{uuid.uuid4()}", "tijd": dt_util.now().replace(microsecond=0).isoformat(),
            "soort": "toegang" if not reden else "geweigerd", "methode": methode, "reden": reden,
            "persoon_id": persoon["id"] if persoon else None,
            "tekst": "Deur geopend via HA" if not reden else f"Openen geweigerd: {reden}",
            "tablet_id": tablet["id"], "deur_id": deur["id"], "deur": deur["naam"],
        }
        if not reden:
            try:
                await deur_openen(hass, deur)
            except ToegangFout as e:
                reden = "fout"
                regel.update(soort="fout", tekst=str(e))
            except Exception as e:  # noqa: BLE001 dienst faalde
                _LOGGER.error("Deur %s openen mislukt: %s", deur["naam"], e)
                reden = "fout"
                regel.update(soort="fout", tekst=f"Openen mislukt: {e}")
        reg.log_toevoegen([regel])
        hass.bus.async_fire(EVENT_TOEGANG, {**regel, "persoon": persoon["naam"] if persoon else None})
        if reden == "fout":
            return web.json_response({"geopend": False, "reden": "fout", "tekst": regel["tekst"]},
                                     status=HTTPStatus.BAD_GATEWAY)
        if reden:
            return web.json_response({"geopend": False, "reden": reden}, status=HTTPStatus.FORBIDDEN)
        return self.json({"geopend": True})


def registreer_views(hass: HomeAssistant) -> None:
    for view in (KoppelView, WachtView, LijstView, LogView, ModusView, StatusView, OpenView):
        hass.http.register_view(view())
