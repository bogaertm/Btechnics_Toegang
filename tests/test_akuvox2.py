"""Twee Akuvox toestellen: elk eigen deur, eigen gebruikers vanaf 700001, eigen Action URL en eigen relais."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_toegang.const import DOMAIN

from .test_akuvox import NepAkuvox

IP1, IP2 = "10.9.8.1", "10.9.8.2"


async def test_twee_toestellen(hass, aioclient_mock, hass_client_no_auth):
    await hass.config.async_set_time_zone("Europe/Brussels")
    nep = {IP1: NepAkuvox(), IP2: NepAkuvox()}
    for ip, n in nep.items():
        aioclient_mock.get(f"https://{ip}/api/user/get", side_effect=n.get)
        aioclient_mock.get(f"https://{ip}/api/doorlog/get", side_effect=n.get)
        for a in ("add", "set", "del"):
            aioclient_mock.post(f"https://{ip}/api/user/{a}", side_effect=n.post)
        aioclient_mock.post(f"https://{ip}/api/relay/trig", json={"retcode": 0})
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = hass.data[DOMAIN]["register"]
    d1, d2 = reg.deur_toevoegen("Magazijn", wie="t"), reg.deur_toevoegen("Achterpoort", wie="t")
    hass.config_entries.async_update_entry(entry, options={
        "akuvox_aan": True, "akuvox_ip": IP1, "akuvox_gebruiker": "a", "akuvox_wachtwoord": "b", "akuvox_deur": d1["id"],
        "akuvox2_aan": True, "akuvox2_ip": IP2, "akuvox2_gebruiker": "a", "akuvox2_wachtwoord": "b", "akuvox2_deur": d2["id"]})
    await hass.async_block_till_done()
    reg = hass.data[DOMAIN]["register"]
    aks = hass.data[DOMAIN]["akuvoxen"]
    assert [a.aan for a in aks] == [True, True]
    reg.toevoegen("t", naam="Beide Deuren", pin="583920", deuren=[d1["id"], d2["id"]])
    reg.toevoegen("t", naam="Enkel Achter", pin="472910", deuren=[d2["id"]])
    r = await hass.services.async_call(DOMAIN, "akuvox_sync", {}, blocking=True, return_response=True)
    assert len(r["alle"]) == 2
    namen = lambda ip: sorted(u["Name"] for u in nep[ip].users.values() if int(u["UserID"]) >= 700001 and u["Name"] != "Vreemd")  # noqa: E731
    assert namen(IP1) == ["Beide Deuren"] and namen(IP2) == ["Beide Deuren", "Enkel Achter"]
    assert reg.data["akuvox"]["webhook"] != reg.data["akuvox2"]["webhook"]
    # deur openen op toestel 2 stuurt het relais van toestel 2
    await hass.services.async_call(DOMAIN, "deur_openen", {"deur": d2["id"]}, blocking=True)
    assert any(str(c[1]) == f"https://{IP2}/api/relay/trig" for c in aioclient_mock.mock_calls)
    assert not any(str(c[1]) == f"https://{IP1}/api/relay/trig" for c in aioclient_mock.mock_calls)
    # Action URL van toestel 2 werkt
    client = await hass_client_no_auth()
    assert (await client.get(f"/api/webhook/{reg.data['akuvox2']['webhook']}")).status == 200
    await hass.async_block_till_done()
    assert reg.data["akuvox2"].get("melding_laatst") and not reg.data["akuvox"].get("melding_laatst")
    # instellingen: zelfde deur voor beide toestellen wordt geweigerd
    f = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(f["flow_id"], {**entry.options, "akuvox2_deur": d1["id"]})
    assert r["type"] == "form" and r["errors"]["base"] == "akuvox"
