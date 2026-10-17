import pytest
from homeassistant.exceptions import Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_toegang.const import DOMAIN


@pytest.fixture
async def opgezet(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass


async def test_ws_data(opgezet, hass_ws_client):
    hass = opgezet
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Vergaderzaal"}, blocking=True)
    await hass.services.async_call(DOMAIN, "persoon_toevoegen", {"naam": "Jan", "pincode": "4711", "deuren": ["Vergaderzaal"],
                                                                 "schema": [{"dagen": [1, 2], "van": "08:00", "tot": "17:00"}]}, blocking=True)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/data"})
    r = await ws.receive_json()
    assert r["success"]
    d = r["result"]
    assert d["deuren"][0]["naam"] == "Vergaderzaal"
    assert d["personen"][0]["naam"] == "Jan" and d["tablets"] == [] and d["log"] == []
    await ws.send_json({"id": 2, "type": f"{DOMAIN}/manage/list"})
    r = await ws.receive_json()
    e = r["result"]["entries"][0]
    assert e["secret"] == "4711" and e["schema"][0]["dagen"] == [1, 2] and e["status"] == "active"
    # wie een code bekijkt of deelt, staat in de wijzigingen (een keer per 5 minuten)
    for i, actie in enumerate(["bekeken", "bekeken", "gedeeld", "alle"]):
        await ws.send_json({"id": 10 + i, "type": f"{DOMAIN}/bekeken", "actie": actie, **({"entry": e["id"]} if actie != "alle" else {})})
        assert (await ws.receive_json())["success"]
    acties = [a["action"] for a in hass.data[DOMAIN]["register"].data["audit"]]
    assert acties.count("code bekeken") == 1 and acties.count("code gedeeld") == 1 and acties.count("alle codes bekeken") == 1
    await ws.send_json({"id": 20, "type": f"{DOMAIN}/beheerders"})
    r = await ws.receive_json()
    assert r["success"] and any(b["actief"] for b in r["result"]["beheerders"])
    # https en akuvox staan in de data
    await ws.send_json({"id": 21, "type": f"{DOMAIN}/data"})
    d = (await ws.receive_json())["result"]
    assert "https" in d and d["akuvox"] is None


async def test_diensten_enkel_beheerder(opgezet, hass_read_only_user):
    from homeassistant.core import Context
    hass = opgezet
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "X"}, blocking=True,
                                       context=Context(user_id=hass_read_only_user.id))


async def test_ws_enkel_beheerder(opgezet, hass_ws_client, hass_read_only_access_token):
    hass = opgezet
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/data"})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["code"] == "unauthorized"
