"""WebSocket-commando's voor de dashboardkaarten. Zelfde commando's en antwoorden als Btechnics VTO
(doors, history, manage/list, manage/action, user/*, errors), plus data voor het deurenbeheer en uitnodigingen."""
from __future__ import annotations

import time
from datetime import date, timedelta

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .const import API, ARCHIEF_DAGEN, DOMAIN, OFFLINE_NA, USER_MAX_ACTIVE, USER_MAX_AHEAD_DAYS, USER_MAX_DAYS
from .register import Register, ToegangFout, tijdstip


def _reg(hass) -> Register:
    return hass.data[DOMAIN]["register"]


def _admin(connection) -> bool:
    return bool(connection.user and connection.user.is_admin)


def _wie(connection) -> str:
    return (connection.user.name if connection.user and connection.user.name else "onbekend")


def _day_start(days_back: int = 0) -> int:
    return int(dt_util.start_of_local_day(dt_util.now().date() - timedelta(days=days_back)).timestamp())


def _date_start(value: str, extra_days: int = 0) -> int:
    return int(dt_util.start_of_local_day(date.fromisoformat(value) + timedelta(days=extra_days)).timestamp())


def portaal_url(hass, token: str) -> str:
    try:
        basis = get_url(hass, prefer_external=True, allow_internal=True)
    except NoURLAvailableError:
        basis = ""
    return f"{basis}{API}/portaal/{token}"


@callback
def async_registreer_ws(hass: HomeAssistant) -> None:
    for cmd in (ws_doors, ws_history, ws_manage_list, ws_manage_action, ws_user_codes, ws_user_add, ws_user_stop,
                ws_user_days, ws_errors, ws_data, ws_uitnodiging, ws_portaal_link):
        websocket_api.async_register_command(hass, cmd)


# ---------------------------------------------------------------- overzicht

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/doors"})
@websocket_api.async_response
async def ws_doors(hass, connection, msg):
    reg, tg = _reg(hass), hass.data[DOMAIN]["toegang"]
    archief = tg.archief
    today = await hass.async_add_executor_job(archief.counts_since, _day_start())
    stats = await hass.async_add_executor_job(archief.stats)
    ent = er.async_get(hass)
    doors = []
    for d in sorted(reg.data["deuren"].values(), key=lambda x: x["naam"].lower()):
        tabs = [t for t in reg.data["tablets"].values() if t["deur_id"] == d["id"]]
        online = [t for t in tabs if reg.tablet_online(t, OFFLINE_NA)]
        recent = tg.recent.get(d["id"], [])
        doors.append({
            "id": d["id"], "name": d["naam"], "available": bool(online),
            "last_unlock": recent[0] if recent else None, "recent": recent[:10],
            "codes": sum(1 for p in reg.data["personen"].values() if p["status"] == "active" and d["id"] in p["deuren"]),
            "cards": len(online), "tablets": len(tabs), "modus": d["modus"], "can_open": bool(d.get("open_entiteit")),
            "today": today.get(d["id"], {"opened": 0, "refused": 0}),
            "entities": {"last_unlock": ent.async_get_entity_id("sensor", DOMAIN, f"{d['id']}_laatste")},
        })
    admin = _admin(connection)
    if not admin:
        for d in doors:
            d.update(last_unlock=None, recent=[], codes=None, cards=None)
        stats = {}
    connection.send_result(msg["id"], {"doors": doors, "archive": stats, "time_zone": hass.config.time_zone, "admin": admin})


# ---------------------------------------------------------------- historiek

@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/history",
    vol.Optional("door_ids"): [str],
    vol.Optional("search"): str,
    vol.Optional("person"): str,
    vol.Optional("status", default="all"): vol.In(["all", "opened", "refused"]),
    vol.Optional("start"): vol.Coerce(int),
    vol.Optional("end"): vol.Coerce(int),
    vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=800)),
    vol.Optional("date_from"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("date_to"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("limit", default=200): vol.All(vol.Coerce(int), vol.Range(min=0, max=50000)),
    vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional("max_id"): vol.All(vol.Coerce(int), vol.Range(min=0)),
})
@websocket_api.async_response
async def ws_history(hass, connection, msg):
    archief = hass.data[DOMAIN]["toegang"].archief
    kw = {k: msg[k] for k in ("door_ids", "search", "person", "status", "start", "end", "limit", "offset", "max_id") if k in msg}
    try:
        if "days" in msg:
            kw["start"] = _day_start(msg["days"] - 1)
        if "date_from" in msg:
            kw["start"] = _date_start(msg["date_from"])
        if "date_to" in msg:
            kw["end"] = _date_start(msg["date_to"], 1)
    except ValueError as e:
        connection.send_error(msg["id"], "invalid_format", f"ongeldige datum: {e}")
        return
    t0 = time.monotonic()
    tz = dt_util.get_default_time_zone()
    res = await hass.async_add_executor_job(lambda: archief.query(tz, **kw))
    res["query_ms"] = int((time.monotonic() - t0) * 1000)
    connection.send_result(msg["id"], res)


# ---------------------------------------------------------------- codes (beheerders)

async def _owner_names(hass, uids) -> dict:
    out = {}
    for uid in {u for u in uids if u}:
        u = await hass.auth.async_get_user(uid)
        out[uid] = u.name if u and u.name else "onbekend"
    return out


def _entry_out(reg: Register, p: dict, admin: bool = True) -> dict:
    names = {d: reg.data["deuren"].get(d, {}).get("naam", d) for d in p["deuren"]}
    deuren = sorted(({"id": d, "name": names[d]} for d in p["deuren"]), key=lambda x: x["name"].lower())
    out = {
        "id": p["id"], "kind": "code", "name": p["naam"], "secret": p.get("pin") or "", "status": p["status"],
        "until": p.get("until"), "valid_from": p.get("valid_from"), "valid_until": p.get("valid_until"),
        "max_uses": None, "uses": 0, "source": p.get("bron"), "created": p.get("gemaakt"), "updated": p.get("gewijzigd"),
        # zoals Btechnics VTO: doors = werkt nu op die deuren, stored = bewaard (geblokkeerd, wachtend, uit dienst)
        "doors": deuren if p["status"] == "active" else [], "stored": [] if p["status"] == "active" else deuren,
        "soort": p.get("soort"), "schema": p.get("schema") or [],
    }
    if admin:
        out.update(voornaam=p.get("voornaam", ""), achternaam=p.get("achternaam", ""), telefoon=p.get("telefoon", ""),
                   email=p.get("email", ""), foto=bool(p.get("foto")), foto_v=p.get("foto_v", 0),
                   portaal=bool(p.get("portaal_hash")), kaarten=len(p.get("kaarten") or []))
    return out


def _doors(reg: Register) -> list:
    return [{"id": d["id"], "name": d["naam"]} for d in sorted(reg.data["deuren"].values(), key=lambda x: x["naam"].lower())]


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/manage/list"})
@websocket_api.async_response
async def ws_manage_list(hass, connection, msg):
    reg = _reg(hass)
    owners = await _owner_names(hass, [p.get("owner") for p in reg.data["personen"].values()])
    entries = []
    for p in reg.data["personen"].values():
        e = _entry_out(reg, p)
        if p.get("owner"):
            e["owner_name"] = owners.get(p["owner"], "onbekend")
        entries.append(e)
    entries.sort(key=lambda e: e["name"].lower())
    nu = dt_util.now().isoformat()
    invites = [{"id": u["id"], "naam": u["naam"], "deuren": [reg.data["deuren"].get(d, {}).get("naam", d) for d in u["deuren"]],
                "verloopt": u["verloopt"], "gemaakt": u["gemaakt"], "door": u.get("door"),
                "valid_from": u.get("valid_from"), "valid_until": u.get("valid_until")}
               for u in reg.data["uitnodigingen"].values() if not u.get("gebruikt") and u["verloopt"] > nu]
    connection.send_result(msg["id"], {"doors": _doors(reg), "entries": entries,
                                       "audit": list(reversed(reg.data["audit"][-200:])), "unknown_cards": [],
                                       "invites": invites, "pin": {"min": 4, "max": 8}})


ACTIES = ["add", "validity", "update", "block", "unblock", "retire", "restore", "forget", "remove"]


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manage/action",
    vol.Required("action"): vol.In(ACTIES),
    vol.Optional("entry"): str,
    vol.Optional("name"): str,
    vol.Optional("voornaam"): str,
    vol.Optional("achternaam"): str,
    vol.Optional("telefoon"): str,
    vol.Optional("email"): str,
    vol.Optional("code"): str,
    vol.Optional("doors"): [str],
    vol.Optional("schema"): list,
    vol.Optional("until"): str,
    vol.Optional("valid_from"): str,
    vol.Optional("valid_until"): str,
})
@websocket_api.async_response
async def ws_manage_action(hass, connection, msg):
    reg, wie, a = _reg(hass), _wie(connection), msg["action"]
    leeg = lambda k: msg.get(k) or None  # noqa: E731
    try:
        res = None
        if a == "add":
            p = reg.toevoegen(wie, naam=msg.get("name", ""), voornaam=msg.get("voornaam", ""), achternaam=msg.get("achternaam", ""),
                              pin=leeg("code"), deuren=msg.get("doors"), valid_from=leeg("valid_from"),
                              valid_until=leeg("valid_until"), schema=msg.get("schema"), telefoon=msg.get("telefoon", ""),
                              email=msg.get("email", ""), soort=None if not msg.get("code") else "vast")
            res = {"id": p["id"], "code": p["pin"], "scheduled": p["status"] == "blocked"}
        else:
            pid = msg.get("entry") or ""
            oud_naam = reg.persoon(pid)["naam"]
            if a == "validity":
                reg.geldigheid(wie, pid, leeg("valid_from"), leeg("valid_until"))
            elif a == "update":
                p = reg.bijwerken(wie, pid, naam=msg.get("name"), voornaam=msg.get("voornaam"), achternaam=msg.get("achternaam"),
                                  pin=leeg("code"), deuren=msg.get("doors"), schema=msg.get("schema"),
                                  telefoon=msg.get("telefoon"), email=msg.get("email"))
                if p["naam"] != oud_naam:
                    await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.hernoem_persoon, pid, p["naam"])
            elif a == "block":
                reg.blokkeren(wie, pid, leeg("until"))
            elif a == "unblock":
                reg.deblokkeren(wie, pid)
            elif a == "retire":
                reg.uit_dienst(wie, pid)
            elif a == "restore":
                reg.herstellen(wie, pid)
            elif a == "forget":
                reg.vergeten(wie, pid)
            elif a == "remove":
                reg.verwijderen(wie, pid)
            res = {"id": pid}
    except ToegangFout as e:
        connection.send_error(msg["id"], "failed", str(e))
        return
    connection.send_result(msg["id"], {"ok": True, "result": res})


# ---------------------------------------------------------------- gebruikers zonder beheerrechten

def _user_error(e, connection) -> str:
    txt = str(e)
    if _admin(connection) or "tijdelijke codes" in txt or "verleden" in txt or "na het begin" in txt:
        return txt
    if "onbekende deur" in txt:
        return "onbekende deur"
    return "niet gelukt; probeer opnieuw of vraag het aan een beheerder"


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/user/codes"})
@callback
def ws_user_codes(hass, connection, msg):
    reg = _reg(hass)
    recent = (dt_util.now() - timedelta(days=7)).isoformat()
    entries = []
    if connection.user:
        for p in reg.data["personen"].values():
            if p.get("owner") != connection.user.id:
                continue
            if p["status"] == "retired" and (p.get("gewijzigd") or "") < recent:
                continue
            entries.append(_entry_out(reg, p, admin=False))
    entries.sort(key=lambda e: (e["status"] == "retired", e.get("valid_until") or "", e["name"].lower()))
    connection.send_result(msg["id"], {"doors": _doors(reg), "entries": entries, "audit": [], "unknown_cards": [],
                                       "admin": _admin(connection),
                                       "limits": {"active": USER_MAX_ACTIVE, "days": USER_MAX_DAYS, "ahead_days": USER_MAX_AHEAD_DAYS}})


def _local_span(end, start):
    return dt_util.as_local(end).replace(tzinfo=None) - dt_util.as_local(start).replace(tzinfo=None)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/user/add",
    vol.Required("name"): vol.All(str, vol.Length(min=1, max=30)),
    vol.Required("doors"): vol.All([str], vol.Length(min=1)),
    vol.Optional("valid_from"): str,
    vol.Required("valid_until"): str,
})
@callback
def ws_user_add(hass, connection, msg):
    reg = _reg(hass)
    if not connection.user:
        connection.send_error(msg["id"], "not_ready", "nog niet klaar")
        return
    try:
        vuntil = tijdstip(msg["valid_until"])
        vfrom = tijdstip(msg["valid_from"]) if msg.get("valid_from") else None
    except ToegangFout as e:
        connection.send_error(msg["id"], "invalid_format", str(e))
        return
    now = dt_util.now()
    start = max(now, vfrom) if vfrom else now
    err = None
    if vuntil <= now:
        err = "het einde ligt in het verleden"
    elif vfrom and vuntil <= vfrom:
        err = "het einde moet na het begin liggen"
    elif _local_span(vuntil, start) > timedelta(days=USER_MAX_DAYS, minutes=1):
        err = f"een tijdelijke code is maximaal {USER_MAX_DAYS} dagen geldig"
    elif vfrom and vfrom - now > timedelta(days=USER_MAX_AHEAD_DAYS):
        err = f"het begin mag maximaal {USER_MAX_AHEAD_DAYS} dagen vooruit liggen"
    if err:
        connection.send_error(msg["id"], "failed", err)
        return
    try:
        p = reg.toevoegen(_wie(connection), naam=msg["name"].strip(), deuren=msg["doors"], valid_until=vuntil,
                          valid_from=vfrom if vfrom and vfrom > now else None, soort="tijdelijk",
                          owner=connection.user.id, owner_limit=USER_MAX_ACTIVE, bron="gebruiker")
    except ToegangFout as e:
        connection.send_error(msg["id"], "failed", _user_error(e, connection))
        return
    connection.send_result(msg["id"], {"ok": True, "id": p["id"], "result": {"id": p["id"]}})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/user/stop", vol.Required("entry"): str})
@callback
def ws_user_stop(hass, connection, msg):
    reg = _reg(hass)
    p = reg.data["personen"].get(msg["entry"])
    if p is None or not connection.user or (p.get("owner") != connection.user.id and not _admin(connection)):
        connection.send_error(msg["id"], "not_found", "onbekende code")
        return
    if p["status"] == "retired":
        connection.send_error(msg["id"], "failed", "deze code is al gestopt")
        return
    reg.uit_dienst(_wie(connection), p["id"], "gestopt")
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/user/days",
    vol.Optional("door_ids"): [str],
    vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=400)),
    vol.Optional("date_from"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
    vol.Optional("date_to"): vol.Match(r"^\d{4}-\d{2}-\d{2}$"),
})
@websocket_api.async_response
async def ws_user_days(hass, connection, msg):
    archief = hass.data[DOMAIN]["toegang"].archief
    kw = {"limit": 0}
    if msg.get("door_ids"):
        kw["door_ids"] = msg["door_ids"]
    try:
        kw["start"] = _date_start(msg["date_from"]) if "date_from" in msg else _day_start(msg.get("days", 30) - 1)
        if "date_to" in msg:
            kw["end"] = _date_start(msg["date_to"], 1)
    except ValueError as e:
        connection.send_error(msg["id"], "invalid_format", f"ongeldige datum: {e}")
        return
    kw["start"] = max(kw["start"], _day_start(ARCHIEF_DAGEN - 1))
    tz = dt_util.get_default_time_zone()
    r = await hass.async_add_executor_job(lambda: archief.query(tz, **kw))
    connection.send_result(msg["id"], {
        "total": r["total"], "opened": r["opened"], "refused": r["refused"],
        "days": [{"day": d["day"], "count": d["count"], "opened": d["opened"], "refused": d["refused"], "doors": d["doors"]}
                 for d in r["days"]]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/errors",
                                  vol.Optional("days", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=60))})
@websocket_api.async_response
async def ws_errors(hass, connection, msg):
    log = hass.data.get(f"{DOMAIN}_errorlog")
    if log is None:
        connection.send_error(msg["id"], "not_ready", "foutenlog niet beschikbaar")
        return
    await hass.async_add_executor_job(log.flush_to_db)
    connection.send_result(msg["id"], await hass.async_add_executor_job(log.query, msg["days"]))


# ---------------------------------------------------------------- deurenbeheer en uitnodigingen

@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/data", vol.Optional("log", default=100): int})
@callback
def ws_data(hass, connection, msg):
    reg = _reg(hass)
    d = reg.data
    tablets = [{"id": t["id"], "naam": t["naam"], "deur_id": t["deur_id"], "app_versie": t.get("app_versie"),
                "gekoppeld": t.get("gekoppeld"), "laatst": t.get("laatst"), "online": reg.tablet_online(t, OFFLINE_NA),
                "status": t.get("status") or {}} for t in d["tablets"].values()]
    deuren = sorted(d["deuren"].values(), key=lambda x: x["naam"].lower())
    gebeurtenissen = list(reversed(d["gebeurtenissen"][-max(1, min(msg["log"], 500)):]))
    connection.send_result(msg["id"], {"deuren": deuren, "tablets": tablets, "log": gebeurtenissen, "versie": d["versie"],
                                       "personen": [{"id": p["id"], "naam": p["naam"], "deuren": p["deuren"], "status": p["status"]}
                                                    for p in d["personen"].values()]})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/uitnodiging",
    vol.Optional("actie", default="maken"): vol.In(["maken", "intrekken"]),
    vol.Optional("invite"): str,
    vol.Optional("naam"): str,
    vol.Optional("doors"): [str],
    vol.Optional("schema"): list,
    vol.Optional("valid_from"): str,
    vol.Optional("valid_until"): str,
    vol.Optional("dagen", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
})
@callback
def ws_uitnodiging(hass, connection, msg):
    reg = _reg(hass)
    try:
        if msg["actie"] == "intrekken":
            reg.uitnodiging_intrekken(_wie(connection), msg.get("invite") or "")
            connection.send_result(msg["id"], {"ok": True})
            return
        u, token = reg.uitnodiging_maken(_wie(connection), deuren=msg.get("doors") or [], valid_from=msg.get("valid_from") or None,
                                         valid_until=msg.get("valid_until") or None, schema=msg.get("schema"),
                                         naam=msg.get("naam", ""), dagen=msg["dagen"])
    except ToegangFout as e:
        connection.send_error(msg["id"], "failed", str(e))
        return
    connection.send_result(msg["id"], {"ok": True, "id": u["id"], "url": portaal_url(hass, token), "verloopt": u["verloopt"]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/portaal/link", vol.Required("entry"): str})
@callback
def ws_portaal_link(hass, connection, msg):
    reg = _reg(hass)
    try:
        token = reg.portaal_token(_wie(connection), msg["entry"])
    except ToegangFout as e:
        connection.send_error(msg["id"], "failed", str(e))
        return
    connection.send_result(msg["id"], {"ok": True, "url": portaal_url(hass, token)})
