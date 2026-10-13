"""Btechnics Toegang: codebeheer voor de deurtablets (Btechnics Deur) vanuit Home Assistant.

Werking van codes, tijdelijke codes, statussen, planner, wijzigingen en historiek zoals Btechnics VTO.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .api import registreer_views
from .archief import Archief
from .const import ARCHIEF_DAGEN, DOMAIN, MODI
from .frontend import async_registreer_kaarten
from .portaal import foto_wissen, registreer_portaal
from .register import Register, ToegangFout
from .toegang import Toegang
from .websocket import async_registreer_ws, portaal_url

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SELECT, Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_BLOK = vol.Schema({vol.Required("dagen"): [vol.All(int, vol.Range(1, 7))],
                    vol.Required("van"): cv.string, vol.Required("tot"): cv.string})
_PERSOON = {
    vol.Optional("naam"): cv.string,
    vol.Optional("voornaam"): cv.string,
    vol.Optional("achternaam"): cv.string,
    vol.Optional("pincode"): cv.string,
    vol.Optional("deuren"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("geldig_van"): vol.Any(None, cv.string),
    vol.Optional("geldig_tot"): vol.Any(None, cv.string),
    vol.Optional("schema"): vol.Any(None, [_BLOK]),
    vol.Optional("telefoon"): cv.string,
    vol.Optional("email"): cv.string,
}


def _data(hass: HomeAssistant) -> dict:
    d = hass.data.get(DOMAIN, {})
    if "register" not in d:
        raise HomeAssistantError("Btechnics Toegang is niet geladen")
    return d


def _publiek(p: dict) -> dict:
    """Persoon zonder code of hashes, voor antwoorden van diensten."""
    return {k: p.get(k) for k in ("id", "naam", "voornaam", "achternaam", "soort", "status", "until", "valid_from",
                                  "valid_until", "deuren", "schema", "telefoon", "email")} | {"pincode": bool(p.get("pin"))}


async def _wie(hass: HomeAssistant, call: ServiceCall) -> str:
    uid = call.context.user_id
    if not uid:
        return "automatisch"
    u = await hass.auth.async_get_user(uid)
    return u.name if u and u.name else "onbekend"


def _veilig(fn):
    async def inner(call: ServiceCall):
        try:
            return await fn(call)
        except ToegangFout as e:
            raise HomeAssistantError(str(e)) from e
    return inner


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    reg_ = lambda: _data(hass)["register"]  # noqa: E731

    @_veilig
    async def deur_toevoegen(call: ServiceCall) -> ServiceResponse:
        d = reg_().deur_toevoegen(call.data["naam"], call.data.get("open_entiteit"), wie=await _wie(hass, call))
        _herlaad_entiteiten(hass)
        return {"deur": d}

    @_veilig
    async def deur_wijzigen(call: ServiceCall) -> ServiceResponse:
        reg = reg_()
        d = reg.deur_wijzigen(reg.deur_op_naam(call.data["deur"])["id"], call.data.get("naam"),
                              call.data.get("open_entiteit"), wie=await _wie(hass, call))
        _herlaad_entiteiten(hass)
        return {"deur": d}

    @_veilig
    async def deur_verwijderen(call: ServiceCall) -> None:
        reg = reg_()
        reg.deur_verwijderen(reg.deur_op_naam(call.data["deur"])["id"], wie=await _wie(hass, call))
        _herlaad_entiteiten(hass)

    @_veilig
    async def deur_openen(call: ServiceCall) -> None:
        d = _data(hass)
        deur = d["register"].deur_op_naam(call.data["deur"])
        wie = await _wie(hass, call)
        try:
            await d["toegang"].async_deur_openen(deur)
        except ToegangFout:
            raise
        except Exception as e:  # noqa: BLE001
            d["register"].audit(wie, "deur openen mislukt", deur["naam"], soort="deur", detail=str(e))
            raise HomeAssistantError(f"Openen mislukt: {e}") from e
        d["register"].audit(wie, "deur geopend op afstand", deur["naam"], soort="deur")
        d["register"].gewijzigd(lijst_veranderd=False)
        await d["toegang"].async_registreer(deur, methode="ha", persoon=None, opened=True, tekst="Deur openen in Home Assistant", wie=wie)

    @_veilig
    async def modus_zetten(call: ServiceCall) -> None:
        reg = reg_()
        reg.modus_zetten(reg.deur_op_naam(call.data["deur"])["id"], call.data["modus"], door="ha")

    @_veilig
    async def persoon_toevoegen(call: ServiceCall) -> ServiceResponse:
        c = call.data
        p = reg_().toevoegen(await _wie(hass, call), naam=c.get("naam", ""), voornaam=c.get("voornaam", ""),
                             achternaam=c.get("achternaam", ""), pin=c.get("pincode"), deuren=c.get("deuren"),
                             valid_from=c.get("geldig_van"), valid_until=c.get("geldig_tot"), schema=c.get("schema"),
                             telefoon=c.get("telefoon", ""), email=c.get("email", ""), soort="vast" if c.get("pincode") else None)
        return {"persoon": _publiek(p), "code": p["pin"]}

    @_veilig
    async def tijdelijke_code(call: ServiceCall) -> ServiceResponse:
        c = call.data
        p = reg_().toevoegen(await _wie(hass, call), naam=c["naam"], deuren=c["deuren"], valid_from=c.get("geldig_van"),
                             valid_until=c["geldig_tot"], soort="tijdelijk")
        return {"persoon": _publiek(p), "code": p["pin"]}

    @_veilig
    async def persoon_wijzigen(call: ServiceCall) -> ServiceResponse:
        reg, c = reg_(), call.data
        wie = await _wie(hass, call)
        p = reg.persoon_op_naam(c["persoon"])
        reg.bijwerken(wie, p["id"], naam=c.get("nieuwe_naam"), voornaam=c.get("voornaam"), achternaam=c.get("achternaam"),
                      pin=c.get("pincode"), deuren=c.get("deuren"), schema=c.get("schema"), telefoon=c.get("telefoon"),
                      email=c.get("email"))
        if "geldig_van" in c or "geldig_tot" in c:
            reg.geldigheid(wie, p["id"], c.get("geldig_van"), c.get("geldig_tot"))
        return {"persoon": _publiek(p)}

    def _statusdienst(methode: str):
        @_veilig
        async def inner(call: ServiceCall) -> None:
            reg = reg_()
            p = reg.persoon_op_naam(call.data["persoon"])
            wie = await _wie(hass, call)
            if methode == "blokkeren":
                reg.blokkeren(wie, p["id"], call.data.get("tot"))
            elif methode == "deblokkeren":
                reg.deblokkeren(wie, p["id"])
            elif methode == "uit_dienst":
                reg.uit_dienst(wie, p["id"])
            elif methode == "herstellen":
                reg.herstellen(wie, p["id"])
            elif methode == "verwijderen":
                reg.verwijderen(wie, p["id"])
                await foto_wissen(hass, p["id"])
        return inner

    @_veilig
    async def overzicht(call: ServiceCall) -> ServiceResponse:
        reg = reg_()
        return {"personen": [_publiek(p) for p in reg.data["personen"].values()],
                "deuren": list(reg.data["deuren"].values()),
                "tablets": [{k: t.get(k) for k in ("id", "naam", "deur_id", "app_versie", "gekoppeld", "laatst")}
                            for t in reg.data["tablets"].values()]}

    @_veilig
    async def tablet_koppelcode(call: ServiceCall) -> ServiceResponse:
        reg = reg_()
        deur = reg.deur_op_naam(call.data["deur"])
        code = reg.koppelcode_maken(deur["id"], call.data.get("naam"))
        persistent_notification.async_create(
            hass, f"Koppelcode voor deur **{deur['naam']}**: **{code}**\n\nTien minuten geldig, één keer bruikbaar.",
            title="Btechnics Toegang", notification_id=f"{DOMAIN}_koppelcode")
        return {"code": code, "deur": deur["naam"], "geldig_minuten": 10}

    @_veilig
    async def tablet_intrekken(call: ServiceCall) -> None:
        reg = reg_()
        tid = call.data["tablet"]
        if tid not in reg.data["tablets"]:
            hits = [t["id"] for t in reg.data["tablets"].values() if t["naam"].lower() == tid.lower()]
            if len(hits) != 1:
                raise ToegangFout(f"Tablet {tid} niet gevonden")
            tid = hits[0]
        reg.tablet_intrekken(tid, wie=await _wie(hass, call))
        _herlaad_entiteiten(hass)

    @_veilig
    async def uitnodiging_maken(call: ServiceCall) -> ServiceResponse:
        c = call.data
        u, token = reg_().uitnodiging_maken(await _wie(hass, call), deuren=c["deuren"], valid_from=c.get("geldig_van"),
                                            valid_until=c.get("geldig_tot"), schema=c.get("schema"), naam=c.get("naam", ""),
                                            dagen=c.get("dagen", 7))
        return {"url": portaal_url(hass, token), "verloopt": u["verloopt"]}

    @_veilig
    async def werk(call: ServiceCall) -> ServiceResponse:
        from .werk import Werk
        c = call.data
        res = Werk(hass, reg_()).uitvoeren(c["actie"], c.get("velden") or {}, c.get("door") or "")
        if c["actie"] in ("deur_toevoegen", "deur_hernoemen"):
            _herlaad_entiteiten(hass)
        return res

    def dienst(naam, fn, schema, antw=SupportsResponse.NONE):
        async_register_admin_service(hass, DOMAIN, naam, fn, schema, antw)

    persoon = vol.Schema({vol.Required("persoon"): cv.string})
    dienst("deur_toevoegen", deur_toevoegen, vol.Schema({vol.Required("naam"): cv.string, vol.Optional("open_entiteit"): cv.entity_id}),
           SupportsResponse.OPTIONAL)
    dienst("deur_wijzigen", deur_wijzigen, vol.Schema({vol.Required("deur"): cv.string, vol.Optional("naam"): cv.string,
                                                       vol.Optional("open_entiteit"): vol.Any("", cv.entity_id)}), SupportsResponse.OPTIONAL)
    dienst("deur_verwijderen", deur_verwijderen, vol.Schema({vol.Required("deur"): cv.string}))
    dienst("deur_openen", deur_openen, vol.Schema({vol.Required("deur"): cv.string}))
    dienst("modus_zetten", modus_zetten, vol.Schema({vol.Required("deur"): cv.string, vol.Required("modus"): vol.In(MODI)}))
    dienst("persoon_toevoegen", persoon_toevoegen, vol.Schema(_PERSOON), SupportsResponse.OPTIONAL)
    dienst("tijdelijke_code", tijdelijke_code, vol.Schema({vol.Required("naam"): cv.string, vol.Required("deuren"): vol.All(cv.ensure_list, [cv.string]),
                                                           vol.Optional("geldig_van"): cv.string, vol.Required("geldig_tot"): cv.string}),
           SupportsResponse.OPTIONAL)
    dienst("persoon_wijzigen", persoon_wijzigen, vol.Schema({vol.Required("persoon"): cv.string, vol.Optional("nieuwe_naam"): cv.string,
                                                             **{k: v for k, v in _PERSOON.items() if str(k) != "naam"}}),
           SupportsResponse.OPTIONAL)
    dienst("persoon_blokkeren", _statusdienst("blokkeren"), vol.Schema({vol.Required("persoon"): cv.string, vol.Optional("tot"): cv.string}))
    for naam in ("deblokkeren", "uit_dienst", "herstellen", "verwijderen"):
        dienst(f"persoon_{naam}", _statusdienst(naam), persoon)
    dienst("overzicht", overzicht, vol.Schema({}), SupportsResponse.ONLY)
    # koppeling met Btechnics Work via Btechnics IOT (zie werk.py)
    dienst("werk", werk, vol.Schema({vol.Required("actie"): cv.string, vol.Optional("velden"): dict,
                                     vol.Optional("door"): cv.string}), SupportsResponse.ONLY)
    dienst("tablet_koppelcode", tablet_koppelcode, vol.Schema({vol.Required("deur"): cv.string, vol.Optional("naam"): cv.string}),
           SupportsResponse.OPTIONAL)
    dienst("tablet_intrekken", tablet_intrekken, vol.Schema({vol.Required("tablet"): cv.string}))
    dienst("uitnodiging_maken", uitnodiging_maken, vol.Schema({vol.Required("deuren"): vol.All(cv.ensure_list, [cv.string]),
                                                               vol.Optional("naam"): cv.string, vol.Optional("geldig_van"): cv.string,
                                                               vol.Optional("geldig_tot"): cv.string, vol.Optional("schema"): [_BLOK],
                                                               vol.Optional("dagen", default=7): vol.All(int, vol.Range(1, 30))}),
           SupportsResponse.OPTIONAL)
    async_registreer_ws(hass)
    await async_registreer_kaarten(hass)
    try:
        from .errorlog import async_setup_errorlog
        await async_setup_errorlog(hass, DOMAIN)
    except Exception:  # noqa: BLE001  foutenlog is een hulpmiddel
        _LOGGER.debug("foutenlog niet gestart", exc_info=True)
    return True


def _herlaad_entiteiten(hass: HomeAssistant) -> None:
    entry: ConfigEntry | None = hass.data[DOMAIN].get("entry")
    if entry:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    if "register" not in data:
        reg = Register(hass)
        await reg.async_laden()
        archief = await hass.async_add_executor_job(Archief, hass.config.path(f"{DOMAIN}_toegang.db"))
        tg = Toegang(hass, reg, archief)
        await tg.async_start()
        data.update(register=reg, toegang=tg)
    if not data.get("views"):
        registreer_views(hass)
        registreer_portaal(hass)
        data["views"] = True
    data["entry"] = entry
    reg, tg = data["register"], data["toegang"]
    reg.bij_nieuwe_tablet = lambda: hass.config_entries.async_schedule_reload(entry.entry_id)

    async def _planner(_now=None):
        try:
            gedaan = reg.planner()
            if gedaan:
                _LOGGER.info("Planner: %s", "; ".join(gedaan))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Planner mislukt")

    async def _opruimen(_now=None):
        n = await hass.async_add_executor_job(tg.archief.prune, ARCHIEF_DAGEN)
        if n:
            _LOGGER.info("Archief: %d toegangen ouder dan %d dagen gewist", n, ARCHIEF_DAGEN)

    entry.async_on_unload(async_track_time_interval(hass, _planner, timedelta(minutes=1)))
    entry.async_on_unload(async_track_time_interval(hass, _opruimen, timedelta(hours=12)))
    await _planner()
    hass.async_create_task(_opruimen())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop("entry", None)
    return ok
