"""Gezichtsafdrukken: tablet geeft publieke sleutel, gsm versleutelt, HA bewaart blind, tablet ontsleutelt."""
import base64
import json
import os

import numpy as np
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from custom_components.btechnics_toegang.const import DOMAIN

from .test_codes import API, JPEG, _deur, _reg, _ws, hass_klaar  # noqa: F401


def _sleutelpaar():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return k, base64.b64encode(der).decode()


OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


def _gsm_pakket(info, afdrukken):
    """Zoals de browser het doet: AES-256-GCM met aad, sleutel per tablet verpakt met RSA-OAEP SHA-1."""
    g = info["gezicht"]
    sleutel, iv = AESGCM.generate_key(256), os.urandom(12)
    klaar = json.dumps({"versie": g["versie"], "afdrukken": [base64.b64encode(a.astype("<f4").tobytes()).decode() for a in afdrukken]}).encode()
    data = AESGCM(sleutel).encrypt(iv, klaar, g["aad"].encode())
    sleutels = {}
    for t in g["tablets"]:
        pub = serialization.load_der_public_key(base64.b64decode(t["pubkey"]))
        sleutels[t["pubkey_id"]] = base64.b64encode(pub.encrypt(sleutel, OAEP)).decode()
    return {"versie": g["versie"], "toestemming": g["toestemming"]["versie"], "toestemming_hash": g["toestemming"]["hash"],
            "aantal": len(afdrukken), "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(data).decode(), "sleutels": sleutels}


async def test_gezicht_keten(hass_klaar, hass_ws_client, hass_client_no_auth):
    hass = hass_klaar
    client = await hass_client_no_auth()
    deur = _deur(hass, "Vergaderzaal")
    # tablet koppelen met publieke sleutel
    priv, pub = _sleutelpaar()
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    k = await (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "t1", "pubkey": pub})).json()
    assert len(k["pubkey_id"]) == 64
    kop = {"Authorization": f"Bearer {k['sleutel']}"}
    # ongeldige sleutel via status: fout terug, oude sleutel blijft
    r = await (await client.post(f"{API}/status", headers=kop, json={"pubkey": base64.b64encode(b"x" * 300).decode()})).json()
    assert "pubkey_fout" in r and r["pubkey_id"] == k["pubkey_id"]
    # medewerker via uitnodiging
    ws = await hass_ws_client(hass)
    r = await _ws(ws, 1, type=f"{DOMAIN}/uitnodiging", doors=[deur], naam="Lien")
    pad = r["result"]["url"][r["result"]["url"].index("/api/"):]
    r = await client.post(pad + "/aanmelden", json={"voornaam": "Lien", "achternaam": "Maes", "telefoon": "1", "email": "l@m.be",
                                                     "akkoord": True, "pin": "740163", "pin2": "740163", "foto": JPEG})
    eigen = (await r.json())["url"]; eigen = eigen[eigen.index("/api/"):]
    info = await (await client.get(eigen + "/info")).json()
    g = info["gezicht"]
    assert not g["ingesteld"] and len(g["tablets"]) == 1 and g["tablets"][0]["deur"] == "Vergaderzaal"
    assert "uitdrukkelijke toestemming" in g["toestemming"]["tekst"] and g["min"] == 3
    # lijst zonder afdruk
    lijst = await (await client.get(f"{API}/lijst", headers=kop)).json()
    assert lijst["personen"][0]["gezicht"] is False
    etag1 = None
    afdrukken = [np.random.RandomState(i).randn(192).astype(np.float32) for i in range(5)]
    afdrukken = [a / np.linalg.norm(a) for a in afdrukken]
    pakket = _gsm_pakket(info, afdrukken)
    # foute code, te weinig afdrukken, verkeerde toestemming
    r = await client.post(eigen + "/gezicht", json={**pakket, "huidige_pin": "000000"})
    assert r.status == 400 and "code klopt niet" in (await r.json())["fout"]
    r = await client.post(eigen + "/gezicht", json={**pakket, "aantal": 2, "huidige_pin": "740163"})
    assert r.status == 400
    r = await client.post(eigen + "/gezicht", json={**pakket, "toestemming_hash": "x", "huidige_pin": "740163"})
    assert r.status == 400 and "toestemming" in (await r.json())["fout"]
    r = await client.post(eigen + "/gezicht", json={**pakket, "huidige_pin": "740163"})
    assert r.status == 200, await r.text()
    assert (await r.json())["gezicht"]["tablets"][0]["heeft"] is True
    # HA heeft geen leesbare afdrukken
    p = [x for x in _reg(hass).data["personen"].values() if x["naam"] == "Lien Maes"][0]
    assert "afdrukken" not in json.dumps(p["gezicht"])
    # tablet krijgt het pakket en kan het ontsleutelen
    r = await client.get(f"{API}/lijst", headers=kop)
    gz = (await r.json())["personen"][0]["gezicht"]
    assert gz["pubkey_id"] == k["pubkey_id"] and gz["aantal"] == 5 and gz["aad"] == "btx-gezicht-v1:" + p["id"]
    aes = priv.decrypt(base64.b64decode(gz["sleutel"]), OAEP)
    klaar = json.loads(AESGCM(aes).decrypt(base64.b64decode(gz["iv"]), base64.b64decode(gz["data"]), gz["aad"].encode()))
    terug = [np.frombuffer(base64.b64decode(a), "<f4") for a in klaar["afdrukken"]]
    assert klaar["versie"] == "mfn112-arcface5-v1" and all(np.allclose(a, b) for a, b in zip(terug, afdrukken))
    # een andere tablet (nieuwe sleutel) krijgt niets
    priv2, pub2 = _sleutelpaar()
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    gemeld = []
    from homeassistant.core import callback
    hass.bus.async_listen(f"{DOMAIN}_gezicht_ontbreekt", callback(lambda e: gemeld.append(e.data)))
    k2 = await (await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "t2", "pubkey": pub2})).json()
    await hass.async_block_till_done()
    assert gemeld and gemeld[0]["personen"][0]["naam"] == "Lien Maes"
    l2 = await (await client.get(f"{API}/lijst", headers={"Authorization": f"Bearer {k2['sleutel']}"})).json()
    assert l2["personen"][0]["gezicht"] is False
    info = await (await client.get(eigen + "/info")).json()
    assert sorted(t["heeft"] for t in info["gezicht"]["tablets"]) == [False, True]
    # wissen via portaal (met code)
    r = await client.post(eigen + "/gezicht", json={"wissen": True, "huidige_pin": "740163"})
    assert r.status == 200 and p["gezicht"] is None
    assert (await (await client.get(f"{API}/lijst", headers=kop)).json())["personen"][0]["gezicht"] is False
    # opnieuw instellen, dan uit dienst: gewist
    info = await (await client.get(eigen + "/info")).json()
    r = await client.post(eigen + "/gezicht", json={**_gsm_pakket(info, afdrukken), "huidige_pin": "740163"})
    assert r.status == 200 and len(p["gezicht"]["sleutels"]) == 2
    await hass.services.async_call(DOMAIN, "persoon_uit_dienst", {"persoon": p["id"]}, blocking=True)
    assert p["gezicht"] is None
    assert any("gezichtsafdruk gewist" in (a.get("details") or a.get("detail") or "") for a in _reg(hass).data["audit"][-3:])
