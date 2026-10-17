"""Te veel foute codes: melding, klavier_tot in de lijst, /open met code geweigerd tot het voorbij is."""
from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.util import dt as dt_util

from custom_components.btechnics_toegang.const import DOMAIN

API = "/api/btechnics_toegang"


async def test_te_veel_foute_codes(hass, hass_client_no_auth):
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    client = await hass_client_no_auth()
    t = await hass.services.async_call(DOMAIN, "testdeur_klaarzetten", {}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "deur_wijzigen", {"deur": t["deur_id"], "open_entiteit": "switch.test"}, blocking=True,
                                   return_response=True)
    r = await client.post(f"{API}/koppel", json={"code": t["koppelcode"], "toestel_id": "x", "naam": "T", "app_versie": "1"})
    kop = {"Authorization": f"Bearer {(await r.json())['sleutel']}"}
    events = []
    hass.bus.async_listen(f"{DOMAIN}_te_veel_pogingen", events.append)
    nu = dt_util.now().replace(microsecond=0)
    fout = lambda i: {"uid": f"f{i}", "tijd": (nu + timedelta(seconds=i)).isoformat(), "soort": "geweigerd", "methode": "pin",  # noqa: E731
                      "reden": "onbekend", "persoon_id": None}
    await client.post(f"{API}/log", headers=kop, json={"items": [fout(i) for i in range(4)]})
    await client.post(f"{API}/log", headers=kop, json={"items": [fout(i) for i in range(4)]})   # dubbel telt niet
    await hass.async_block_till_done()
    assert not events and (await (await client.get(f"{API}/lijst", headers=kop)).json())["deur"]["klavier_tot"] is None
    await client.post(f"{API}/log", headers=kop, json={"items": [fout(4)]})
    await hass.async_block_till_done()
    assert len(events) == 1 and events[0].data["aantal"] == 5
    assert any("te veel foute pogingen" in (n.get("title") or "") for n in hass.data["persistent_notification"].values())
    lijst = await (await client.get(f"{API}/lijst", headers=kop)).json()
    assert lijst["deur"]["klavier_tot"] > nu.isoformat()
    # HA weigert ook zelf een code zolang het klavier dicht is
    pid = t["personen"][0]["id"]
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": pid})
    assert r.status == 403 and (await r.json())["reden"] == "geblokkeerd"
    # na de blokkering weer normaal
    hass.data[DOMAIN]["register"].data["deuren"][t["deur_id"]]["klavier_tot"] = (nu - timedelta(seconds=1)).isoformat()
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": pid})
    assert r.status == 200
    assert (await (await client.get(f"{API}/lijst", headers=kop)).json())["deur"]["klavier_tot"] is None
