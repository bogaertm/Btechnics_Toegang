"""Schema 2: strengheid gezicht per deur en app update voor de tablets via HA (v0.14.0)."""
import hashlib

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.btechnics_toegang.const import DOMAIN

from .test_flow import API, dienst, koppel, opgezet  # noqa: F401

APK = b"PK\x03\x04" + bytes(range(256)) * 40   # nep apk: HA kijkt enkel naar grootte en sha256


async def test_strengheid_per_deur(opgezet):
    hass, client = opgezet
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal")
    kop, _ = await koppel(hass, client)
    reg = hass.data[DOMAIN]["register"]
    v = reg.versie
    await dienst(hass, "deur_wijzigen", deur="Vergaderzaal", gezicht_drempel=0.65)
    lijst = await (await client.get(f"{API}/lijst", headers=kop)).json()
    assert lijst["deur"]["gezicht_drempel"] == 0.65 and lijst["versie"] > v
    with pytest.raises((HomeAssistantError, ServiceValidationError, Exception)):
        await dienst(hass, "deur_wijzigen", deur="Vergaderzaal", gezicht_drempel=0.3)
    await dienst(hass, "deur_wijzigen", deur="Vergaderzaal", gezicht_drempel="")
    lijst = await (await client.get(f"{API}/lijst", headers=kop)).json()
    assert lijst["deur"]["gezicht_drempel"] is None


async def test_app_update(opgezet, hass_client):
    hass, client = opgezet
    await dienst(hass, "deur_toevoegen", naam="Vergaderzaal")
    kop, _ = await koppel(hass, client)
    beheer = await hass_client()
    assert (await client.get(f"{API}/apk", headers=kop)).status == 404
    sha = hashlib.sha256(APK).hexdigest()
    # zonder login geen upload
    assert (await client.post(f"{API}/apk_upload?version_code=66&versie=1.72&sha256={sha}", data=APK)).status == 401
    # foute sha256: niets bewaard
    r = await beheer.post(f"{API}/apk_upload?version_code=66&versie=1.72&sha256={'0' * 64}", data=APK)
    assert r.status == 400 and hass.data[DOMAIN]["register"].data.get("app") is None
    r = await beheer.post(f"{API}/apk_upload?version_code=66&versie=1.72&sha256={sha}", data=APK)
    assert r.status == 200 and (await r.json())["grootte"] == len(APK)
    lijst = await (await client.get(f"{API}/lijst", headers=kop)).json()
    assert lijst["app"] == {"version_code": 66, "versie": "1.72", "sha256": sha, "grootte": len(APK)}
    r = await client.get(f"{API}/apk", headers=kop)
    assert r.status == 200 and await r.read() == APK
    assert r.headers["Content-Type"] == "application/vnd.android.package-archive"
    # zonder tabletsleutel niets
    assert (await client.get(f"{API}/apk")).status == 401
    # oudere versie wordt geweigerd
    r = await beheer.post(f"{API}/apk_upload?version_code=60&versie=1.66&sha256={sha}", data=APK)
    assert r.status == 409
    # via de tunnel geweigerd
    r = await beheer.post(f"{API}/apk_upload?version_code=67&versie=1.73&sha256={sha}", data=APK, headers={"Cf-Connecting-Ip": "1.2.3.4"})
    assert r.status == 403
