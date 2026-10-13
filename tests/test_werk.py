"""Koppeling met Btechnics Work: acties via de dienst btechnics_toegang.werk (zoals Btechnics IOT ze oproept)."""
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.btechnics_toegang.const import DOMAIN

from .test_codes import API, JPEG, _reg, hass_klaar  # noqa: F401


async def werk(hass, actie, door="Matthias", **velden):
    return await hass.services.async_call(DOMAIN, "werk", {"actie": actie, "velden": velden, "door": door},
                                          blocking=True, return_response=True)


async def test_werk_flow(hass_klaar, hass_client_no_auth):
    hass = hass_klaar
    client = await hass_client_no_auth()
    o = await werk(hass, "overzicht")
    assert o["schema"] == 1 and [d["naam"] for d in o["deuren"]] == ["Magazijn", "Vergaderzaal"] and o["medewerkers"] == []
    # strenge velden
    with pytest.raises(HomeAssistantError, match="onbekende velden"):
        await werk(hass, "uitnodigen", werk_id="12", voornaam="A", achternaam="B", deuren=["Magazijn"], pin="1234")
    with pytest.raises(HomeAssistantError, match="onbekende actie"):
        await werk(hass, "deur_openen", deur="Magazijn")
    # uitnodigen met voorinvullen
    r = await werk(hass, "uitnodigen", werk_id="12", voornaam="Lotte", achternaam="Peeters", telefoon="+32470111222",
                   email="lotte@btechnics.be", deuren=["Magazijn"], schema=[{"dagen": [1, 2, 3, 4, 5], "van": "07:00", "tot": "18:00"}])
    pad = r["url"][r["url"].index("/api/"):]
    info = await (await client.get(pad + "/info")).json()
    assert info["voorinvullen"] == {"voornaam": "Lotte", "achternaam": "Peeters", "telefoon": "+32470111222", "email": "lotte@btechnics.be"}
    # tweede uitnodiging voor dezelfde medewerker vervangt de eerste
    r2 = await werk(hass, "uitnodigen", werk_id="12", voornaam="Lotte", achternaam="Peeters", deuren=["Magazijn"])
    assert (await client.get(pad + "/info")).status == 404
    pad = r2["url"][r2["url"].index("/api/"):]
    r = await client.post(pad + "/aanmelden", json={"voornaam": "Lotte", "achternaam": "Peeters", "telefoon": "+32470111222",
                                                     "email": "lotte@btechnics.be", "akkoord": True, "pin": "740163", "pin2": "740163", "foto": JPEG})
    assert r.status == 200
    o = await werk(hass, "overzicht")
    m = o["medewerkers"][0]
    assert m["werk_id"] == "12" and m["pincode"] and m["foto"] and m["portaal"] and "740163" not in str(o)
    # portaallink: dezelfde zolang niet nieuw
    l1 = await werk(hass, "portaal_link", werk_id="12")
    l2 = await werk(hass, "portaal_link", werk_id="12")
    assert l1["url"] == l2["url"] and l1["portaal_v"] == m["portaal_v"]
    assert (await client.get(l1["url"][l1["url"].index("/api/"):] + "/info")).status == 200
    l3 = await werk(hass, "portaal_link", werk_id="12", nieuw=True)
    assert l3["url"] != l1["url"] and l3["portaal_v"] != l1["portaal_v"]
    # wijzigen, blokkeren, uit dienst
    r = await werk(hass, "wijzigen", werk_id="12", deuren=["Magazijn", "Vergaderzaal"], telefoon="+32470999999")
    assert len(r["medewerker"]["deuren"]) == 2 and r["medewerker"]["telefoon"] == "+32470999999"
    assert (await werk(hass, "blokkeren", werk_id="12"))["medewerker"]["status"] == "blocked"
    assert (await werk(hass, "deblokkeren", werk_id="12"))["medewerker"]["status"] == "active"
    assert (await werk(hass, "uit_dienst", werk_id="12"))["medewerker"]["status"] == "retired"
    with pytest.raises(HomeAssistantError, match="uit dienst"):
        await werk(hass, "portaal_link", werk_id="12")
    assert (await werk(hass, "herstellen", werk_id="12"))["medewerker"]["status"] == "active"
    # bestaande HA persoon koppelen
    await hass.services.async_call(DOMAIN, "persoon_toevoegen", {"naam": "Jan Oud", "pincode": "583920", "deuren": ["Magazijn"]}, blocking=True)
    o = await werk(hass, "overzicht")
    jan = [x for x in o["andere"] if x["naam"] == "Jan Oud"][0]
    with pytest.raises(HomeAssistantError, match="al gekoppeld"):
        await werk(hass, "koppelen", werk_id="12", persoon_id=jan["persoon_id"])
    assert (await werk(hass, "koppelen", werk_id="13", persoon_id=jan["persoon_id"]))["medewerker"]["naam"] == "Jan Oud"
    # tijdelijke code, modus, deur, koppelcode
    t = await werk(hass, "tijdelijke_code", naam="Pakket", deuren=["Magazijn"], geldig_tot="2099-01-01T12:00:00")
    assert len(t["code"]) == 6
    assert (await werk(hass, "modus", deur="Magazijn", modus="niet_storen"))["modus"] == "niet_storen"
    assert (await werk(hass, "tablet_koppelcode", deur="Magazijn"))["geldig_s"] == 600
    d = await werk(hass, "deur_toevoegen", naam="Kelder")
    await hass.async_block_till_done()
    assert d["naam"] == "Kelder"
    # audit toont wie in Work
    assert any(a.get("user") == "Matthias via Work" for a in _reg(hass).data["audit"][-5:])
