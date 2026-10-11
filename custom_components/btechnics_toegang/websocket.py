"""WebSocket voor de dashboardkaarten (enkel beheerders). Wijzigingen lopen via de diensten."""
from __future__ import annotations

import time

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, OFFLINE_NA


@callback
def async_registreer_ws(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_data)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/data", vol.Optional("log", default=500): int})
@callback
def ws_data(hass, connection, msg):
    reg = hass.data.get(DOMAIN, {}).get("register")
    if reg is None:
        connection.send_error(msg["id"], "niet_geladen", "Btechnics Toegang is niet geladen")
        return
    d = reg.data
    nu = time.monotonic()
    tablets = [{
        "id": t["id"], "naam": t["naam"], "deur_id": t["deur_id"], "app_versie": t.get("app_versie"),
        "gekoppeld": t.get("gekoppeld"), "laatst": t.get("laatst"),
        "online": bool(t.get("laatst_mono") and nu - t["laatst_mono"] < OFFLINE_NA),
        "status": t.get("status") or {},
    } for t in d["tablets"].values()]
    personen = [{
        "id": p["id"], "naam": p["naam"], "actief": p["actief"], "pin": p.get("pin"),
        "kaarten": len(p["kaarten"]), "deuren": p["deuren"], "van": p["van"], "tot": p["tot"],
        "schema": p["schema"], "gemaakt": p.get("gemaakt"),
    } for p in d["personen"].values()]
    personen.sort(key=lambda p: p["naam"].lower())
    deuren = sorted(d["deuren"].values(), key=lambda x: x["naam"].lower())
    namen = {p["id"]: p["naam"] for p in d["personen"].values()}
    log = [{**r, "persoon": namen.get(r.get("persoon_id") or "")} for r in d["log"][-max(1, min(msg["log"], 2000)):]]
    log.reverse()
    connection.send_result(msg["id"], {"deuren": deuren, "personen": personen, "tablets": tablets, "log": log,
                                       "versie": d["versie"]})
