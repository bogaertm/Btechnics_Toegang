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
    aioclient_mock.get(f"https://{IP}/api/schedule/get", side_effect=nep.get)
    for a in ("add", "set", "del"):
        aioclient_mock.post(f"https://{IP}/api/user/{a}", side_effect=nep.post)
        aioclient_mock.post(f"https://{IP}/api/schedule/{a}", side_effect=nep.post)
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


async def test_weigering_buiten_de_uren_is_geen_alarm(hass, aioclient_mock):
    """v0.15.0: het schema staat op de S532; weigert het toestel buiten de uren, dan is dat terecht (geen alarm)."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    nu = dt_util.now()
    andere_dag = (nu.isoweekday() % 7) + 1
    reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    v = await ak.async_sync()
    assert v["toegevoegd"] == ["Jan Peeters"] and ak.status["ok"]
    nep.log = [{"ID": "10", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "x", "UserID": "1", "Type": "PIN", "Status": "Succ"}]
    await ak.async_deurlog()
    nep.log.append({"ID": "11", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                    "UserID": "700001", "Type": "PIN", "Status": "Failed", "RelayID": "--"})
    rijen = await ak.async_deurlog()
    from homeassistant.components import persistent_notification
    assert not any("weigert eigen gebruiker" in (n.get("title") or "") for n in persistent_notification._async_get_or_create_notifications(hass).values())
    assert ak.status["ok"] is True and rijen[0]["tekst"].endswith("(buiten de uren)") and not rijen[0]["opened"]


async def test_schema_niet_op_toestel_valt_veilig_terug(hass, aioclient_mock):
    """Weigert het toestel het schema, dan geen gebruiker met 'altijd' buiten de uren: veilig terug naar de oude regel."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)

    nep.schema_weigeren = True
    andere_dag = (dt_util.now().isoweekday() % 7) + 1
    reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    v = await ak.async_sync()
    # nieuw en buiten de uren: wel op het toestel, maar met Never (1002), nooit met altijd
    assert v["fouten"] and [u["ScheduleRelay"] for u in nep.users.values() if u["Name"] == "Jan Peeters"] == ["1002-1;"]



def _relay(nep, naam):
    return [u for u in nep.users.values() if u["Name"] == naam][0]["ScheduleRelay"]


async def test_schema_leesfout_maakt_nooit_altijd(hass, aioclient_mock):
    """Review 01/11 punt 1: een tijdelijke leesfout van de schema's mag niemand met uren of einddatum op 1001 (altijd) zetten
    en ook niemand wissen; de gebruikers blijven staan zoals ze stonden."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    andere_dag = (dt_util.now().isoweekday() % 7) + 1
    reg.toevoegen("test", naam="Piet", pin="583920", deuren=[deur["id"]], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    reg.toevoegen("test", naam="Ann", pin="694031", deuren=[deur["id"]], valid_until="2099-12-31T23:59:00+01:00")
    v = await ak.async_sync()
    assert not v["fouten"]
    voor = {n: _relay(nep, n) for n in ("Piet", "Ann")}
    assert "1001-1;" not in voor.values()
    nep.schema_get_fout = True
    v = await ak.async_sync()
    assert v["fouten"] and not v["gewijzigd"] and not v["gewist"]
    assert {n: _relay(nep, n) for n in ("Piet", "Ann")} == voor
    nep.schema_get_fout = False
    v = await ak.async_sync()
    assert not v["fouten"] and {n: _relay(nep, n) for n in ("Piet", "Ann")} == voor


async def test_schema_niet_gewist_zolang_gebruiker_ernaar_verwijst(hass, aioclient_mock):
    """Review 01/11 punt 2: lukt het wissen van een gebruiker niet, dan blijft zijn schema staan (geen vrijgekomen DisplayID
    die later bij iemand anders terechtkomt)."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    andere_dag = (dt_util.now().isoweekday() % 7) + 1
    wim = reg.toevoegen("test", naam="Wim", pin="583920", deuren=[deur["id"]], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    await ak.async_sync()
    rel = _relay(nep, "Wim")
    nep.user_del_fout = True
    reg.blokkeren("test", wim["id"])
    v = await ak.async_sync()
    assert v["fouten"]
    btx = [x for x in nep.schemas.values() if x["Name"].startswith("BTX ")]
    assert len(btx) == 1 and rel == f"{btx[0]['DisplayID']}-1;"
    nep.user_del_fout = False
    v = await ak.async_sync()
    assert v["gewist"] == ["Wim"] and not v["fouten"]
    v = await ak.async_sync()   # schema pas weg als niemand er nog naar verwijst
    assert not [x for x in nep.schemas.values() if x["Name"].startswith("BTX ")] and ak._staat["schemas"] == {}


async def test_herschikte_displayids_worden_rechtgezet(hass, aioclient_mock):
    """Review 01/11 punt 3: nummert het toestel DisplayID's opnieuw na een wissing, dan zet de tweede ronde iedereen recht."""
    nep = NepAkuvox()
    nep.herschik = True
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    a = reg.toevoegen("test", naam="Een", pin="583920", deuren=[deur["id"]], schema=[{"dagen": [1], "van": "08:00", "tot": "09:00"}])
    reg.toevoegen("test", naam="Twee", pin="694031", deuren=[deur["id"]], schema=[{"dagen": [2], "van": "10:00", "tot": "11:00"}])
    await ak.async_sync()
    reg.bijwerken("test", a["id"], schema=[])   # Een: altijd, zijn schema mag weg -> Twee schuift op
    v = await ak.async_sync()
    assert not v["fouten"], v
    twee = [x for x in nep.schemas.values() if x["Name"].startswith("BTX ")]
    assert len(twee) == 1 and twee[0]["Week"] == "2" and _relay(nep, "Twee") == f"{twee[0]['DisplayID']}-1;"
    assert _relay(nep, "Een") == "1001-1;"


async def test_bestaande_gebruiker_blijft_als_schema_niet_lukt(hass, aioclient_mock):
    """Lukt het nieuwe schema niet, dan blijft een bestaande gebruiker zoals hij stond (niet naar altijd, niet gewist)."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    jan = reg.toevoegen("test", naam="Jan", pin="583920", deuren=[deur["id"]])
    await ak.async_sync()
    assert _relay(nep, "Jan") == "1001-1;"
    nep.schema_weigeren = True
    andere_dag = (dt_util.now().isoweekday() % 7) + 1
    reg.bijwerken("test", jan["id"], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    v = await ak.async_sync()
    # nog geen werkend schema: HA bewaakt de uren, nu buiten de uren dus Never (1002), niet gewist
    assert v["fouten"] and not v["gewist"] and _relay(nep, "Jan") == "1002-1;"
    nep.schema_weigeren = False
    v = await ak.async_sync()
    assert not v["fouten"] and _relay(nep, "Jan") not in ("1001-1;", "1002-1;")


async def test_binnengelaten_zonder_recht_geeft_alarm(hass, aioclient_mock):
    """Review 01/11 punt 7: laat het toestel iemand binnen die volgens HA geen toegang heeft (geblokkeerd), dan een melding."""
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    nep.user_del_fout = True   # wissen lukt niet, dus hij staat er nog op
    jan = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]])
    await ak.async_sync()
    reg.blokkeren("test", jan["id"])
    await ak.async_sync()
    nu = dt_util.now()
    nep.log = [{"ID": "10", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "x", "UserID": "1", "Type": "PIN", "Status": "Succ"}]
    await ak.async_deurlog()
    nep.log.append({"ID": "11", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                    "UserID": "700001", "Type": "PIN", "Status": "Succ", "RelayID": "A"})
    await ak.async_deurlog()
    from homeassistant.components import persistent_notification
    titels = [n.get("title") or "" for n in persistent_notification._async_get_or_create_notifications(hass).values()]
    assert any("liet iemand binnen" in t for t in titels) and ak.status["ok"] is False


async def test_alarmen_gaan_naar_gsm_en_ha(hass, aioclient_mock):
    """v0.15.3: S532 weigert iemand binnen de uren, of laat iemand zonder recht binnen: melding in HA EN op de gsm."""
    from pytest_homeassistant_custom_component.common import async_mock_service
    gsm = async_mock_service(hass, "notify", "mobile_app_test")
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    r = await hass.services.async_call(DOMAIN, "gsm_melding", {"doel": "notify.mobile_app_test", "test": True}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert r["gsm_melding"] == "mobile_app_test" and len(gsm) == 1
    ak = hass.data[DOMAIN]["akuvox"]
    jan = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur["id"]])
    await ak.async_sync()
    nu = dt_util.now()
    regel = lambda i, st: {"ID": str(i), "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                           "UserID": "700001", "Type": "PIN", "Status": st}
    nep.log = [{**regel(10, "Succ"), "UserID": "1", "Name": "x"}]
    await ak.async_deurlog()
    nep.log.append(regel(11, "Failed"))            # binnen de uren geweigerd
    await ak.async_deurlog()
    await hass.async_block_till_done()
    assert len(gsm) == 2 and "weigert" in gsm[-1].data["title"].lower()
    nep.user_del_fout = True
    reg.blokkeren("test", jan["id"])
    await ak.async_sync()
    nep.log.append(regel(12, "Succ"))              # geblokkeerd maar toch binnen
    await ak.async_deurlog()
    await hass.async_block_till_done()
    assert len(gsm) >= 3 and any("binnen" in c.data["title"].lower() for c in gsm)
    from homeassistant.components import persistent_notification
    titels = [n.get("title") or "" for n in persistent_notification._async_get_or_create_notifications(hass).values()]
    assert any("liet iemand binnen" in t for t in titels) and any("weigert" in t for t in titels)
    # leeg = enkel in HA
    await hass.services.async_call(DOMAIN, "gsm_melding", {"doel": ""}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    voor = len(gsm)
    await hass.services.async_call(DOMAIN, "gsm_melding", {"test": True}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert len(gsm) == voor


async def test_zelfcontrole_en_offline_alarm(hass, aioclient_mock):
    """v0.15.3: dagelijkse zelfcontrole meldt problemen; een toestel langer dan 30 min offline geeft één alarm."""
    import time
    from pytest_homeassistant_custom_component.common import async_mock_service
    from custom_components.btechnics_toegang.controle import async_offline_bewaking, zelfcontrole
    gsm = async_mock_service(hass, "notify", "mobile_app_test")
    nep = NepAkuvox()
    reg, ak, deur = await _opzet(hass, aioclient_mock, nep)
    await hass.services.async_call(DOMAIN, "gsm_melding", {"doel": "mobile_app_test"}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    ak = hass.data[DOMAIN]["akuvox"]
    await ak.async_sync()
    r = await hass.services.async_call(DOMAIN, "controle", {}, blocking=True, return_response=True)
    assert r["ok"] is True and r["fouten"] == []
    # nep tablet die al lang geen contact had
    reg.data["tablets"]["t_x"] = {"id": "t_x", "naam": "Testtablet", "deur_id": deur["id"], "laatst_mono": time.monotonic() - 3600}
    assert any("Testtablet is offline" in f for f in zelfcontrole(hass))
    hass.data[DOMAIN]["bewaking_sinds"] = time.monotonic() - 4000
    await async_offline_bewaking(hass)
    await async_offline_bewaking(hass)   # tweede keer: geen tweede alarm
    await hass.async_block_till_done()
    assert sum(1 for c in gsm if "offline" in c.data["title"].lower()) == 1
    reg.data["tablets"]["t_x"]["laatst_mono"] = time.monotonic()
    await async_offline_bewaking(hass)
    from homeassistant.components import persistent_notification
    titels = [n.get("title") or "" for n in persistent_notification._async_get_or_create_notifications(hass).values()]
    assert any("terug online" in t for t in titels) and not any(t.endswith("toestel offline") for t in titels)
    del reg.data["tablets"]["t_x"]
