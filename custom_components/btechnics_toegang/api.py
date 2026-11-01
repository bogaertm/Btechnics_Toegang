"""HTTP endpoints voor de deurtablets (afsprakendocument schema 1).

De tablets gebruiken geen HA gebruiker maar een eigen tabletsleutel (Authorization: Bearer).
Daarom staan alle views op requires_auth = False en controleren we de sleutel zelf.
"""
from __future__ import annotations

import base64
import tempfile
import asyncio
import binascii
import hashlib
import logging
import os
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


def _niet_via_internet(request: web.Request) -> None:
    """Tablets en de gezichtsserver praten enkel lokaal met HA. Verkeer via de Cloudflare tunnel (ha.voorbeeld.be)
    draagt Cf-Connecting-Ip of X-Forwarded-For: dat weigeren we voor alle toestel-endpoints."""
    if request.headers.get("Cf-Connecting-Ip") or request.headers.get("Cf-Ray") or request.headers.get("X-Forwarded-For"):
        raise web.HTTPForbidden(text='{"fout": "enkel bereikbaar op het lokale netwerk"}', content_type="application/json")


def _enkel_https(hass: HomeAssistant, request: web.Request) -> None:
    """Na de overgangsperiode (optie enkel_https) weigert HA tabletverkeer over gewoon http."""
    _niet_via_internet(request)
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
                data.get("naam"), data.get("app_versie"), data.get("pubkey"), data.get("soort"),
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
            vragen = reg.wacht_ophalen(tablet["id"])
            if vragen["foto"]:
                antwoord["foto"] = vragen["foto"]   # HA opende de deur: neem nu een foto en stuur ze met deze uid naar POST /foto
            if vragen["geopend"]:
                antwoord["geopend"] = vragen["geopend"]   # schema 2: de tablet toont "Deur open" met het deurgeluid
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
        if tablet.get("soort") == "server":
            # de gezichtsserver mag enkel op gezicht openen, en enkel als de beheerder dat voor deze deur aanzette
            if methode != "gezicht":
                return web.json_response({"geopend": False, "reden": "niet_toegelaten"}, status=HTTPStatus.FORBIDDEN)
            if not deur.get("gezicht_live"):
                return web.json_response({"geopend": False, "reden": "proef"}, status=HTTPStatus.FORBIDDEN)
        persoon = reg.data["personen"].get(data.get("persoon_id") or "")
        reden = None
        if methode in ("pin", "kaart") and (deur.get("klavier_tot") or "") > dt_util.now().isoformat(timespec="seconds"):
            reden = "geblokkeerd"   # te veel foute pogingen: HA weigert ook als de tablet het klavier niet dichtzet
        elif methode in ("pin", "kaart", "gezicht"):
            if not persoon or tablet["deur_id"] not in persoon["deuren"]:
                reden = "onbekend"
            elif persoon["status"] != "active":
                reden = "verlopen" if persoon["status"] == "retired" else "buiten_schema"
            elif (persoon.get("valid_until") or "") and dt_util.parse_datetime(persoon["valid_until"]) <= dt_util.now():
                reden = "verlopen"   # nog voor de planner langskomt
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


class HerverpakView(_TabletView):
    """Enkel voor de gezichtsserver: afdrukken doorzetten naar nieuwe toestellen zonder nieuwe scan.
    GET geeft taken (verpakte sleutel voor de server + publieke sleutels van de doelen), POST {persoon_id, sleutels: {pubkey_id: ...}}."""
    url = f"{API}/herverpak"
    name = f"api:{DOMAIN}:herverpak"

    async def get(self, request: web.Request) -> web.Response:
        t = self._tablet(request)
        if not t or t.get("soort") != "server":
            return _fout(HTTPStatus.UNAUTHORIZED, "enkel voor de gezichtsserver")
        return self.json({"taken": _reg(request.app[KEY_HASS]).herverpak_taken(t)})

    async def post(self, request: web.Request) -> web.Response:
        t = self._tablet(request)
        if not t or t.get("soort") != "server":
            return _fout(HTTPStatus.UNAUTHORIZED, "enkel voor de gezichtsserver")
        data = await self._json(request)
        try:
            n = _reg(request.app[KEY_HASS]).herverpak_zetten(t, str(data.get("persoon_id") or ""), data.get("sleutels") or {})
        except ToegangFout as e:
            return _fout(HTTPStatus.BAD_REQUEST, str(e))
        return self.json({"ok": True, "nieuw": n})


APK_MAX = 150 * 1024 * 1024


def apk_pad(hass: HomeAssistant) -> str:
    return hass.config.path(DOMAIN, "apk", "btechnics-deur.apk")


class ApkView(_TabletView):
    """Schema 2: de tablet haalt de update die in de lijst staat ("app"). Enkel tablets, met de tabletsleutel."""
    url = f"{API}/apk"
    name = f"api:{DOMAIN}:apk"

    async def get(self, request: web.Request) -> web.StreamResponse:
        tablet = self._tablet(request)
        if not tablet:
            return _fout(HTTPStatus.UNAUTHORIZED, "niet gekoppeld")
        if tablet.get("soort", "tablet") == "server":
            return _fout(HTTPStatus.FORBIDDEN, "enkel voor tablets")
        app = _reg(request.app[KEY_HASS]).data.get("app")
        pad = apk_pad(request.app[KEY_HASS])
        if not app or not os.path.isfile(pad):
            return _fout(HTTPStatus.NOT_FOUND, "geen update klaar")
        if app.get("grootte") and await request.app[KEY_HASS].async_add_executor_job(os.path.getsize, pad) != app["grootte"]:
            return _fout(HTTPStatus.SERVICE_UNAVAILABLE, "update wordt nog klaargezet")
        _LOGGER.info("Tablet %s haalt app %s", tablet["naam"], app.get("versie"))
        return web.FileResponse(pad, headers={"Content-Type": "application/vnd.android.package-archive"})


class ApkUploadView(HomeAssistantView):
    """Beheerder zet een nieuwe versie van de app klaar: POST /api/btechnics_toegang/apk_upload?version_code=66&versie=1.72&sha256=<hex>
    met de APK als body (HA token van een beheerder). HA rekent zelf de sha256 na; bij een verschil wordt niets bewaard."""
    url = f"{API}/apk_upload"
    name = f"api:{DOMAIN}:apk_upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        _niet_via_internet(request)
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return _fout(HTTPStatus.FORBIDDEN, "enkel een beheerder")
        try:
            vc = int(request.query.get("version_code", ""))
        except ValueError:
            return _fout(HTTPStatus.BAD_REQUEST, "version_code ontbreekt")
        versie = request.query.get("versie", str(vc))
        verwacht = request.query.get("sha256", "").lower()
        if len(verwacht) != 64:
            return _fout(HTTPStatus.BAD_REQUEST, "sha256 ontbreekt")
        # v0.15.2: één upload tegelijk, eigen tijdelijk bestand, altijd opruimen, versie opnieuw nakijken voor het vervangen
        slot = hass.data[DOMAIN].setdefault("apk_slot", asyncio.Lock())
        async with slot:
            oud = (_reg(hass).data.get("app") or {}).get("version_code") or 0
            if vc < oud:
                return _fout(HTTPStatus.CONFLICT, f"versie {vc} is ouder dan {oud}")
            pad = apk_pad(hass)
            await hass.async_add_executor_job(lambda: os.makedirs(os.path.dirname(pad), exist_ok=True))
            fd, tmp = await hass.async_add_executor_job(lambda: tempfile.mkstemp(dir=os.path.dirname(pad), suffix=".tmp"))
            bestand = await hass.async_add_executor_job(os.fdopen, fd, "wb")
            h = hashlib.sha256()
            grootte = 0
            klaar = False
            try:
                try:
                    async for stuk in request.content.iter_chunked(1024 * 256):
                        grootte += len(stuk)
                        if grootte > APK_MAX:
                            return _fout(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "te groot")
                        h.update(stuk)
                        await hass.async_add_executor_job(bestand.write, stuk)
                finally:
                    await hass.async_add_executor_job(bestand.close)
                if h.hexdigest() != verwacht or grootte < 1024:
                    return _fout(HTTPStatus.BAD_REQUEST, "sha256 klopt niet met wat ontvangen werd")
                # eerst het bestand op zijn plaats, pas daarna aankondigen (apk_zetten verwittigt de tablets meteen)
                await hass.async_add_executor_job(os.replace, tmp, pad)
                klaar = True
                try:
                    app = _reg(hass).apk_zetten(vc, versie, verwacht, grootte, os.path.basename(pad), wie=user.name or "")
                except ToegangFout as e:
                    return _fout(HTTPStatus.CONFLICT, str(e))
            finally:
                if not klaar:
                    await hass.async_add_executor_job(lambda: os.path.exists(tmp) and os.remove(tmp))
        return self.json({"ok": True, **{k: app[k] for k in ("version_code", "versie", "sha256", "grootte")}})


TABLET_VIEWS = (KoppelView, WachtView, LijstView, LogView, FotoView, ModusView, StatusView, OpenView, HerverpakView, ApkView)


def registreer_views(hass: HomeAssistant) -> None:
    for view in TABLET_VIEWS:
        hass.http.register_view(view())
    hass.http.register_view(ApkUploadView())
