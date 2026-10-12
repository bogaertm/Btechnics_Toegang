"""Portaal voor medewerkers: aanmelden met een uitnodigingslink en later terugkomen met de eigen link.

Geen HA login: de link zelf (24 willekeurige bytes) is de sleutel. HA bewaart enkel een hash ervan.
Na het aanmelden krijgt de medewerker een nieuwe, vaste link. Wijzigen vraagt altijd de huidige code.
"""
from __future__ import annotations

import base64
import logging
import os
from http import HTTPStatus
from pathlib import Path

from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from .const import API, BEDRIJF, DOMAIN, FOTO_MAP, FOTO_MAX, PIN_MAX, PIN_MIN
from .register import Register, ToegangFout
from .websocket import portaal_url

_LOGGER = logging.getLogger(__name__)
HTML = Path(__file__).parent / "frontend" / "portaal.html"
KOPPEN = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}


def foto_pad(hass, pid: str) -> str:
    return hass.config.path(FOTO_MAP, f"{pid}.jpg")


def _foto_bytes(b64: str) -> bytes:
    if not isinstance(b64, str) or not b64:
        raise ToegangFout("Neem of kies een foto.")
    if "," in b64[:100]:
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except ValueError as e:
        raise ToegangFout("De foto kon niet gelezen worden.") from e
    if len(data) > FOTO_MAX:
        raise ToegangFout("De foto is te groot.")
    if not data.startswith(b"\xff\xd8"):
        raise ToegangFout("De foto moet een JPEG zijn.")
    return data


def _schrijf(pad: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    tmp = pad + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, pad)


def _lees(pad: str) -> bytes | None:
    try:
        with open(pad, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


class _PortaalView(HomeAssistantView):
    requires_auth = False
    cors_allowed = False

    def _reg(self, request) -> Register:
        return request.app[KEY_HASS].data[DOMAIN]["register"]

    def _zoek(self, request, token):
        if not token or len(token) > 100:
            return None
        return self._reg(request).portaal_zoek(token)

    async def _json(self, request) -> dict:
        try:
            d = await request.json()
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    @staticmethod
    def _antw(data: dict, status: int = 200) -> web.Response:
        return web.json_response(data, status=status, headers=KOPPEN)


class PortaalPagina(_PortaalView):
    url = API + "/portaal/{token}"
    name = f"api:{DOMAIN}:portaal"

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        html = await hass.async_add_executor_job(HTML.read_text, "utf-8")
        return web.Response(text=html, content_type="text/html", headers=KOPPEN)


def _deuren(reg: Register, ids: list[str]) -> list[str]:
    return [reg.data["deuren"].get(d, {}).get("naam", "?") for d in ids]


class PortaalInfo(_PortaalView):
    url = API + "/portaal/{token}/info"
    name = f"api:{DOMAIN}:portaal:info"

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        reg = self._reg(request)
        gevonden = self._zoek(request, token)
        basis = {"bedrijf": BEDRIJF, "pin": {"min": PIN_MIN, "max": PIN_MAX}, "tijdzone": hass.config.time_zone}
        if not gevonden:
            return self._antw({**basis, "soort": "ongeldig", "fout": "Deze link is niet (meer) geldig."}, HTTPStatus.NOT_FOUND)
        soort, obj = gevonden
        if soort == "uitnodiging":
            reden = reg.uitnodiging_bruikbaar(obj)
            if reden:
                return self._antw({**basis, "soort": "ongeldig", "fout": reden}, HTTPStatus.GONE)
            return self._antw({**basis, "soort": "uitnodiging", "naam": obj.get("naam", ""), "deuren": _deuren(reg, obj["deuren"]),
                               "valid_from": obj.get("valid_from"), "valid_until": obj.get("valid_until"),
                               "schema": obj.get("schema") or [], "verloopt": obj["verloopt"]})
        p = obj
        recent = await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.voor_persoon, p["id"], 10)
        return self._antw({**basis, "soort": "persoon", "voornaam": p.get("voornaam", ""), "achternaam": p.get("achternaam", ""),
                           "naam": p["naam"], "telefoon": p.get("telefoon", ""), "email": p.get("email", ""),
                           "deuren": _deuren(reg, p["deuren"]), "status": p["status"], "until": p.get("until"),
                           "valid_from": p.get("valid_from"), "valid_until": p.get("valid_until"), "schema": p.get("schema") or [],
                           "foto": bool(p.get("foto")), "foto_v": p.get("foto_v", 0),
                           "pin_lengte": len(p["pin"]) if p.get("pin") else 0, "recent": recent,
                           "geblokkeerd": p.get("fouten", 0) >= 10})


class PortaalAanmelden(_PortaalView):
    url = API + "/portaal/{token}/aanmelden"
    name = f"api:{DOMAIN}:portaal:aanmelden"

    async def post(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        reg = self._reg(request)
        d = await self._json(request)
        if not d.get("akkoord"):
            return self._antw({"fout": "Lees en aanvaard eerst de privacyverklaring."}, HTTPStatus.BAD_REQUEST)
        velden = {k: str(d.get(k) or "").strip()[:80] for k in ("voornaam", "achternaam", "telefoon", "email")}
        if not velden["voornaam"] or not velden["achternaam"]:
            return self._antw({"fout": "Vul je voornaam en naam in."}, HTTPStatus.BAD_REQUEST)
        if not velden["telefoon"] or not velden["email"] or "@" not in velden["email"]:
            return self._antw({"fout": "Vul je gsm-nummer en een geldig e-mailadres in."}, HTTPStatus.BAD_REQUEST)
        if str(d.get("pin") or "") != str(d.get("pin2") or ""):
            return self._antw({"fout": "De twee codes zijn niet gelijk."}, HTTPStatus.BAD_REQUEST)
        try:
            foto = _foto_bytes(d.get("foto"))
            p, nieuw = reg.aanmelden(token, pin=str(d.get("pin") or ""), **velden)
        except ToegangFout as e:
            return self._antw({"fout": str(e)}, HTTPStatus.BAD_REQUEST)
        await hass.async_add_executor_job(_schrijf, foto_pad(hass, p["id"]), foto)
        reg.foto_gezet(f"{p['naam']} (portaal)", p["id"])
        persistent_notification.async_create(
            hass, f"**{p['naam']}** meldde zich aan via het portaal. De code werkt meteen op: {', '.join(_deuren(reg, p['deuren']))}.",
            title="Btechnics Toegang: nieuwe medewerker", notification_id=f"{DOMAIN}_aanmelding_{p['id']}")
        hass.bus.async_fire(f"{DOMAIN}_aanmelding", {"persoon_id": p["id"], "naam": p["naam"]})
        return self._antw({"ok": True, "url": portaal_url(hass, nieuw), "token": nieuw})


class PortaalWijzig(_PortaalView):
    url = API + "/portaal/{token}/wijzig"
    name = f"api:{DOMAIN}:portaal:wijzig"

    async def post(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        reg = self._reg(request)
        gevonden = self._zoek(request, token)
        if not gevonden or gevonden[0] != "persoon":
            return self._antw({"fout": "Deze link is niet (meer) geldig."}, HTTPStatus.NOT_FOUND)
        p = gevonden[1]
        d = await self._json(request)
        nieuwe_pin = str(d.get("pin") or "") or None
        if nieuwe_pin and nieuwe_pin != str(d.get("pin2") or ""):
            return self._antw({"fout": "De twee nieuwe codes zijn niet gelijk."}, HTTPStatus.BAD_REQUEST)
        email = d.get("email")
        if email is not None and "@" not in str(email):
            return self._antw({"fout": "Vul een geldig e-mailadres in."}, HTTPStatus.BAD_REQUEST)
        try:
            foto = _foto_bytes(d["foto"]) if d.get("foto") else None
            reg.portaal_wijzigen(p, str(d.get("huidige_pin") or ""),
                                 telefoon=str(d["telefoon"]).strip()[:80] if d.get("telefoon") is not None else None,
                                 email=str(email).strip()[:80] if email is not None else None, nieuwe_pin=nieuwe_pin)
        except ToegangFout as e:
            return self._antw({"fout": str(e)}, HTTPStatus.BAD_REQUEST)
        if foto:
            await hass.async_add_executor_job(_schrijf, foto_pad(hass, p["id"]), foto)
            reg.foto_gezet(f"{p['naam']} (portaal)", p["id"])
        return self._antw({"ok": True})


class PortaalFoto(_PortaalView):
    url = API + "/portaal/{token}/foto"
    name = f"api:{DOMAIN}:portaal:foto"

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        gevonden = self._zoek(request, token)
        if not gevonden or gevonden[0] != "persoon":
            return web.Response(status=HTTPStatus.NOT_FOUND)
        data = await hass.async_add_executor_job(_lees, foto_pad(hass, gevonden[1]["id"]))
        if data is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(body=data, content_type="image/jpeg", headers=KOPPEN)


class BeheerFoto(HomeAssistantView):
    """Foto van een persoon voor het dashboard (enkel beheerders, via auth/sign_path)."""
    url = API + "/foto/{pid}"
    name = f"api:{DOMAIN}:foto"
    requires_auth = True

    async def get(self, request: web.Request, pid: str) -> web.Response:
        hass = request.app[KEY_HASS]
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return web.Response(status=HTTPStatus.FORBIDDEN, text="Enkel voor beheerders")
        if not pid.replace("_", "").isalnum():
            return web.Response(status=HTTPStatus.NOT_FOUND)
        data = await hass.async_add_executor_job(_lees, foto_pad(hass, pid))
        if data is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=3000"})


def registreer_portaal(hass) -> None:
    for v in (PortaalPagina, PortaalInfo, PortaalAanmelden, PortaalWijzig, PortaalFoto, BeheerFoto):
        hass.http.register_view(v())


async def foto_wissen(hass, pid: str) -> None:
    def _weg():
        try:
            os.remove(foto_pad(hass, pid))
        except FileNotFoundError:
            pass
    await hass.async_add_executor_job(_weg)
