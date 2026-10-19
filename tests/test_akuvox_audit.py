"""Audit 19/10: de sync controleert op het toestel zelf, raakt nooit vreemde gebruikers aan en ziet codeconflicten."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.util import dt as dt_util

from custom_components.btechnics_toegang.const import DOMAIN
from custom_components.btechnics_toegang.register import ToegangFout

from .test_akuvox import IP, NepAkuvox


async def _opzet(hass, aioclient_mock, nep):
    await hass.config.async_set_time_zone("Europe/Brussels")
    aioclient_mock.get(f"https://{IP}/api/user/get", side_effect=nep.get)
    aioclient_mock.get(f"https://{IP}/api/doorlog/get", side_effect=nep.get)
    for a in ("add", "set", "del"):
        aioclient_mock.post(f"https://{IP}/api/user/{a}", side_effect=nep.post)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = hass.data[DOMAIN]["register"]
    deur = reg.deur_toevoegen("Voordeur", wie="test")
    hass.config_entries.async_update_entry(entry, options={"akuvox_aan": True, "akuvox_ip": IP, "akuvox_gebruiker": "api",
                                                           "akuvox_wachtwoord": "geheim", "akuvox_deur": deur["id"]})
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["register"], hass.data[DOMAIN]["akuvox"], deur


async def test_toestel_neemt_rechten_niet_over(hass, aioclient_mock):
    """De fout van 18/10: het toestel aanvaardt de gebruiker maar zonder schema. Dat moet een fout en melding geven."""
    nep = NepAkuvox(schema_negeren=True)
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]])
    v = await ak.async_sync()
    assert v["fouten"] and "schema/relais" in v["fouten"][0]
    assert ak.status["ok"] is False and ak.status["eigen"] == 0
    meldingen = hass.states.async_all("persistent_notification") or []
    from homeassistant.components import persistent_notification
    assert any("Akuvox klopt niet" in (n.get("title") or "") for n in persistent_notification._async_get_or_create_notifications(hass).values())
    # toestel hersteld (schema wordt wel overgenomen): de volgende sync zet het recht en meldt in orde
    nep.schema_negeren = False
    v = await ak.async_sync()
    assert v["gewijzigd"] == ["Jan Peeters"] and ak.status["ok"] and ak.status["eigen"] == 1


async def test_vrijgekomen_nummer_van_ander_nooit_aanraken(hass, aioclient_mock):
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    jan = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]])
    await ak.async_sync()
    reg.blokkeren("test", jan["id"])
    await ak.async_sync()
    # iemand anders krijgt op het toestel zelf 700001
    nep.n += 1
    nep.users[str(nep.n)] = {"ID": str(nep.n), "Name": "Piet Anders", "UserID": "700001", "PrivatePIN": "4444", "Source": "Local",
                             "ScheduleRelay": "1001-12;", "RelayScheduleActivationPermission": "12", "Group": "Default"}
    reg.deblokkeren("test", jan["id"])
    v = await ak.async_sync()
    piet = [u for u in nep.users.values() if u["Name"] == "Piet Anders"]
    assert piet and piet[0]["PrivatePIN"] == "4444" and piet[0]["UserID"] == "700001"   # onaangeroerd
    nieuw = [u for u in nep.users.values() if u["Name"] == "Jan Peeters"]
    assert nieuw and nieuw[0]["UserID"] != "700001" and v["toegevoegd"] == ["Jan Peeters"] and ak.status["ok"]
    # blokkeren wist enkel Jan, nooit Piet
    reg.blokkeren("test", jan["id"])
    await ak.async_sync()
    assert [u["Name"] for u in nep.users.values() if u["Name"] in ("Jan Peeters", "Piet Anders")] == ["Piet Anders"]


async def test_codeconflict_met_smartplus(hass, aioclient_mock):
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    await ak.async_sync()   # leert de codes van vreemde gebruikers (als hash)
    try:
        reg.toevoegen("test", naam="Piet", pin="1111", deuren=[deur["id"]])   # 1111 = Cloud Jan op het toestel
        raise AssertionError("conflict niet gezien")
    except ToegangFout as e:
        assert "Akuvox" in str(e)
    assert all(not (c.isdigit() and c == "1111") for c in [reg.nieuwe_code(4) for _ in range(50)])


async def test_toestel_weigert_eigen_gebruiker_geeft_alarm(hass, aioclient_mock):
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]])
    await ak.async_sync()
    nu = dt_util.now()
    nep.log = [{"ID": "10", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "x", "UserID": "1",
                "Type": "PIN", "Status": "Succ"}]
    await ak.async_deurlog()   # eerste lezing zet de teller
    nep.log.append({"ID": "11", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                    "UserID": "700001", "Type": "PIN", "Status": "Failed", "RelayID": "--"})
    await ak.async_deurlog()
    from homeassistant.components import persistent_notification
    assert any("weigert eigen gebruiker" in (n.get("title") or "") for n in persistent_notification._async_get_or_create_notifications(hass).values())
    assert ak.status["ok"] is False
