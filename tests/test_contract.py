"""Gedeelde testset (map contract/): echte voorbeelden van verzoeken en antwoorden, en de verwachte uitkomst.

De APK chat draait dezelfde bestanden in regressie.mjs. Deze test controleert dat HA nog altijd antwoordt in de vorm
die in contract/ staat (velden en types; ids en tijdstippen mogen verschillen).
Nieuwe voorbeelden schrijven: CONTRACT_SCHRIJF=1 pytest tests/test_contract.py
"""
import base64
import json
import os
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.btechnics_toegang.const import DOMAIN
from custom_components.btechnics_toegang.hashing import hash_waarde

from .test_codes import JPEG

MAP = Path(__file__).parent.parent / "contract"
SCHRIJF = os.environ.get("CONTRACT_SCHRIJF") == "1"
API = "/api/btechnics_toegang"
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)


def vorm(x):
    """Velden en types, zonder de waarden (ids, tijden en hashes verschillen per run)."""
    if isinstance(x, dict):
        return {k: vorm(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return [vorm(x[0])] if x else []
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, (int, float)):
        return "getal"
    if x is None:
        return "null"
    return "tekst"


def vergelijk(naam: str, inhoud: dict) -> None:
    pad = MAP / f"{naam}.json"
    if SCHRIJF:
        pad.write_text(json.dumps(inhoud, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return
    oud = json.loads(pad.read_text("utf-8"))
    for k in inhoud:
        if k.startswith("_"):
            continue
        assert vorm(inhoud[k]) == vorm(oud[k]), f"contract {naam}.{k} wijkt af van contract/{naam}.json"


async def test_contract(hass, hass_client_no_auth):
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    client = await hass_client_no_auth()
    svc = lambda n, **d: hass.services.async_call(DOMAIN, n, d, blocking=True, return_response=True)  # noqa: E731

    # hash: testvectoren (zout + 2000 rondes sha256)
    vectoren = [{"zout": z, "waarde": w, "hash": hash_waarde(z, w)} for z, w in (("test", "1234"), ("a1b2c3d4e5f6a7b8c9d0e1f2", "740163"),
                                                                                ("a1b2c3d4e5f6a7b8c9d0e1f2", "04A1B2C3"))]
    assert vectoren[0]["hash"] == "78ddd1e09efdd4c9c9b044ffee6ea076b05e95441fc2ead652e0c46809e31824"
    vergelijk("hash", {"_uitleg": "sha256(zout + 'zout:waarde'), daarna 1999 keer sha256(zout + vorige digest), lowercase hex",
                       "vectoren": vectoren})

    # testdeur, persoon met code, persoon met gezicht
    t = await svc("testdeur_klaarzetten")
    await svc("deur_wijzigen", deur=t["deur_id"], open_entiteit="switch.testrelais")
    priv = serialization.load_pem_private_key((MAP / "testsleutel-tablet.pem").read_bytes(), None)
    pub = base64.b64encode(priv.public_key().public_bytes(serialization.Encoding.DER,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    koppel_verzoek = {"code": t["koppelcode"], "toestel_id": "contract-test", "naam": "Testtablet", "app_versie": "1.64", "pubkey": pub}
    r = await client.post(f"{API}/koppel", json=koppel_verzoek)
    koppel = await r.json()
    assert r.status == 200 and koppel["pubkey_id"]
    vergelijk("koppel", {"_uitleg": "POST /koppel zonder sleutel. https is null als de https poort niet draait.",
                         "verzoek": koppel_verzoek | {"code": "123456", "pubkey": "<SPKI DER base64>"},
                         "antwoord": koppel | {"https": koppel["https"] or {"poort": 8443, "sha256": "64 hex"}},
                         "fout_antwoord": {"fout": "Koppelcode ongeldig of vervallen"}})
    kop = {"Authorization": f"Bearer {koppel['sleutel']}"}

    # persoon met gezicht via het portaal
    r = await svc("uitnodiging_maken", deuren=[t["deur_id"]], naam="Gezicht Test")
    pad = r["url"][r["url"].index("/api/"):]
    r = await client.post(pad + "/aanmelden", json={"voornaam": "Gezicht", "achternaam": "Test", "telefoon": "1", "email": "g@t.be",
                                                     "akkoord": True, "pin": "582947", "pin2": "582947", "foto": JPEG})
    eigen = (await r.json())["url"]
    eigen = eigen[eigen.index("/api/"):]
    info = (await (await client.get(eigen + "/info")).json())["gezicht"]
    afdrukken = [np.random.RandomState(i).randn(192).astype("<f4") for i in range(3)]
    afdrukken = [(a / np.linalg.norm(a)).astype("<f4") for a in afdrukken]
    klaar = {"versie": info["versie"], "bron": "gsm", "punten": "mediapipe-mesh 468 473 2 61 291",
             "afdrukken": [base64.b64encode(a.tobytes()).decode() for a in afdrukken]}
    sleutel, iv = AESGCM.generate_key(256), os.urandom(12)
    data = AESGCM(sleutel).encrypt(iv, json.dumps(klaar).encode(), info["aad"].encode())
    pakket = {"versie": info["versie"], "toestemming": "v1", "toestemming_hash": info["toestemming"]["hash"], "aantal": 3,
              "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(data).decode(),
              "sleutels": {info["tablets"][0]["pubkey_id"]: base64.b64encode(priv.public_key().encrypt(sleutel, OAEP)).decode()}}
    assert (await client.post(eigen + "/gezicht", json={**pakket, "huidige_pin": "582947"})).status == 200

    # lijst
    r = await client.get(f"{API}/lijst", headers=kop)
    lijst = await r.json()
    etag = r.headers["ETag"]
    assert (await client.get(f"{API}/lijst", headers={**kop, "If-None-Match": etag})).status == 304
    met_gezicht = next(p for p in lijst["personen"] if p["gezicht"])
    zonder = next(p for p in lijst["personen"] if not p["gezicht"])
    # de tablet ontsleutelt het pakket met zijn eigen sleutel
    g = met_gezicht["gezicht"]
    aes = priv.decrypt(base64.b64decode(g["sleutel"]), OAEP)
    terug = json.loads(AESGCM(aes).decrypt(base64.b64decode(g["iv"]), base64.b64decode(g["data"]), g["aad"].encode()))
    assert terug == klaar
    vergelijk("lijst", {"_uitleg": "GET /lijst met Authorization: Bearer <sleutel>; ETag en If-None-Match geeft 304. "
                                   "gezicht is false of een object; enkel voor de pubkey van deze tablet.",
                        "antwoord": {**lijst, "personen": [zonder, met_gezicht]}})
    vergelijk("gezicht", {"_uitleg": "Pakket zoals de gsm het maakt en de tablet het ontsleutelt. RSA-OAEP SHA-1 (MGF1 SHA-1) op 'sleutel' "
                                     "met contract/testsleutel-tablet.pem geeft de AES-256 sleutel; AES-GCM met iv en aad (UTF-8) op 'data' "
                                     "(tag van 16 bytes achteraan) geeft 'klaar'. Afdrukken: 192 float32 little endian.",
                          "in_de_lijst": g, "klaar": klaar,
                          "afdruk_0_eerste_5": [round(float(v), 6) for v in afdrukken[0][:5]]})

    # log: toegang, geweigerd, vermoedelijke foto, melding
    tijd = "2026-10-17T16:30:00+02:00"
    items = [{"uid": "c1", "tijd": tijd, "soort": "toegang", "methode": "pin", "persoon_id": zonder["id"], "tekst": "met code"},
             {"uid": "c2", "tijd": tijd, "soort": "geweigerd", "methode": "pin", "reden": "onbekend", "persoon_id": None},
             {"uid": "c3", "tijd": tijd, "soort": "geweigerd", "methode": "gezicht", "reden": "vermoedelijke_foto",
              "persoon_id": met_gezicht["id"], "score": 0.12},
             {"uid": "c4", "tijd": tijd, "soort": "systeem", "tekst": "app gestart"}]
    r = await client.post(f"{API}/log", headers=kop, json={"items": items})
    log_antw = await r.json()
    rij = await hass.async_add_executor_job(hass.data[DOMAIN]["toegang"].archief.recent, t["deur_id"], 10)
    verwacht = sorted([{"naam": x["name"], "manier": x["method"], "geopend": bool(x["opened"])} for x in rij], key=lambda x: x["naam"])
    assert {v["manier"] for v in verwacht} == {"code", "gezicht (vermoedelijke foto)"}
    vergelijk("log", {"_uitleg": "POST /log {items}; antwoord ontvangen = uids die HA kreeg (ook dubbele). Toegang en geweigerd gaan "
                                 "naar de historiek, de rest naar de tabletmeldingen.",
                      "verzoek": {"items": items}, "antwoord": log_antw, "historiek": verwacht})

    # open
    ok = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": zonder["id"]})
    nee = await client.post(f"{API}/open", headers=kop, json={"methode": "pin", "persoon_id": None})
    assert ok.status == 200 and nee.status == 403
    vergelijk("open", {"_uitleg": "POST /open; de tablet logt zelf (log ontbreekt of true). Met log:false logt HA. "
                                  "200 geopend, 403 geweigerd met reden onbekend, buiten_schema of verlopen, 502 fout bij het relais.",
                       "verzoek": {"methode": "pin", "persoon_id": zonder["id"]}, "antwoord_200": await ok.json(),
                       "verzoek_onbekend": {"methode": "pin", "persoon_id": None}, "antwoord_403": await nee.json()})

    # status
    status_verzoek = {"app_versie": "1.64", "version_code": 58, "versie": lijst["versie"], "log_wachtrij": 0, "schema": 1,
                      "personen": len(lijst["personen"]), "gezichten": 1, "laatste_lijst": "2026-10-17T16:30:00+02:00", "pubkey": pub}
    r = await client.post(f"{API}/status", headers=kop, json=status_verzoek)
    vergelijk("status", {"_uitleg": "POST /status elke minuut. pubkey mag elke keer mee. personen, gezichten en laatste_lijst "
                                    "toont het dashboard (gezichten in orde als gelijk aan wat HA verwacht).",
                         "verzoek": status_verzoek | {"pubkey": "<SPKI DER base64>"}, "antwoord": await r.json()})
    assert (await client.post(f"{API}/status", headers={"Authorization": "Bearer fout"}, json={})).status == 401

    await svc("testdeur_opruimen")
