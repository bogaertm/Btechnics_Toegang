"""Eigen https poort voor de tablets: zelf gemaakt certificaat, vingerafdruk bij het koppelen, enkel https na de overgang."""
import hashlib
import ssl

import aiohttp
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.btechnics_toegang.const import DOMAIN

POORT = 18443


async def test_https_poort(hass, socket_enabled):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"https_poort": POORT})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "deur_toevoegen", {"naam": "Testdeur"}, blocking=True)
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Testdeur"}, blocking=True, return_response=True))["code"]
    h = hass.data[DOMAIN]["https"]
    assert h.fout is None and len(h.vingerafdruk) == 64
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession() as s:
        async with s.post(f"https://127.0.0.1:{POORT}/api/btechnics_toegang/koppel", json={"code": code, "toestel_id": "x"}, ssl=ctx) as r:
            assert r.status == 200
            antw = await r.json()
        pem = await hass.async_add_executor_job(ssl.get_server_certificate, ("127.0.0.1", POORT))
        der = ssl.PEM_cert_to_DER_cert(pem)
        assert antw["https"]["sha256"] == hashlib.sha256(der).hexdigest() and antw["https"]["poort"] == POORT
        kop = {"Authorization": f"Bearer {antw['sleutel']}"}
        async with s.get(f"https://127.0.0.1:{POORT}/api/btechnics_toegang/lijst", headers=kop, ssl=ctx) as r:
            assert r.status == 200 and (await r.json())["deur"]["naam"] == "Testdeur"
        assert hass.data[DOMAIN]["register"].data["tablets"][antw["tablet_id"]]["kanaal"] == "https"
    # enkel https: http weigert, https werkt
    hass.config_entries.async_update_entry(entry, options={"https_poort": POORT, "enkel_https": True})
    await hass.async_block_till_done()
    async with aiohttp.ClientSession() as s:
        async with s.get(f"https://127.0.0.1:{POORT}/api/btechnics_toegang/lijst", headers=kop, ssl=ctx) as r:
            assert r.status == 200
    await hass.async_stop()
