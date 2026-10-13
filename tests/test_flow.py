"""Volledige flow zoals een tablet hem doorloopt, tegen een echte (test) Home Assistant."""
import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.btechnics_toegang.const import DOMAIN
from custom_components.btechnics_toegang.hashing import hash_waarde

API = "/api/btechnics_toegang"


@pytest.fixture
async def opgezet(hass, hass_client_no_auth):
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    return hass, client


async def dienst(hass, _dienst, **data):
    from homeassistant.core import SupportsResponse
    antw = hass.services.supports_response(DOMAIN, _dienst) != SupportsResponse.NONE
    return await hass.services.async_call(DOMAIN, _dienst, data, blocking=True, return_response=antw)


async def koppel(hass, client, deur="Vergaderzaal"):
    code = (await dienst(hass, "tablet_koppelcode", deur=deur, naam="Tablet test"))["code"]
    r = await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "abc", "naam": "Tablet test",
                                                  "app_versie": "1.60"})
    assert r.status == 200
    antw = await r.json()
    await hass.async_block_till_done()
    return {"Authorization": f"Bearer {antw['sleutel']}"}, antw


async def test_volledige_flow(opgezet):
    hass, client = opgezet
    calls = async_mock_service(hass, "switch", "turn_on")
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal", open_entiteit="switch.relais_vergaderzaal")
    await hass.async_block_till_done()
    jan = (await dienst(hass, "persoon_toevoegen", naam="Jan Peeters", pincode="4711", deuren=["Vergaderzaal"]))["persoon"]
    await dienst(hass, "deur_toevoegen", naam="Kelder")
    await hass.async_block_till_done()
    await dienst(hass, "persoon_toevoegen", naam="Elders", pincode="9988", deuren=["Kelder"])
    assert "pin" not in jan and jan["pincode"] is True

    # zonder sleutel: 401
    assert (await client.get(f"{API}/lijst")).status == 401

    kop, antw = await koppel(hass, client)
    assert antw["deur_id"].startswith("d_")

    # lijst: enkel Jan, pincode gehasht met lengte
    r = await client.get(f"{API}/lijst", headers=kop)
    assert r.status == 200
    etag = r.headers["ETag"]
    lijst = await r.json()
    assert lijst["schema"] == 1 and lijst["deur"]["naam"] == "Vergaderzaal" and lijst["deur"]["modus"] == "open"
    assert [p["naam"] for p in lijst["personen"]] == ["Jan Peeters"]
    pin = lijst["personen"][0]["pin"]
    assert pin["lengte"] == 4 and len(pin["zout"]) == 24
    assert pin["hash"] == hash_waarde(pin["zout"], "4711")
    assert "4711" not in await r.text()

    # ongewijzigd: 304
    r = await client.get(f"{API}/lijst", headers={**kop, "If-None-Match": etag})
    assert r.status == 304

    # wacht: wordt wakker zodra HA de modus zet
    versie = lijst["versie"]
    taak = asyncio.ensure_future(client.get(f"{API}/wacht?versie={versie}", headers=kop))
    await asyncio.sleep(0.2)
    assert not taak.done()
    await dienst(hass, "modus_zetten", deur="Vergaderzaal", modus="vergadering")
    r = await asyncio.wait_for(taak, 5)
    assert r.status == 200 and (await r.json())["versie"] > versie
    assert hass.states.get("select.deur_vergaderzaal_modus").state == "vergadering"

    # modus vanaf de tablet
    r = await client.post(f"{API}/modus", headers=kop, json={"modus": "niet_storen"})
    assert r.status == 200
    await hass.async_block_till_done()
    assert hass.states.get("select.deur_vergaderzaal_modus").state == "niet_storen"
    assert (await client.post(f"{API}/modus", headers=kop, json={"modus": "beschikbaar"})).status == 400

    # logboek, dubbele uid wordt genegeerd
    events = []
    from homeassistant.core import callback
    hass.bus.async_listen(f"{DOMAIN}_toegang", callback(lambda e: events.append(e)))
    regel = {"uid": "u1", "tijd": "2026-10-11T17:06:12+02:00", "soort": "toegang", "methode": "pin",
             "persoon_id": jan["id"], "tekst": "Jan met pincode"}
    fout = {"uid": "u2", "tijd": "2026-10-11T17:07:00+02:00", "soort": "geweigerd", "methode": "pin",
            "reden": "onbekend", "persoon_id": None}
    r = await client.post(f"{API}/log", headers=kop, json={"items": [regel, fout]})
    assert (await r.json())["ontvangen"] == ["u1", "u2"]
    r = await client.post(f"{API}/log", headers=kop, json={"items": [regel]})
    assert (await r.json())["ontvangen"] == ["u1"]
    await hass.async_block_till_done()
    assert len(events) == 2 and {e.data["persoon"] for e in events} == {"Jan Peeters", "Foute code"}
    arch = hass.data[DOMAIN]["toegang"].archief
    rij = await hass.async_add_executor_job(arch.recent, antw["deur_id"], 10)
    assert len(rij) == 2 and {r["name"] for r in rij} == {"Jan Peeters", "Foute code"}

    # openen: Jan mag, onbekende niet, Elders niet (geen recht op deze deur)
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": jan["id"]})
    assert r.status == 200 and (await r.json())["geopend"] is True
    assert calls[-1].data["entity_id"] == "switch.relais_vergaderzaal"
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": None})
    assert r.status == 403 and (await r.json())["reden"] == "onbekend"
    elders = [p for p in hass.data[DOMAIN]["register"].data["personen"].values() if p["naam"] == "Elders"][0]
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": elders["id"]})
    assert r.status == 403
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "knop"})
    assert r.status == 200
    assert len(calls) == 2
    # HA schrijft zelf geen dubbele regel bij /open (de tablet logt zelf)
    assert len(await hass.async_add_executor_job(arch.recent, antw["deur_id"], 10)) == 2

    # status: tablet online
    r = await client.post(f"{API}/status", headers=kop, json={"app_versie": "1.60", "version_code": 54,
                                                               "versie": versie, "log_wachtrij": 0})
    assert r.status == 200
    await hass.async_block_till_done()
    online = [s for s in hass.states.async_all("binary_sensor") if s.entity_id.endswith("_online")]
    assert len(online) == 1 and online[0].state == "on"

    # de 30 s tik voor online/offline loopt in de event loop (geen thread waarschuwing)
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(online[0].entity_id).state == "on"

    # sensor laatste toegang
    laatste = hass.states.get("sensor.deur_vergaderzaal_laatste_toegang")
    assert laatste is not None and laatste.state != "unknown"

    # intrekken: 401
    tablet_id = antw["tablet_id"]
    await dienst(hass, "tablet_intrekken", tablet=tablet_id)
    await hass.async_block_till_done()
    assert (await client.get(f"{API}/lijst", headers=kop)).status == 401


async def test_koppelcode_eenmalig_en_fouten(opgezet):
    hass, client = opgezet
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal")
    code = (await dienst(hass, "tablet_koppelcode", deur="Vergaderzaal"))["code"]
    assert (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "x"})).status == 200
    assert (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "y"})).status == 403
    # na 5 foute pogingen vervallen alle open codes
    code2 = (await dienst(hass, "tablet_koppelcode", deur="Vergaderzaal"))["code"]
    fout = "000000" if code2 != "000000" else "111111"
    for _ in range(5):
        await client.post(f"{API}/koppel", json={"code": fout})
    assert (await client.post(f"{API}/koppel", json={"code": code2})).status == 403


async def test_pincode_uniek_en_validatie(opgezet):
    hass, _ = opgezet
    from homeassistant.exceptions import HomeAssistantError
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal")
    await dienst(hass, "persoon_toevoegen", naam="A", pincode="1234", deuren=["Vergaderzaal"])
    with pytest.raises(HomeAssistantError, match="in gebruik"):
        await dienst(hass, "persoon_toevoegen", naam="B", pincode="1234", deuren=["Vergaderzaal"])
    # geen bevestigingstoets op het klavier: de ene code mag niet het begin van de andere zijn
    with pytest.raises(HomeAssistantError, match="dezelfde cijfers"):
        await dienst(hass, "persoon_toevoegen", naam="B2", pincode="12345678", deuren=["Vergaderzaal"])
    await dienst(hass, "persoon_toevoegen", naam="B3", pincode="24681357", deuren=["Vergaderzaal"])
    with pytest.raises(HomeAssistantError, match="dezelfde cijfers"):
        await dienst(hass, "persoon_toevoegen", naam="B4", pincode="2468", deuren=["Vergaderzaal"])
    from custom_components.btechnics_toegang.register import ToegangFout
    reg = hass.data[DOMAIN]["register"]
    with pytest.raises(ToegangFout, match="niet gebruikt worden"):
        reg.controleer_pin("246813", streng=True)
    for _ in range(50):
        c = reg.nieuwe_code()
        assert not c.startswith("1234") and not "24681357".startswith(c)
    with pytest.raises(HomeAssistantError, match="4 tot 8"):
        await dienst(hass, "persoon_toevoegen", naam="C", pincode="12", deuren=["Vergaderzaal"])
    with pytest.raises(HomeAssistantError, match="onbekende deur"):
        await dienst(hass, "persoon_toevoegen", naam="D", pincode="5555", deuren=["Kelder"])


async def test_geblokkeerd_geweigerd(opgezet):
    hass, client = opgezet
    async_mock_service(hass, "switch", "turn_on")
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal", open_entiteit="switch.x")
    p = (await dienst(hass, "persoon_toevoegen", naam="Oud", pincode="2468", deuren=["Vergaderzaal"]))["persoon"]
    await dienst(hass, "persoon_blokkeren", persoon=p["id"])
    kop, _ = await koppel(hass, client)
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": p["id"]})
    assert r.status == 403 and (await r.json())["reden"] == "buiten_schema"
    r = await client.get(f"{API}/lijst", headers=kop)
    assert (await r.json())["personen"] == []
