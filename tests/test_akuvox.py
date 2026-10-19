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
        self.schema_negeren = schema_negeren   # toestel dat het schema niet overneemt (zoals de fout van 18/10)

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
        if url.path == "/api/user/get":
            pagina = int(url.query.get("page", "1"))
            alles = list(self.users.values())
            stuk = alles[(pagina - 1) * 2:pagina * 2]   # het echte toestel geeft per pagina een deel
            return AiohttpClientMockResponse(method, url, json={"retcode": 0, "data": {"num": len(stuk), "item": stuk}})
        return AiohttpClientMockResponse(method, url, json={"retcode": 0, "data": {"num": len(self.log), "item": self.log}})

    async def post(self, method, url, data):
        actie = url.path.rsplit("/", 1)[1]
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


async def test_akuvox_sync(hass, aioclient_mock, hass_client_no_auth):
    await hass.config.async_set_time_zone("Europe/Brussels")
    nep = NepAkuvox()
    aioclient_mock.get(f"https://{IP}/api/user/get", side_effect=nep.get)
    aioclient_mock.get(f"https://{IP}/api/doorlog/get", side_effect=nep.get)
    for a in ("add", "set", "del"):
        aioclient_mock.post(f"https://{IP}/api/user/{a}", side_effect=nep.post)
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
    # buiten de uren: niet op het toestel
    reg.deblokkeren("test", jan["id"])
    nu = dt_util.now()
    andere_dag = (nu.isoweekday() % 7) + 1
    reg.bijwerken("test", jan["id"], schema=[{"dagen": [andere_dag], "van": "08:00", "tot": "09:00"}])
    v = await ak.async_sync()
    assert not v["toegevoegd"] and sorted(nep.users) == ["1", "2"]
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
