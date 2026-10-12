"""Statussen, planner, gebruikersgrenzen, historiek en portaal (zoals Btechnics VTO)."""
import base64
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.btechnics_toegang.const import DOMAIN
from custom_components.btechnics_toegang.register import zwak

API = "/api/btechnics_toegang"
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()


@pytest.fixture
async def hass_klaar(hass):
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Vergaderzaal"}, blocking=True)
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Magazijn"}, blocking=True)
    await hass.async_block_till_done()
    return hass


def _reg(hass):
    return hass.data[DOMAIN]["register"]


async def _ws(client, i, **msg):
    await client.send_json({"id": i, **msg})
    return await client.receive_json()


def test_zwakke_codes():
    for c in ("111111", "123456", "654321", "121212", "123123", "123321", "112211"):
        assert zwak(c), c
    for c in ("583920", "740163"):
        assert not zwak(c), c


async def test_tijdelijke_code_en_planner(hass_klaar, hass_ws_client, freezer: FrozenDateTimeFactory):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    nu = dt_util.now()
    tot = (nu + timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="seconds")
    r = await _ws(ws, 1, type=f"{DOMAIN}/manage/action", action="add", name="Pakket 12/10", doors=[_deur(hass, "Vergaderzaal")], valid_until=tot)
    assert r["success"], r
    pid = r["result"]["result"]["id"]
    p = _reg(hass).data["personen"][pid]
    assert len(p["pin"]) == 6 and not zwak(p["pin"]) and p["soort"] == "tijdelijk" and p["status"] == "active"
    # tweede met dezelfde naam krijgt (2)
    r = await _ws(ws, 2, type=f"{DOMAIN}/manage/action", action="add", name="Pakket 12/10", doors=[_deur(hass, "Vergaderzaal")], valid_until=tot)
    assert _reg(hass).data["personen"][r["result"]["result"]["id"]]["naam"] == "Pakket 12/10 (2)"
    # na het einde: planner zet uit dienst
    freezer.tick(timedelta(hours=1, minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert p["status"] == "retired"
    assert _reg(hass).data["audit"][-1]["user"] == "planner"
    assert "einde geldigheid" in _reg(hass).data["audit"][-1]["detail"]
    # herstellen: geldigheid voorbij, dus gewist en actief
    r = await _ws(ws, 3, type=f"{DOMAIN}/manage/action", action="restore", entry=pid)
    assert r["success"] and p["status"] == "active" and p["valid_until"] is None


async def test_wacht_op_begin(hass_klaar, hass_ws_client, freezer: FrozenDateTimeFactory):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    nu = dt_util.now().replace(tzinfo=None)
    van = (nu + timedelta(hours=2)).isoformat(timespec="seconds")
    tot = (nu + timedelta(days=1)).isoformat(timespec="seconds")
    r = await _ws(ws, 1, type=f"{DOMAIN}/manage/action", action="add", name="Gast", doors=[_deur(hass, "Magazijn")],
                  valid_from=van, valid_until=tot)
    p = _reg(hass).data["personen"][r["result"]["result"]["id"]]
    assert r["result"]["result"]["scheduled"] and p["status"] == "blocked" and _reg(hass).wacht(p)
    assert _reg(hass).lijst_voor_deur(_deur(hass, "Magazijn"))["personen"] == []
    freezer.tick(timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert p["status"] == "active" and _reg(hass).data["audit"][-1]["action"] == "begin geldigheid"
    lijst = _reg(hass).lijst_voor_deur(_deur(hass, "Magazijn"))["personen"]
    assert lijst[0]["geldig_tot"] == p["valid_until"] and lijst[0]["tot"] == p["valid_until"][:10]


async def test_blokkeren_tot_en_deblokkeren(hass_klaar, hass_ws_client, freezer: FrozenDateTimeFactory):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/manage/action", action="add", name="Jan", code="583920", doors=[_deur(hass, "Magazijn")])
    pid = r["result"]["result"]["id"]
    p = _reg(hass).data["personen"][pid]
    tot = (dt_util.now() + timedelta(hours=3)).replace(tzinfo=None).isoformat(timespec="seconds")
    assert (await _ws(ws, 2, type=f"{DOMAIN}/manage/action", action="block", entry=pid, until=tot))["success"]
    assert p["status"] == "blocked"
    freezer.tick(timedelta(hours=3, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert p["status"] == "active" and _reg(hass).data["audit"][-1]["action"] == "automatisch gedeblokkeerd"
    # uit dienst, definitief verwijderen kan enkel daarna
    r = await _ws(ws, 3, type=f"{DOMAIN}/manage/action", action="forget", entry=pid)
    assert not r["success"]
    await _ws(ws, 4, type=f"{DOMAIN}/manage/action", action="retire", entry=pid)
    assert (await _ws(ws, 5, type=f"{DOMAIN}/manage/action", action="forget", entry=pid))["success"]
    assert pid not in _reg(hass).data["personen"]


async def test_gebruiker_grenzen(hass_klaar, hass_ws_client, hass_read_only_access_token, hass_read_only_user):
    hass = hass_klaar
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    deur = _deur(hass, "Vergaderzaal")
    nu = dt_util.now().replace(tzinfo=None)
    r = await _ws(ws, 1, type=f"{DOMAIN}/user/add", name="Technicus", doors=[deur], valid_until=(nu + timedelta(days=8)).isoformat())
    assert not r["success"] and "7 dagen" in r["error"]["message"]
    for i in range(10):
        r = await _ws(ws, 10 + i, type=f"{DOMAIN}/user/add", name="Gast", doors=[deur], valid_until=(nu + timedelta(days=1)).isoformat())
        assert r["success"], r
    r = await _ws(ws, 30, type=f"{DOMAIN}/user/add", name="Gast", doors=[deur], valid_until=(nu + timedelta(days=1)).isoformat())
    assert not r["success"] and "10 actieve" in r["error"]["message"]
    r = await _ws(ws, 31, type=f"{DOMAIN}/user/codes")
    assert len(r["result"]["entries"]) == 10 and all(e.get("telefoon") is None for e in r["result"]["entries"])
    eid = r["result"]["entries"][0]["id"]
    assert (await _ws(ws, 32, type=f"{DOMAIN}/user/stop", entry=eid))["success"]
    # beheerdersfuncties zijn niet toegankelijk
    assert not (await _ws(ws, 33, type=f"{DOMAIN}/manage/list"))["success"]
    assert not (await _ws(ws, 34, type=f"{DOMAIN}/history"))["success"]
    r = await _ws(ws, 35, type=f"{DOMAIN}/doors")
    assert r["success"] and r["result"]["doors"][0]["recent"] == [] and r["result"]["admin"] is False


async def test_historiek_en_overzicht(hass_klaar, hass_ws_client, hass_client_no_auth):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    reg = _reg(hass)
    p = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=["Vergaderzaal"])
    code = reg.koppelcode_maken(_deur(hass, "Vergaderzaal"))
    client = await hass_client_no_auth()
    k = await (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "x"})).json()
    kop = {"Authorization": f"Bearer {k['sleutel']}"}
    nu = dt_util.now().isoformat()
    items = [{"uid": f"u{i}", "tijd": nu, "soort": "toegang", "methode": "pin", "persoon_id": p["id"]} for i in range(3)]
    items += [{"uid": "x1", "tijd": nu, "soort": "geweigerd", "methode": "pin", "reden": "onbekend"},
              {"uid": "s1", "tijd": nu, "soort": "systeem", "tekst": "Deurpaneel gestart"}]
    assert (await client.post(f"{API}/log", headers=kop, json={"items": items})).status == 200
    r = await _ws(ws, 1, type=f"{DOMAIN}/history", days=7)
    h = r["result"]
    assert h["total"] == 4 and h["opened"] == 3 and h["refused"] == 1
    assert h["people"][0]["name"] == "Jan Peeters" and h["people"][0]["count"] == 3
    assert h["rows"][0]["method"] == "code"
    r = await _ws(ws, 2, type=f"{DOMAIN}/history", days=7, person="jan peeters")
    assert r["result"]["total"] == 3
    r = await _ws(ws, 3, type=f"{DOMAIN}/doors")
    d = [x for x in r["result"]["doors"] if x["name"] == "Vergaderzaal"][0]
    assert d["today"] == {"opened": 3, "refused": 1} and d["codes"] == 1 and d["tablets"] == 1
    assert reg.data["gebeurtenissen"][-1]["tekst"] == "Deurpaneel gestart"
    # naam wijzigen: historiek volgt
    await _ws(ws, 4, type=f"{DOMAIN}/manage/action", action="update", entry=p["id"], voornaam="Jan", achternaam="Peters")
    r = await _ws(ws, 5, type=f"{DOMAIN}/history", days=7, person="Jan Peters")
    assert r["result"]["total"] == 3


async def test_portaal(hass_klaar, hass_ws_client, hass_client_no_auth):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/uitnodiging", doors=[_deur(hass, "Vergaderzaal")], naam="Sofie",
                  schema=[{"dagen": [1, 2, 3, 4, 5], "van": "07:00", "tot": "19:00"}])
    assert r["success"], r
    url = r["result"]["url"]
    pad = url[url.index("/api/"):]
    client = await hass_client_no_auth()
    # pagina en info
    pg = await client.get(pad)
    assert pg.status == 200 and "Toegang" in await pg.text() and pg.headers["Referrer-Policy"] == "no-referrer"
    info = await (await client.get(pad + "/info")).json()
    assert info["soort"] == "uitnodiging" and info["deuren"] == ["Vergaderzaal"] and info["naam"] == "Sofie"
    # onvolledig, zwakke code, zonder foto
    basis = {"voornaam": "Sofie", "achternaam": "Claeys", "telefoon": "+32470000000", "email": "sofie@voorbeeld.be", "akkoord": True}
    r = await client.post(pad + "/aanmelden", json={**basis, "pin": "123456", "pin2": "123456", "foto": JPEG})
    assert r.status == 400 and "kan niet gebruikt worden" in (await r.json())["fout"]
    r = await client.post(pad + "/aanmelden", json={**basis, "pin": "740163", "pin2": "740163"})
    assert r.status == 400 and "foto" in (await r.json())["fout"]
    r = await client.post(pad + "/aanmelden", json={**basis, "pin": "740163", "pin2": "740163", "foto": JPEG})
    assert r.status == 200, await r.text()
    nieuw = (await r.json())["url"]
    eigen = nieuw[nieuw.index("/api/"):]
    p = [x for x in _reg(hass).data["personen"].values() if x["naam"] == "Sofie Claeys"][0]
    assert p["status"] == "active" and p["pin"] == "740163" and p["foto"] and p["schema"][0]["van"] == "07:00"
    assert p["bron"] == "portaal" and p["telefoon"] == "+32470000000"
    # uitnodiging is opgebruikt
    assert (await client.get(pad + "/info")).status == 410
    # eigen pagina
    info = await (await client.get(eigen + "/info")).json()
    assert info["soort"] == "persoon" and info["voornaam"] == "Sofie" and info["pin_lengte"] == 6 and info["pin"] == {"min": 4, "max": 8} and "740163" not in str(info)
    assert (await client.get(eigen + "/foto")).status == 200
    # wijzigen zonder juiste code mag niet
    r = await client.post(eigen + "/wijzig", json={"huidige_pin": "000000", "telefoon": "+32479999999", "email": "sofie@voorbeeld.be"})
    assert r.status == 400 and p["telefoon"] == "+32470000000"
    r = await client.post(eigen + "/wijzig", json={"huidige_pin": "740163", "telefoon": "+32479999999", "email": "sofie@voorbeeld.be",
                                                    "pin": "385027", "pin2": "385027"})
    assert r.status == 200 and p["telefoon"] == "+32479999999" and p["pin"] == "385027"
    # beheerder: nieuwe portaallink maakt de oude ongeldig
    r = await _ws(ws, 2, type=f"{DOMAIN}/portaal/link", entry=p["id"])
    assert r["success"] and (await client.get(eigen + "/info")).status == 404
    # onbekende link
    assert (await client.get(f"{API}/portaal/onzin/info")).status == 404
    # verwijderen wist de foto
    await hass.services.async_call(DOMAIN, "persoon_verwijderen", {"persoon": p["id"]}, blocking=True)
    import os
    assert not os.path.exists(hass.config.path("btechnics_toegang/fotos", f"{p['id']}.jpg"))


async def test_portaal_te_veel_fouten(hass_klaar, hass_ws_client, hass_client_no_auth):
    hass = hass_klaar
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/uitnodiging", doors=[_deur(hass, "Magazijn")])
    pad = r["result"]["url"][r["result"]["url"].index("/api/"):]
    client = await hass_client_no_auth()
    basis = {"voornaam": "A", "achternaam": "B", "telefoon": "1", "email": "a@b.be", "akkoord": True, "foto": JPEG}
    for _ in range(10):
        await client.post(pad + "/aanmelden", json={**basis, "pin": "111111", "pin2": "111111"})
    r = await client.post(pad + "/aanmelden", json={**basis, "pin": "740163", "pin2": "740163"})
    assert r.status == 400 and "geblokkeerd" in (await r.json())["fout"]


def _deur(hass, naam):
    return _reg(hass).deur_op_naam(naam)["id"]
