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
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal", "soort": "server"}, blocking=True,
                                           return_response=True))["code"]
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
    # herkend, maar de deur staat nog op proef: HA weigert, ook als de server zelf live staat
    pid = gz["aad"].split(":", 1)[1]
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "gezicht", "persoon_id": pid, "log": False})
    assert r.status == 403 and (await r.json())["reden"] == "proef" and not oproepen
    # de server mag nooit met een andere methode openen
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "ha", "log": False})
    assert r.status == 403 and (await r.json())["reden"] == "niet_toegelaten"
    # beheerder zet de gezichtsserver live voor deze deur
    await hass.services.async_call(DOMAIN, "deur_wijzigen", {"deur": "Vergaderzaal", "gezicht_live": True}, blocking=True)
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "gezicht", "persoon_id": pid, "log": False})
    assert r.status == 200 and (await r.json())["geopend"] is True
    await hass.async_block_till_done()
    assert len(oproepen) == 1
    recent = hass.data[DOMAIN]["toegang"].recent[deur]
    assert recent[0]["method"] == "gezicht" and recent[0]["name"] == "Lien Maes" and recent[0]["opened"]
    # onbekende persoon: geweigerd, deur blijft dicht
    r = await client.post(f"{API}/open", headers=kop, json={"methode": "gezicht", "persoon_id": "p_bestaatniet", "log": False})
    assert r.status == 403 and (await r.json())["reden"] == "onbekend" and len(oproepen) == 1


async def test_doorzetten_zonder_nieuwe_scan(hass_klaar, hass_ws_client, hass_client_no_auth):
    """Nieuwe tablet of extra deur: de gezichtsserver zet de afdruk door, de medewerker scant niet opnieuw."""
    from homeassistant.core import callback
    hass = hass_klaar
    client = await hass_client_no_auth()
    reg = _reg(hass)
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Magazijn2"}, blocking=True)
    binnen, magazijn = _deur(hass, "Vergaderzaal"), _deur(hass, "Magazijn2")

    async def koppel(deur, soort=None):
        priv, pub = _sleutelpaar()
        code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": deur, "soort": soort or "tablet"}, blocking=True,
                                               return_response=True))["code"]
        k = await (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": deur + (soort or ""), "pubkey": pub,
                                                             **({"soort": soort} if soort else {})})).json()
        return priv, {"Authorization": f"Bearer {k['sleutel']}"}

    srv_priv, srv = await koppel("Magazijn2", "server")
    t1_priv, t1 = await koppel("Vergaderzaal")
    # medewerker enkel voor Vergaderzaal: gsm versleutelt voor tablet 1 en voor de server als bewaarder
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/uitnodiging", doors=[binnen], naam="Lien")
    pad = r["result"]["url"][r["result"]["url"].index("/api/"):]
    r = await client.post(pad + "/aanmelden", json={"voornaam": "Lien", "achternaam": "Maes", "telefoon": "1", "email": "l@m.be",
                                                     "akkoord": True, "pin": "740163", "pin2": "740163", "foto": JPEG})
    eigen = (await r.json())["url"]; eigen = eigen[eigen.index("/api/"):]
    info = await (await client.get(eigen + "/info")).json()
    assert sorted(t["deur"] for t in info["gezicht"]["tablets"]) == ["Nieuwe toestellen", "Vergaderzaal"]
    afdrukken = [np.random.RandomState(i).randn(192).astype(np.float32) for i in range(3)]
    assert (await client.post(eigen + "/gezicht", json={**_gsm_pakket(info, afdrukken), "huidige_pin": "740163"})).status == 200
    # server ziet haar niet in zijn lijst (geen toegang tot Magazijn2) en heeft nog niets door te zetten
    assert (await (await client.get(f"{API}/lijst", headers=srv)).json())["personen"] == []
    assert (await (await client.get(f"{API}/herverpak", headers=srv)).json())["taken"] == []
    assert (await client.get(f"{API}/herverpak", headers=t1)).status == 401
    # nieuwe tablet aan Vergaderzaal: geen mail om opnieuw te scannen, wel een taak voor de server
    gemeld = []
    hass.bus.async_listen(f"{DOMAIN}_gezicht_ontbreekt", callback(lambda e: gemeld.append(e.data)))
    t2_priv, t2 = await koppel("Vergaderzaal")
    await hass.async_block_till_done()
    assert gemeld == []

    async def server_doet():
        taken = (await (await client.get(f"{API}/herverpak", headers=srv)).json())["taken"]
        for tk in taken:
            aes = srv_priv.decrypt(base64.b64decode(tk["sleutel"]), OAEP)
            sl = {}
            for d in tk["doel"]:
                from cryptography.hazmat.primitives import serialization
                pub = serialization.load_der_public_key(base64.b64decode(d["pubkey"]))
                sl[d["pubkey_id"]] = base64.b64encode(pub.encrypt(aes, OAEP)).decode()
            r = await (await client.post(f"{API}/herverpak", headers=srv, json={"persoon_id": tk["persoon_id"], "sleutels": sl})).json()
            assert r["nieuw"] == len(tk["doel"])
        return len(taken)

    assert await server_doet() == 1
    gz = (await (await client.get(f"{API}/lijst", headers=t2)).json())["personen"][0]["gezicht"]
    aes = t2_priv.decrypt(base64.b64decode(gz["sleutel"]), OAEP)
    klaar = json.loads(AESGCM(aes).decrypt(base64.b64decode(gz["iv"]), base64.b64decode(gz["data"]), gz["aad"].encode()))
    assert len(klaar["afdrukken"]) == 3
    assert await server_doet() == 0
    # extra deur Magazijn2: de server leest ze nu zelf, zonder nieuwe scan
    p = [x for x in reg.data["personen"].values() if x["naam"] == "Lien Maes"][0]
    await hass.services.async_call(DOMAIN, "persoon_wijzigen", {"persoon": p["id"], "deuren": [binnen, magazijn]}, blocking=True)
    assert (await (await client.get(f"{API}/lijst", headers=srv)).json())["personen"][0]["gezicht"]
    # vreemde sleutel voor een toestel dat er niet bij hoort: genegeerd
    r = await (await client.post(f"{API}/herverpak", headers=srv, json={"persoon_id": p["id"], "sleutels": {"x" * 64: "A" * 344}})).json()
    assert r["nieuw"] == 0



async def test_soort_en_sleutelwissel(hass_klaar, hass_client_no_auth):
    """Audit: het toestel kiest zijn soort niet zelf, een sleutelwissel wacht op goedkeuring, en via de tunnel kan niets."""
    hass = hass_klaar
    client = await hass_client_no_auth()
    reg = _reg(hass)
    _, pub = _sleutelpaar()
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    # tabletcode, maar het toestel beweert server te zijn: geweigerd
    r = await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "x", "soort": "server", "pubkey": pub})
    assert r.status == 403
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    # via de tunnel: geweigerd, ook met een geldige code
    r = await client.post(f"{API}/koppel", headers={"Cf-Connecting-Ip": "1.2.3.4"}, json={"code": code, "toestel_id": "t1", "pubkey": pub})
    assert r.status == 403
    k = await (await client.post(f"{API}/koppel", json={"code": " " + code + " ", "toestel_id": "t1", "pubkey": pub})).json()
    t = reg.data["tablets"][k["tablet_id"]]
    kop = {"Authorization": f"Bearer {k['sleutel']}"}
    # andere sleutel via /status: niet toegepast, wacht op goedkeuring
    _, pub2 = _sleutelpaar()
    oud = t["pubkey_id"]
    r = await (await client.post(f"{API}/status", headers=kop, json={"pubkey": pub2})).json()
    assert r["pubkey_id"] == oud and "goedkeuring" in r["pubkey_fout"] and t["pubkey_wacht"]
    await hass.services.async_call(DOMAIN, "tablet_sleutel_goedkeuren", {"tablet": t["id"]}, blocking=True)
    assert t["pubkey_id"] != oud and "pubkey_wacht" not in t
    # tabletverkeer met een tunnelkop: geweigerd
    assert (await client.get(f"{API}/lijst", headers={**kop, "X-Forwarded-For": "1.2.3.4"})).status in (400, 403)
