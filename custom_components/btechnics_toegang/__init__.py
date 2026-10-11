"""Btechnics Toegang: codebeheer voor de deurtablets (Btechnics Deur) vanuit Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .api import deur_openen, registreer_views
from .frontend import async_registreer_kaarten
from .websocket import async_registreer_ws
from .const import DOMAIN, MODI
from .register import Register, ToegangFout

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SELECT, Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_BLOK = vol.Schema({vol.Required("dagen"): [vol.All(int, vol.Range(1, 7))],
                    vol.Required("van"): cv.string, vol.Required("tot"): cv.string})
_PERSOON = {
    vol.Optional("pincode"): cv.string,
    vol.Optional("deuren"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("van"): vol.Any(None, cv.string),
    vol.Optional("tot"): vol.Any(None, cv.string),
    vol.Optional("schema"): vol.Any(None, [_BLOK]),
}


def _reg(hass: HomeAssistant) -> Register:
    if DOMAIN not in hass.data or "register" not in hass.data[DOMAIN]:
        raise HomeAssistantError("Btechnics Toegang is niet geladen")
    return hass.data[DOMAIN]["register"]


def _publiek(p: dict) -> dict:
    """Persoon zonder pincode of hashes, voor antwoorden van diensten."""
    return {"id": p["id"], "naam": p["naam"], "actief": p["actief"], "pincode": bool(p.get("pin")),
            "kaarten": len(p["kaarten"]), "deuren": p["deuren"], "van": p["van"], "tot": p["tot"],
            "schema": p["schema"]}


def _veilig(fn):
    async def inner(call: ServiceCall):
        try:
            return await fn(call)
        except ToegangFout as e:
            raise HomeAssistantError(str(e)) from e
    return inner


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})

    @_veilig
    async def deur_toevoegen(call: ServiceCall) -> ServiceResponse:
        d = _reg(hass).deur_toevoegen(call.data["naam"], call.data.get("open_entiteit"))
        await _herlaad_entiteiten(hass)
        return {"deur": d}

    @_veilig
    async def deur_wijzigen(call: ServiceCall) -> ServiceResponse:
        reg = _reg(hass)
        d = reg.deur_wijzigen(reg.deur_op_naam(call.data["deur"])["id"],
                              call.data.get("naam"), call.data.get("open_entiteit"))
        await _herlaad_entiteiten(hass)
        return {"deur": d}

    @_veilig
    async def deur_verwijderen(call: ServiceCall) -> None:
        reg = _reg(hass)
        reg.deur_verwijderen(reg.deur_op_naam(call.data["deur"])["id"])
        await _herlaad_entiteiten(hass)

    @_veilig
    async def deur_openen_dienst(call: ServiceCall) -> None:
        await deur_openen(hass, _reg(hass).deur_op_naam(call.data["deur"]))

    @_veilig
    async def modus_zetten(call: ServiceCall) -> None:
        reg = _reg(hass)
        reg.modus_zetten(reg.deur_op_naam(call.data["deur"])["id"], call.data["modus"], door="ha")

    @_veilig
    async def persoon_toevoegen(call: ServiceCall) -> ServiceResponse:
        p = _reg(hass).persoon_toevoegen(
            call.data["naam"], call.data.get("pincode"), call.data.get("deuren"),
            call.data.get("van"), call.data.get("tot"), call.data.get("schema"))
        return {"persoon": _publiek(p)}

    @_veilig
    async def persoon_wijzigen(call: ServiceCall) -> ServiceResponse:
        reg = _reg(hass)
        p = reg.persoon_op_naam(call.data["persoon"])
        v = {k: call.data[k] for k in ("van", "tot", "schema", "actief", "deuren") if k in call.data}
        if "nieuwe_naam" in call.data:
            v["naam"] = call.data["nieuwe_naam"]
        if "pincode" in call.data:
            v["pin"] = call.data["pincode"]
        return {"persoon": _publiek(reg.persoon_wijzigen(p["id"], **v))}

    @_veilig
    async def persoon_verwijderen(call: ServiceCall) -> None:
        reg = _reg(hass)
        reg.persoon_verwijderen(reg.persoon_op_naam(call.data["persoon"])["id"])

    @_veilig
    async def personen(call: ServiceCall) -> ServiceResponse:
        reg = _reg(hass)
        return {"personen": [_publiek(p) for p in reg.data["personen"].values()],
                "deuren": list(reg.data["deuren"].values()),
                "tablets": [{k: t.get(k) for k in ("id", "naam", "deur_id", "app_versie", "gekoppeld", "laatst")}
                            for t in reg.data["tablets"].values()]}

    @_veilig
    async def tablet_koppelcode(call: ServiceCall) -> ServiceResponse:
        reg = _reg(hass)
        deur = reg.deur_op_naam(call.data["deur"])
        code = reg.koppelcode_maken(deur["id"], call.data.get("naam"))
        persistent_notification.async_create(
            hass,
            f"Koppelcode voor deur **{deur['naam']}**: **{code}**\n\nTien minuten geldig, één keer bruikbaar.",
            title="Btechnics Toegang", notification_id=f"{DOMAIN}_koppelcode",
        )
        return {"code": code, "deur": deur["naam"], "geldig_minuten": 10}

    @_veilig
    async def tablet_intrekken(call: ServiceCall) -> None:
        reg = _reg(hass)
        tid = call.data["tablet"]
        if tid not in reg.data["tablets"]:
            gevonden = [t["id"] for t in reg.data["tablets"].values() if t["naam"].lower() == tid.lower()]
            if len(gevonden) != 1:
                raise ToegangFout(f"Tablet {tid} niet gevonden")
            tid = gevonden[0]
        reg.tablet_intrekken(tid)
        await _herlaad_entiteiten(hass)

    def reg_dienst(domein, naam, fn, schema=None, supports_response=SupportsResponse.NONE):
        # enkel beheerders: deze diensten beheren pincodes en deuren
        async_register_admin_service(hass, domein, naam, fn, schema or vol.Schema({}), supports_response)
    reg_dienst(DOMAIN, "deur_toevoegen", deur_toevoegen,
               vol.Schema({vol.Required("naam"): cv.string, vol.Optional("open_entiteit"): cv.entity_id}),
               supports_response=SupportsResponse.OPTIONAL)
    reg_dienst(DOMAIN, "deur_wijzigen", deur_wijzigen,
               vol.Schema({vol.Required("deur"): cv.string, vol.Optional("naam"): cv.string,
                           vol.Optional("open_entiteit"): vol.Any("", cv.entity_id)}),
               supports_response=SupportsResponse.OPTIONAL)
    reg_dienst(DOMAIN, "deur_verwijderen", deur_verwijderen, vol.Schema({vol.Required("deur"): cv.string}))
    reg_dienst(DOMAIN, "deur_openen", deur_openen_dienst, vol.Schema({vol.Required("deur"): cv.string}))
    reg_dienst(DOMAIN, "modus_zetten", modus_zetten,
               vol.Schema({vol.Required("deur"): cv.string, vol.Required("modus"): vol.In(MODI)}))
    reg_dienst(DOMAIN, "persoon_toevoegen", persoon_toevoegen,
               vol.Schema({vol.Required("naam"): cv.string, **_PERSOON}),
               supports_response=SupportsResponse.OPTIONAL)
    reg_dienst(DOMAIN, "persoon_wijzigen", persoon_wijzigen,
               vol.Schema({vol.Required("persoon"): cv.string, vol.Optional("nieuwe_naam"): cv.string,
                           vol.Optional("actief"): cv.boolean, **_PERSOON}),
               supports_response=SupportsResponse.OPTIONAL)
    reg_dienst(DOMAIN, "persoon_verwijderen", persoon_verwijderen,
               vol.Schema({vol.Required("persoon"): cv.string}))
    reg_dienst(DOMAIN, "overzicht", personen, vol.Schema({}), supports_response=SupportsResponse.ONLY)
    reg_dienst(DOMAIN, "tablet_koppelcode", tablet_koppelcode,
               vol.Schema({vol.Required("deur"): cv.string, vol.Optional("naam"): cv.string}),
               supports_response=SupportsResponse.OPTIONAL)
    reg_dienst(DOMAIN, "tablet_intrekken", tablet_intrekken, vol.Schema({vol.Required("tablet"): cv.string}))
    async_registreer_ws(hass)
    await async_registreer_kaarten(hass)
    return True


async def _herlaad_entiteiten(hass: HomeAssistant) -> None:
    """Nieuwe of verwijderde deuren en tablets: entiteiten opnieuw opbouwen."""
    entry: ConfigEntry | None = hass.data[DOMAIN].get("entry")
    if entry:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    if "register" not in data:
        reg = Register(hass)
        await reg.async_laden()
        data["register"] = reg
    if not data.get("views"):
        registreer_views(hass)
        data["views"] = True
    data["entry"] = entry
    data["register"].bij_nieuwe_tablet = lambda: hass.config_entries.async_schedule_reload(entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop("entry", None)
    return ok
