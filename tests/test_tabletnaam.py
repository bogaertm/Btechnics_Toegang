"""Tablet hernoemen vanuit HA."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_toegang.const import DOMAIN


async def test_tablet_hernoemen(hass, hass_client_no_auth):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    t = await hass.services.async_call(DOMAIN, "testdeur_klaarzetten", {}, blocking=True, return_response=True)
    r = await client.post("/api/btechnics_toegang/koppel", json={"code": t["koppelcode"], "toestel_id": "x", "naam": "Testdeur", "app_versie": "1"})
    tid = (await r.json())["tablet_id"]
    await hass.services.async_call(DOMAIN, "tablet_hernoemen", {"tablet": "Testdeur", "naam": "Deur Verdiep"}, blocking=True)
    reg = hass.data[DOMAIN]["register"]
    assert reg.data["tablets"][tid]["naam"] == "Deur Verdiep"
