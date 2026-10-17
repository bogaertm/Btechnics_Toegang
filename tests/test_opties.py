"""Instellingen: enkel https aanzetten zonder Akuvox maakt geen deur aan, en http koppel geeft dan 403."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_toegang.const import DOMAIN


async def test_enkel_https_zonder_akuvox(hass, hass_client_no_auth):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    deuren = set(hass.data[DOMAIN]["register"].data["deuren"])
    f = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(f["flow_id"], {"akuvox_aan": False, "akuvox_deur": "__nieuw__",
                                                                        "https_poort": 18444, "enkel_https": True})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["enkel_https"] is True and entry.options["akuvox_deur"] == ""
    assert set(hass.data[DOMAIN]["register"].data["deuren"]) == deuren
    client = await hass_client_no_auth()
    r = await client.post("/api/btechnics_toegang/koppel", json={"code": "123456"})
    assert r.status == 403
