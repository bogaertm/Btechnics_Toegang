import asyncio
"""Akuvox S532 sync tegen een nagebootst toestel: enkel eigen gebruikers vanaf 700001, SmartPlus blijft onaangeroerd."""
from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.util import dt as dt_util

from custom_components.btechnics_toegang.const import DOMAIN

IP = "10.9.8.7"


class NepAkuvox:
    """Nagebootste S532 volgens contract/akuvox_s532.json: het toestel bewaart enkel velden die het kent,
    neemt het schema enkel aan als 'Schedule-Relay' (met streepje) en toont het als 'ScheduleRelay'.
    'ScheduleRelay' meesturen bij add/set doet niets (zo bleven onze gebruikers gisteren zonder rechten)."""
    VELDEN = ("Name", "UserID", "PrivatePIN", "CardCode", "RelayScheduleActivationPermission", "Group", "WebRelay", "LiftFloorNum")

    def __init__(self, schema_negeren=False):
        self.users = {"1": {"ID": "1", "Name": "Cloud Jan", "UserID": "12", "PrivatePIN": "1111", "Source": "Cloud",
                            "ScheduleRelay": "32682-12", "RelayScheduleActivationPermission": "12", "Group": "Default"},
                      "2": {"ID": "2", "Name": "Vreemd", "UserID": "700500", "PrivatePIN": "2222", "Source": "Local",
                            "ScheduleRelay": "1001-12;", "RelayScheduleActivationPermission": "12", "Group": "Default"}}
        self.n = 2
        self.log = []
        self.schemas = {"1": {"ID": "1", "DisplayID": "1001", "Name": "Always", "Daily": "00:00:00-23:59:59"},
                        "2": {"ID": "2", "DisplayID": "1002", "Name": "Never"}}
        self.schema_negeren = schema_negeren   # toestel dat het schema niet overneemt (zoals de fout van 18/10)
        self.schema_weigeren = False           # toestel dat geen nieuwe schema's aanneemt
        self.schema_get_fout = False           # /api/schedule/get geeft HTTP 500
        self.user_del_fout = False             # /api/user/del geeft HTTP 500
        self.herschik = False                  # toestel nummert DisplayID's opnieuw na een wissing (1, 2, 3 ...)

    def _neem(self, doel, it):
        for k in self.VELDEN:
            if k in it:
                doel[k] = str(it[k])
        if "Schedule-Relay" in it and not self.schema_negeren:
            doel["ScheduleRelay"] = str(it["Schedule-Relay"])
        doel.setdefault("ScheduleRelay", "")
        doel.setdefault("RelayScheduleActivationPermission", "")
        doel.setdefault("Group", "")

    async def get(self, method, url, data):
        if url.path == "/api/schedule/get":
            if self.schema_get_fout:
                return AiohttpClientMockResponse(method, url, status=500)
            pagina = int(url.query.get("page", "1"))
            stuk = list(self.schemas.values())[(pagina - 1) * 2:pagina * 2]
            return AiohttpClientMockResponse(method, url, json={"retcode": 0, "data": {"num": len(stuk), "item": stuk}})
        if url.path == "/api/user/get":
            pagina = int(url.query.get("page", "1"))
            alles = list(self.users.values())
            stuk = alles[(pagina - 1) * 2:pagina * 2]   # het echte toestel geeft per pagina een deel
            return AiohttpClientMockResponse(method, url, json={"retcode": 0, "data": {"num": len(stuk), "item": stuk}})
        return AiohttpClientMockResponse(method, url, json={"retcode": 0, "data": {"num": len(self.log), "item": self.log}})

    async def post(self, method, url, data):
        actie = url.path.rsplit("/", 1)[1]
        if url.path == "/api/user/del" and self.user_del_fout:
            return AiohttpClientMockResponse(method, url, status=500)
        if url.path.startswith("/api/schedule/"):
            for it in data["data"]["item"]:
                # zoals de echte S532 (01/11): bewaart Type/Week/Daily/Date letterlijk, Mode = Type, DisplayID klein volgnummer
                velden = {k: str(it.get(k, "")) for k in ("Type", "Week", "Daily", "Date")}
                if actie == "add" and self.schema_weigeren:
                    antw = [{"Ret": 1, "ID": "", "Name": it["Name"]}]
                elif actie == "add":
                    self.n += 1
                    # zoals de echte S532 (gemeten 01/11): laagste vrije DisplayID, een vrijgekomen nummer wordt hergebruikt
                    bezet = {x["DisplayID"] for x in self.schemas.values()}
                    vrij = next(str(i) for i in range(1, 1000) if str(i) not in bezet)
                    self.schemas[str(self.n)] = {"ID": str(self.n), "DisplayID": vrij, "Name": it["Name"], "Source": "Local",
                                                 "Mode": int(velden["Type"] or 0), **velden}
                    antw = [{"Ret": 0, "Type": int(velden["Type"] or 0), "ID": str(self.n), "Name": it["Name"]}]
                elif actie == "set":
                    self.schemas[it["ID"]].update(velden)
                elif actie == "del":
                    del self.schemas[it["ID"]]
                    if self.herschik:
                        eigen = sorted((x for x in self.schemas.values() if x.get("Source") == "Local" and x["DisplayID"] not in ("1001", "1002")),
                                       key=lambda x: int(x["ID"]))
                        for i, x in enumerate(eigen, 1):
                            x["DisplayID"] = str(i)
            return AiohttpClientMockResponse(method, url, json={"retcode": 0, "message": "OK", "data": {"num": 1, "item": antw if actie == "add" else []}})
        for it in data["data"]["item"]:
            if actie == "add":
                self.n += 1
                u = {"ID": str(self.n), "Source": "Local"}
                self._neem(u, it)
                self.users[str(self.n)] = u
            elif actie == "set":
                self._neem(self.users[it["ID"]], it)
            elif actie == "del":
                del self.users[it["ID"]]
        return AiohttpClientMockResponse(method, url, json={"retcode": 0, "message": "OK"})


async def test_akuvox_sync(hass, aioclient_mock, hass_client_no_auth, hass_ws_client):
    await hass.config.async_set_time_zone("Europe/Brussels")
    nep = NepAkuvox()
    aioclient_mock.get(f"https://{IP}/api/user/get", side_effect=nep.get)
    aioclient_mock.get(f"https://{IP}/api/doorlog/get", side_effect=nep.get)
    aioclient_mock.get(f"https://{IP}/api/schedule/get", side_effect=nep.get)
    for a in ("add", "set", "del"):
        aioclient_mock.post(f"https://{IP}/api/user/{a}", side_effect=nep.post)
        aioclient_mock.post(f"https://{IP}/api/schedule/{a}", side_effect=nep.post)
    aioclient_mock.post(f"https://{IP}/api/relay/trig", json={"retcode": 0, "message": "OK"})
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = hass.data[DOMAIN]["register"]
    voordeur = reg.deur_toevoegen("Voordeur", wie="test")
    hass.config_entries.async_update_entry(entry, options={"akuvox_aan": True, "akuvox_ip": IP, "akuvox_gebruiker": "api",
                                                           "akuvox_wachtwoord": "geheim", "akuvox_deur": voordeur["id"]})
    await hass.async_block_till_done()
    reg = hass.data[DOMAIN]["register"]
    ak = hass.data[DOMAIN]["akuvox"]
    assert ak.aan
    # persoon met recht op de voordeur komt erop, iemand zonder dat recht niet
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Binnen"}, blocking=True)
    jan = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[voordeur["id"]])
    reg.toevoegen("test", naam="Enkel Binnen", pin="472910", deuren=["Binnen"])
    v = await ak.async_sync()
    assert v["toegevoegd"] == ["Jan Peeters"] and not v["fouten"]
    nieuw = [u for u in nep.users.values() if u["Name"] == "Jan Peeters"][0]
    assert nieuw["UserID"] == "700001" and nieuw["PrivatePIN"] == "583920" and nieuw["ScheduleRelay"] == "1001-1;"
    assert nieuw["RelayScheduleActivationPermission"] == "1" and nieuw["Group"] == "Default" and ak.status["ok"] and ak.status["eigen"] == 1
    assert ak.status["vreemd_boven_start"] == 1 and ak.status["eigen"] == 1
    # code wijzigen: set, geen nieuwe gebruiker
    reg.bijwerken("test", jan["id"], pin="694031")
    v = await ak.async_sync()
    assert v["gewijzigd"] == ["Jan Peeters"] and nieuw["PrivatePIN"] == "694031" and len(nep.users) == 3
    # blokkeren: weg van het toestel; SmartPlus en de vreemde 700500 blijven
    reg.blokkeren("test", jan["id"])
    v = await ak.async_sync()
    assert v["gewist"] == ["Jan Peeters"] and sorted(nep.users) == ["1", "2"]
    assert ak._staat["gebruikers"][jan["id"]]["userid"] == "700001"
    # v0.15.0: uren als schema op het toestel; de persoon blijft erop, het toestel bewaakt de uren (ook zonder HA)
    reg.deblokkeren("test", jan["id"])
    nu = dt_util.now()
    andere_dag = (nu.isoweekday() % 7) + 1
    reg.bijwerken("test", jan["id"], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    v = await ak.async_sync()
    assert v["toegevoegd"] == ["Jan Peeters"] and not v["fouten"] and ak.status["ok"]
    eigen_s = [x for x in nep.schemas.values() if x["Name"].startswith("BTX ")]
    assert len(eigen_s) == 1 and eigen_s[0]["Type"] == "1" and eigen_s[0]["Week"] == str(andere_dag % 7) and eigen_s[0]["Daily"] == "08:00-09:00"
    jan_dev = [u for u in nep.users.values() if u["Name"] == "Jan Peeters"][0]
    assert jan_dev["ScheduleRelay"] == f"{eigen_s[0]['DisplayID']}-1;"
    # nog eens: niets te doen; over middernacht + einddatum: twee stukken van Type 0 met datum
    v = await ak.async_sync()
    assert not any(v[k] for k in ("toegevoegd", "gewijzigd", "gewist", "fouten"))
    reg.bijwerken("test", jan["id"], schema=[{"dagen": [5], "van": "22:00", "tot": "06:00"}])
    reg.geldigheid("test", jan["id"], valid_until="2099-12-31T23:59:00+01:00")
    v = await ak.async_sync()
    assert not v["fouten"], v
    eigen_s = sorted((x for x in nep.schemas.values() if x["Name"].startswith("BTX ")), key=lambda x: x["Daily"])
    assert [(x["Type"], x["Week"], x["Daily"], x["Date"]) for x in eigen_s] == [("0", "6", "00:00-06:00", "00000000-20991231"),
                                                                              ("0", "5", "22:00-23:59", "00000000-20991231")]
    jan_dev = [u for u in nep.users.values() if u["Name"] == "Jan Peeters"][0]
    assert sorted(jan_dev["ScheduleRelay"].strip(";").split(";")) == sorted(f"{x['DisplayID']}-1" for x in eigen_s)
    # een vreemd schema (SmartPlus of handmatig) blijft altijd staan
    nep.schemas["90"] = {"ID": "90", "DisplayID": "35533", "Name": "Medewerkers", "Source": "Cloud", "Type": "2", "Daily": "00:00:00-23:59:59"}
    # schema en datum weg: terug naar 1001, eigen schema's opgeruimd, vreemde blijven
    reg.bijwerken("test", jan["id"], schema=[])
    reg.geldigheid("test", jan["id"], valid_until=None)
    v = await ak.async_sync()
    assert not v["fouten"], v
    assert sorted(nep.schemas) == ["1", "2", "90"] and ak._staat["schemas"] == {}
    assert [u for u in nep.users.values() if u["Name"] == "Jan Peeters"][0]["ScheduleRelay"] == "1001-1;"
    reg.bijwerken("test", jan["id"], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    await ak.async_sync()
    # deur openen zonder schakelaar: relais 1 van de S532, met de pulsduur
    await hass.services.async_call(DOMAIN, "deur_openen", {"deur": voordeur["id"]}, blocking=True)
    trig = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/relay/trig")]
    assert trig and trig[-1][2]["data"] == {"num": 1, "mode": 0, "level": 0, "delay": 5}
    # deurlog naar de historiek, met een foto van de camera van de deur
    reg.deur_wijzigen(voordeur["id"], camera="camera.voordeur", wie="test")
    reg.bijwerken("test", jan["id"], schema=[])
    await ak.async_sync()
    nep.log = [{"ID": "41", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                "UserID": "700001", "Type": "PrivatePIN", "Status": "Succ"},
               {"ID": "42", "Date": nu.strftime("%Y-%m-%d"), "Time": nu.strftime("%H:%M:%S"), "Name": "Cloud Jan",
                "UserID": "12", "Type": "Card", "Status": "Failed"}]
    from unittest.mock import AsyncMock, patch
    with patch("custom_components.btechnics_toegang.toegang._camera_beeld", AsyncMock(return_value=b"\xff\xd8jpg")):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1, seconds=5))
        await hass.async_block_till_done()
        for _ in range(5):
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
    rij = [r for r in await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.recent, voordeur["id"], 10)
           if r["name"] != "Op afstand"]
    assert {(r["name"], r["method"], bool(r["opened"])) for r in rij} == {("Jan Peeters", "code", True), ("Cloud Jan", "badge", False)}
    assert all(r.get("photo", "").startswith("a_") for r in rij)
    from custom_components.btechnics_toegang.portaal import foto_pad
    with open(foto_pad(hass, rij[0]["photo"]), "rb") as f:
        assert f.read() == b"\xff\xd8jpg"
    # tweede keer niets dubbel
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2, seconds=10))
    for _ in range(5):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    assert len(await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.recent, voordeur["id"], 10)) == 3
    # Action URL: de S532 meldt een poging, HA neemt meteen een foto en leest het deurlog zonder op de 5 s te wachten
    nu2 = dt_util.now()
    nep.log.append({"ID": "43", "Date": nu2.strftime("%Y-%m-%d"), "Time": nu2.strftime("%H:%M:%S"), "Name": "Jan Peeters",
                    "UserID": "700001", "Type": "PrivatePIN", "Status": "Succ"})
    wid = reg.data["akuvox"]["webhook"]
    from unittest.mock import AsyncMock, patch
    with patch("custom_components.btechnics_toegang.toegang._camera_beeld", AsyncMock(return_value=b"\xff\xd8vooraf")) as cam:
        client = await hass_client_no_auth()
        r = await client.get(f"/api/webhook/{wid}")
        assert r.status == 200
        for _ in range(5):
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
        assert cam.await_count == 1   # enkel de momentopname bij de melding, niet nog eens bij het deurlog
    rij = await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.recent, voordeur["id"], 10)
    assert len(rij) == 4 and rij[0].get("photo")
    assert reg.data["akuvox"].get("melding_laatst")
    # diagnose (enkel lezen)
    t = await hass.services.async_call(DOMAIN, "akuvox_test", {}, blocking=True, return_response=True)
    assert t["toestel"]["boven_700000_niet_van_ons"] == ["700500"] and t["toestel"]["van_ons"] == 1
    # v0.14.1: kaart van een Akuvox deur zonder tablets toont toestellen (S532) i.p.v. Tablets online 0/0
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 91, "type": f"{DOMAIN}/doors"})
    r = await ws.receive_json()
    d = [x for x in r["result"]["doors"] if x["id"] == voordeur["id"]][0]
    assert d["devices"]["totaal"] == 1 and d["devices"]["namen"] == ["S532"] and d["devices"]["online"] in (0, 1)
    b = [x for x in r["result"]["doors"] if x["name"] == "Binnen"][0]
    assert b["devices"] is None


async def test_akuvox_schema_proef(hass, aioclient_mock):
    """v0.14.3: de proef zet schema's en gebruiker 799990 tijdelijk, leest terug en laat het toestel achter zoals ervoor."""
    nep = NepAkuvox()
    aioclient_mock.get(f"https://{IP}/api/user/get", side_effect=nep.get)
    aioclient_mock.get(f"https://{IP}/api/schedule/get", side_effect=nep.get)
    for a in ("add", "set", "del"):
        aioclient_mock.post(f"https://{IP}/api/user/{a}", side_effect=nep.post)
        aioclient_mock.post(f"https://{IP}/api/schedule/{a}", side_effect=nep.post)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    deur = hass.data[DOMAIN]["register"].deur_toevoegen("Voordeur", wie="test")
    hass.config_entries.async_update_entry(entry, options={"akuvox_aan": True, "akuvox_ip": IP, "akuvox_gebruiker": "api",
                                                           "akuvox_wachtwoord": "geheim", "akuvox_deur": deur["id"]})
    await hass.async_block_till_done()
    users_voor = {k: dict(v) for k, v in nep.users.items()}
    r = await hass.services.async_call(DOMAIN, "akuvox_schema_proef", {}, blocking=True, return_response=True)
    assert r["varianten"]["A"]["teruggelezen"] and r["varianten"]["B"]["teruggelezen"]
    g = r["gebruiker"]
    assert g["teruggelezen"][0]["ScheduleRelay"] == g["schema"] + "-1;" and "PrivatePIN" not in g["teruggelezen"][0] and g["weg"]
    assert r["opgeruimd"] == 2 and sorted(nep.schemas) == ["1", "2"] and nep.users == users_voor
    # v0.14.4: eigen varianten, naam wordt altijd BTX proef n, zonder proefgebruiker
    r = await hass.services.async_call(DOMAIN, "akuvox_schema_proef", {"items": [{"Name": "Always", "Type": "1", "Week": "12345"}], "gebruiker": False},
                                       blocking=True, return_response=True)
    assert r["varianten"]["1"]["verstuurd"]["Name"] == "BTX proef 1" and r["gebruiker"] is None
    assert r["opgeruimd"] == 1 and sorted(nep.schemas) == ["1", "2"] and nep.users == users_voor
    # v0.15.1: volgorde meten (DisplayID's en schedule/set), toestel blijft zoals het was
    r = await hass.services.async_call(DOMAIN, "akuvox_schema_proef", {"volgorde": True}, blocking=True, return_response=True)
    assert len(r["1_na_A_B_C"]) == 3 and len(r["3_na_wissen_A"]) == 2 and len(r["4_na_D"]) == 3 and r["5_opgeruimd"]
    assert r["2_na_set_B"]["BTX proef B"]["Week"] == "06"
    assert sorted(nep.schemas) == ["1", "2"] and nep.users == users_voor


def test_schema_items_randgevallen():
    """v0.15.1: weekdagen, middernacht, 24 uur, en een einduur dat het toestel ook zonder HA exact moet respecteren."""
    from custom_components.btechnics_toegang.akuvox import Akuvox
    oud = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Brussels"))
    try:
        _randgevallen(Akuvox.schema_items)
    finally:
        dt_util.set_default_time_zone(oud)


def _randgevallen(si):
    assert si({}) == []
    # zondag = 0, maandag = 1
    assert si({"schema": [{"dagen": [7, 1], "van": "08:00", "tot": "17:00"}]}) == [{"Type": "1", "Week": "01", "Daily": "08:00-17:00"}]
    # over middernacht: vrijdag 22:00 tot zaterdag 06:00
    assert si({"schema": [{"dagen": [5], "van": "22:00", "tot": "06:00"}]}) == [
        {"Type": "1", "Week": "5", "Daily": "22:00-23:59"}, {"Type": "1", "Week": "6", "Daily": "00:00-06:00"}]
    # 24 uur vanaf 00:00 = enkel de startdag; 24 uur vanaf 08:00 = tot 08:00 de dag erna
    assert si({"schema": [{"dagen": [1], "van": "00:00", "tot": "00:00"}]}) == [{"Type": "1", "Week": "1", "Daily": "00:00-23:59"}]
    assert si({"schema": [{"dagen": [1], "van": "08:00", "tot": "08:00"}]}) == [
        {"Type": "1", "Week": "1", "Daily": "08:00-23:59"}, {"Type": "1", "Week": "2", "Daily": "00:00-08:00"}]
    # einddatum om 23:59: gewoon bereik
    assert si({"valid_until": "2026-11-04T23:59:00+01:00"}) == [{"Type": "0", "Week": "0123456", "Daily": "00:00-23:59", "Date": "00000000-20261104"}]
    # einduur 14:00 op woensdag 4/11: bereik tot dinsdag, woensdag apart tot 14:00
    assert si({"valid_until": "2026-11-04T14:00:00+01:00"}) == [
        {"Type": "0", "Week": "0123456", "Daily": "00:00-23:59", "Date": "00000000-20261103"},
        {"Type": "0", "Week": "3", "Daily": "00:00-14:00", "Date": "20261104-20261104"}]
    # met uren: blok 08-17 op werkdagen, einde woensdag 12:00 -> woensdag 08-12; blok na het einduur valt weg
    assert si({"schema": [{"dagen": [1, 2, 3, 4, 5], "van": "08:00", "tot": "17:00"}, {"dagen": [3], "van": "13:00", "tot": "15:00"}],
               "valid_until": "2026-11-04T12:00:00+01:00"}) == [
        {"Type": "0", "Week": "12345", "Daily": "08:00-17:00", "Date": "00000000-20261103"},
        {"Type": "0", "Week": "3", "Daily": "13:00-15:00", "Date": "00000000-20261103"},
        {"Type": "0", "Week": "3", "Daily": "08:00-12:00", "Date": "20261104-20261104"}]
    # UTC tijd wordt lokale tijd (Brussel): 2026-11-04T22:30Z = 23:30 lokaal
    assert si({"valid_until": "2026-11-04T22:59:00+00:00"})[0]["Date"] == "00000000-20261104"
    # einde om 00:00: niets meer over op de laatste dag
    assert si({"valid_until": "2026-11-04T00:00:00+01:00"}) == [{"Type": "0", "Week": "0123456", "Daily": "00:00-23:59", "Date": "00000000-20261103"}]
