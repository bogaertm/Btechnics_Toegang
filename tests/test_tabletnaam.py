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


async def test_toestellen_opgeruimd(hass, hass_client_no_auth):
    from homeassistant.helpers import device_registry as dr
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    t = await hass.services.async_call(DOMAIN, "testdeur_klaarzetten", {}, blocking=True, return_response=True)
    r = await client.post("/api/btechnics_toegang/koppel", json={"code": t["koppelcode"], "toestel_id": "x", "naam": "Testtablet", "app_versie": "1"})
    tid = (await r.json())["tablet_id"]
    await hass.async_block_till_done()
    dreg = dr.async_get(hass)
    # een oud toestel van een deur die al lang weg is
    oud = dreg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "d_weg")}, name="Deur weg")
    await hass.services.async_call(DOMAIN, "testdeur_opruimen", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    ids = {i[1] for d in dr.async_entries_for_config_entry(dreg, entry.entry_id) for i in d.identifiers}
    assert t["deur_id"] not in ids and tid not in ids and "d_weg" not in ids
    assert dreg.async_get(oud.id) is None
    assert t["deur_id"] not in hass.data[DOMAIN]["register"].data["deuren"]
