"""HTTP endpoints voor de deurtablets (afsprakendocument schema 1).

De tablets gebruiken geen HA gebruiker maar een eigen tabletsleutel (Authorization: Bearer).
Daarom staan alle views op requires_auth = False en controleren we de sleutel zelf.
"""
from __future__ import annotations

import base64
import binascii
import logging
from http import HTTPStatus
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import API, DOMAIN, LOG_METHODES, MODI, WACHT_MAX
from .geldigheid import reden_geweigerd
from .register import Register, ToegangFout

FOTO_MAX = 400 * 1024   # foto van de tablet bij een toegang

_LOGGER = logging.getLogger(__name__)


def _reg(hass: HomeAssistant) -> Register:
    return hass.data[DOMAIN]["register"]


def _fout(status: int, tekst: str) -> web.Response:
    return web.json_response({"fout": tekst}, status=status)


def _enkel_https(hass: HomeAssistant, request: web.Request) -> None:
    """Na de overgangsperiode (optie enkel_https) weigert HA tabletverkeer over gewoon http."""
    entry = hass.data[DOMAIN].get("entry")
    if entry and entry.options.get("enkel_https") and not request.secure:
        raise web.HTTPForbidden(text='{"fout": "enkel https: gebruik poort %s"}' % entry.options.get("https_poort", 8443),
                                content_type="application/json")


def https_info(hass: HomeAssistant) -> dict | None:
    h = hass.data[DOMAIN].get("https")
    if not h or not h.vingerafdruk or h.fout:
        return None
    return {"poort": h.poort, "sha256": h.vingerafdruk}


class _TabletView(HomeAssistantView):
    requires_auth = False
    cors_allowed = False

    def _tablet(self, request: web.Request) -> dict | None:
        hass = request.app[KEY_HASS]
        _enkel_https(hass, request)
        kop = request.headers.get("Authorization", "")
        if not kop.startswith("Bearer "):
            return None
        t = _reg(hass).tablet_voor_sleutel(kop[7:].strip())
        if t is not None:
            t["kanaal"] = "https" if request.secure else "http"
        return t

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
        _enkel_https(hass, request)
        data = await self._json(request)
        try:
            antw = _reg(hass).koppelen(
                str(data.get("code", "")), str(data.get("toestel_id") or ""),
                data.get("naam"), data.get("app_versie"), data.get("pubkey"),
            )
        except ToegangFout as e:
            _LOGGER.warning("Koppelen geweigerd vanaf %s: %s", request.remote, e)
            return _fout(HTTPStatus.FORBIDDEN, str(e))
        _LOGGER.info("Tablet %s gekoppeld aan deur %s", antw["tablet_id"], antw["deur_id"])
        t = _reg(hass).data["tablets"].get(antw["tablet_id"])
        if t is not None:
            t["kanaal"] = "https" if request.secure else "http"
        # https voor de tablets: de tablet zet deze vingerafdruk vast (ook al koppelt hij via http)
        antw["https"] = https_info(hass)
        return self.json(antw)


class WachtView(_TabletView):
    url = f"{API}/wacht"
    name = f"api:{DOMAIN}:wacht"

    async def get(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        try:
            versie = int(request.query.get("versie", "0"))
        except ValueError:
            versie = 0
        reg = _reg(request.app[KEY_HASS])
        if await reg.wacht_op_versie(versie, WACHT_MAX, tablet["id"]):
            antwoord = {"versie": reg.versie}
            fotos = reg.fotovragen_ophalen(tablet["id"])
            if fotos:
                antwoord["foto"] = fotos   # HA opende de deur: neem nu een foto en stuur ze met deze uid naar POST /foto
            return self.json(antwoord)
        return web.Response(status=HTTPStatus.NO_CONTENT)


class LijstView(_TabletView):
    url = f"{API}/lijst"
    name = f"api:{DOMAIN}:lijst"

    async def get(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        reg = _reg(request.app[KEY_HASS])
        etag = f'"{reg.versie}-{(tablet.get("pubkey_id") or "")[:12]}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag})
        try:
            lijst = reg.lijst_voor_deur(tablet["deur_id"], tablet)
        except ToegangFout as e:
            return _fout(HTTPStatus.NOT_FOUND, str(e))
        tablet["laatste_lijst_ha"] = dt_util.utcnow().isoformat()
        return web.json_response(lijst, headers={"ETag": etag})


class LogView(_TabletView):
    url = f"{API}/log"
    name = f"api:{DOMAIN}:log"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        data = await self._json(request)
        items = data.get("items")
        if not isinstance(items, list):
            return _fout(HTTPStatus.BAD_REQUEST, "items ontbreekt")
        ontvangen = await request.app[KEY_HASS].data[DOMAIN]["toegang"].async_regels(tablet, items)
        return self.json({"ontvangen": ontvangen})


class FotoView(_TabletView):
    """Foto die de tablet nam bij een toegang of weigering: {uid, foto (JPEG in base64)}. uid = die van de logregel."""
    url = f"{API}/foto"
    name = f"api:{DOMAIN}:tabletfoto"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        data = await self._json(request)
        uid = str(data.get("uid") or "")[:64]
        try:
            beeld = base64.b64decode(str(data.get("foto") or ""), validate=True)
        except (binascii.Error, ValueError):
            beeld = b""
        if not uid:
            return _fout(HTTPStatus.BAD_REQUEST, "uid ontbreekt")
        if not beeld.startswith(b"\xff\xd8") or len(beeld) > FOTO_MAX:
            return _fout(HTTPStatus.BAD_REQUEST, f"foto moet een JPEG zijn van hoogstens {FOTO_MAX // 1024} kB")
        fid = await request.app[KEY_HASS].data[DOMAIN]["toegang"].async_tablet_foto(tablet, uid, beeld)
        return self.json({"ok": True, "foto": fid})


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
        fout = _reg(request.app[KEY_HASS]).tablet_status(tablet, await self._json(request))
        antw = {"ok": True, "pubkey_id": tablet.get("pubkey_id")}
        if fout:
            antw["pubkey_fout"] = fout
        return self.json(antw)


class OpenView(_TabletView):
    url = f"{API}/open"
    name = f"api:{DOMAIN}:open"

    async def post(self, request: web.Request) -> web.Response:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        hass = request.app[KEY_HASS]
        reg = _reg(hass)
        tg = hass.data[DOMAIN]["toegang"]
        data = await self._json(request)
        methode = data.get("methode") if data.get("methode") in LOG_METHODES else None
        if not methode:
            return _fout(HTTPStatus.BAD_REQUEST, f"methode moet een van {', '.join(LOG_METHODES)} zijn")
        deur = reg.data["deuren"].get(tablet["deur_id"])
        if not deur:
            return _fout(HTTPStatus.NOT_FOUND, "deur bestaat niet meer")
        persoon = reg.data["personen"].get(data.get("persoon_id") or "")
        reden = None
        if methode in ("pin", "kaart", "gezicht"):
            if not persoon or tablet["deur_id"] not in persoon["deuren"]:
                reden = "onbekend"
            elif persoon["status"] != "active":
                reden = "verlopen" if persoon["status"] == "retired" else "buiten_schema"
            else:
                reden = reden_geweigerd({"schema": persoon["schema"]}, dt_util.now())
        # De tablet schrijft zelf een logregel; HA registreert enkel wat het zelf besliste als de tablet
        # daar niet om vraagt (log=false), zodat een toegang niet dubbel in de historiek komt.
        dubbel = data.get("log") is False
        if reden:
            if dubbel:
                await tg.async_registreer(deur, methode=methode, persoon=persoon, opened=False, tekst=f"Openen geweigerd: {reden}")
            return web.json_response({"geopend": False, "reden": reden}, status=HTTPStatus.FORBIDDEN)
        try:
            await tg.async_deur_openen(deur)
        except ToegangFout as e:
            return web.json_response({"geopend": False, "reden": "fout", "tekst": str(e)}, status=HTTPStatus.BAD_GATEWAY)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Deur %s openen mislukt: %s", deur["naam"], e)
            return web.json_response({"geopend": False, "reden": "fout", "tekst": f"Openen mislukt: {e}"},
                                     status=HTTPStatus.BAD_GATEWAY)
        if dubbel:
            await tg.async_registreer(deur, methode=methode, persoon=persoon, opened=True, tekst="Deur geopend via HA")
        return self.json({"geopend": True})


TABLET_VIEWS = (KoppelView, WachtView, LijstView, LogView, FotoView, ModusView, StatusView, OpenView)


def registreer_views(hass: HomeAssistant) -> None:
    for view in TABLET_VIEWS:
        hass.http.register_view(view())
