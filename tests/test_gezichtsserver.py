"""Gezichtsserver (container op Unraid) koppelt als soort server aan een deur met camera en gebruikt het tabletprotocol."""
import base64
import json

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.btechnics_toegang.const import DOMAIN

from .test_codes import API, JPEG, _deur, _reg, _ws, hass_klaar  # noqa: F401
from .test_gezicht import OAEP, _gsm_pakket, _sleutelpaar


async def test_gezichtsserver(hass_klaar, hass_ws_client, hass_client_no_auth):
    hass = hass_klaar
    client = await hass_client_no_auth()
    reg = _reg(hass)
    deur = _deur(hass, "Vergaderzaal")
    reg.deur(deur)["open_entiteit"] = "script.deur_test"
    oproepen = async_mock_service(hass, "script", "turn_on")
    priv, pub = _sleutelpaar()
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    k = await (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "gezichtsserver-vergaderzaal",
                                                         "soort": "server", "pubkey": pub, "app_versie": "0.1.0"})).json()
    t = reg.data["tablets"][k["tablet_id"]]
    assert t["soort"] == "server" and t["naam"] == "Gezichtsserver"
    kop = {"Authorization": f"Bearer {k['sleutel']}"}
    # dashboard: server staat bij de tablets met soort, telt niet mee als tablet op de kaart
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/data")
    assert [x["soort"] for x in r["result"]["tablets"]] == ["server"]
    r = await _ws(ws, 3, type=f"{DOMAIN}/doors")
    assert [d["tablets"] for d in r["result"]["doors"] if d["id"] == deur] == [0]
    # medewerker scant met toestemming v2, ook voor de server
    r = await _ws(ws, 4, type=f"{DOMAIN}/uitnodiging", doors=[deur], naam="Lien")
    pad = r["result"]["url"][r["result"]["url"].index("/api/"):]
    r = await client.post(pad + "/aanmelden", json={"voornaam": "Lien", "achternaam": "Maes", "telefoon": "1", "email": "l@m.be",
                                                     "akkoord": True, "pin": "740163", "pin2": "740163", "foto": JPEG})
    eigen = (await r.json())["url"]; eigen = eigen[eigen.index("/api/"):]
    info = await (await client.get(eigen + "/info")).json()
    g = info["gezicht"]
    assert g["toestemming"]["versie"] == "v2" and "gezichtsserver" in g["toestemming"]["tekst"]
    assert g["tablets"][0]["soort"] == "server"
    afdrukken = [np.random.RandomState(i).randn(192).astype(np.float32) for i in range(4)]
    r = await client.post(eigen + "/gezicht", json={**_gsm_pakket(info, afdrukken), "huidige_pin": "740163"})
    assert r.status == 200, await r.text()
    # server haalt de lijst en kan ontsleutelen
    gz = (await (await client.get(f"{API}/lijst", headers=kop)).json())["personen"][0]["gezicht"]
    aes = priv.decrypt(base64.b64decode(gz["sleutel"]), OAEP)
    klaar = json.loads(AESGCM(aes).decrypt(base64.b64decode(gz["iv"]), base64.b64decode(gz["data"]), gz["aad"].encode()))
    assert len(klaar["afdrukken"]) == 4 and gz["toestemming"] == "v2"
    # herkend: HA beslist, opent en logt
    pid = gz["aad"].split(":", 1)[1]
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "gezicht", "persoon_id": pid, "log": False})
    assert r.status == 200 and (await r.json())["geopend"] is True
    await hass.async_block_till_done()
    assert len(oproepen) == 1
    recent = hass.data[DOMAIN]["toegang"].recent[deur]
    assert recent[0]["method"] == "gezicht" and recent[0]["name"] == "Lien Maes" and recent[0]["opened"]
    # onbekende persoon: geweigerd, deur blijft dicht
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "gezicht", "persoon_id": "p_bestaatniet", "log": False})
    assert r.status == 403 and (await r.json())["reden"] == "onbekend" and len(oproepen) == 1
