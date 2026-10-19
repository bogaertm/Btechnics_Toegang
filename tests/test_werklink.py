"""Knop Toegang in Work: link met HMAC, 10 min geldig, een keer bruikbaar, daarna portaal zonder huidige code."""
import hashlib
import hmac
import time

import numpy as np

from custom_components.btechnics_toegang.const import DOMAIN

from .test_codes import API, _deur, _reg, hass_klaar  # noqa: F401
from .test_gezicht import _gsm_pakket, _sleutelpaar

GEHEIM = "x" * 40


def _link(werk_id, t=None, geheim=GEHEIM):
    t = t or int(time.time()) + 300
    s = hmac.new(geheim.encode(), f"{werk_id}.{t}".encode(), hashlib.sha256).hexdigest()
    return f"{API}/werklink?w={werk_id}&t={t}&s={s}"


async def test_werklink(hass_klaar, hass_client_no_auth):
    hass = hass_klaar
    client = await hass_client_no_auth()
    reg = _reg(hass)
    deur = _deur(hass, "Vergaderzaal")
    p = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur])
    p["werk_id"] = "w42"
    # nog geen geheim
    assert (await client.get(_link("w42"), allow_redirects=False)).status == 403
    # geheim via Work (IOT kanaal)
    await hass.services.async_call(DOMAIN, "werk", {"actie": "geheim_zetten", "velden": {"geheim": GEHEIM}}, blocking=True, return_response=True)
    # fout geheim, verlopen, te ver in de toekomst, onbekende medewerker
    assert (await client.get(_link("w42", geheim="y" * 40), allow_redirects=False)).status == 403
    assert (await client.get(_link("w42", t=int(time.time()) - 5), allow_redirects=False)).status == 403
    assert (await client.get(_link("w42", t=int(time.time()) + 3600), allow_redirects=False)).status == 403
    assert (await client.get(_link("w99"), allow_redirects=False)).status == 403
    # goede link: doorsturen naar een sessie, een keer bruikbaar
    link = _link("w42")
    r = await client.get(link, allow_redirects=False)
    assert r.status == 302
    sessie = r.headers["Location"]
    assert (await client.get(link, allow_redirects=False)).status == 403
    info = await (await client.get(sessie + "/info")).json()
    assert info["soort"] == "persoon" and info["vertrouwd"] is True and info["naam"] == "Jan Peeters"
    # code wijzigen zonder huidige code: nieuwe code twee keer
    r = await client.post(sessie + "/wijzig", json={"pin": "740163", "pin2": "740163", "telefoon": "1", "email": "j@p.be"})
    assert r.status == 200, await r.text()
    assert p["pin"] == "740163"
    # gezicht instellen zonder code (tablet met sleutel aan de deur)
    priv, pub = _sleutelpaar()
    code = (await hass.services.async_call(DOMAIN, "tablet_koppelcode", {"deur": "Vergaderzaal"}, blocking=True, return_response=True))["code"]
    await client.post(f"{API}/koppel", json={"code": code, "toestel_id": "t1", "pubkey": pub})
    info = await (await client.get(sessie + "/info")).json()
    afdrukken = [np.random.RandomState(i).randn(192).astype(np.float32) for i in range(3)]
    r = await client.post(sessie + "/gezicht", json=_gsm_pakket(info, afdrukken))
    assert r.status == 200, await r.text()
    assert p["gezicht"]
    # de gewone portaallink vraagt nog altijd de code
    gewoon = reg.portaal_token("test", p["id"])
    info = await (await client.get(f"{API}/portaal/{gewoon}/info")).json()
    assert info["vertrouwd"] is False
    r = await client.post(f"{API}/portaal/{gewoon}/wijzig", json={"telefoon": "2", "email": "j@p.be"})
    assert r.status == 400


async def test_werklink_audit(hass_klaar, hass_client_no_auth):
    """Audit 19/10: geen replay met hoofdletters of na herstart, enkel actieve medewerkers, uit dienst sluit alles."""
    hass = hass_klaar
    client = await hass_client_no_auth()
    reg = _reg(hass)
    deur = _deur(hass, "Vergaderzaal")
    p = reg.toevoegen("test", naam="Jan Peeters", pin="583920", deuren=[deur])
    p["werk_id"] = "w42"
    reg.werk_geheim_zetten("test", GEHEIM)
    link = _link("w42")
    assert (await client.get(link, allow_redirects=False)).status == 302
    # zelfde link met hoofdletters in s: geweigerd
    basis, s = link.rsplit("&s=", 1)
    assert (await client.get(basis + "&s=" + s.upper(), allow_redirects=False)).status == 403
    # gebruikte links staan in de opslag (overleven een herstart)
    assert any(k.startswith("w42.") for k in reg.data["werklinks"])
    # geblokkeerd: geen sessie
    reg.blokkeren("test", p["id"])
    assert (await client.get(_link("w42", t=int(time.time()) + 200), allow_redirects=False)).status == 403
    reg.deblokkeren("test", p["id"])
    r = await client.get(_link("w42", t=int(time.time()) + 250), allow_redirects=False)
    sessie = r.headers["Location"]
    vast = reg.portaal_token("test", p["id"])
    # te veel geweigerde codes: na 5 keer een uur dicht
    for i in range(5):
        r = await client.post(sessie + "/wijzig", json={"pin": "1234", "pin2": "1234", "telefoon": "1", "email": "j@p.be"})
        assert r.status == 400
    r = await client.post(sessie + "/wijzig", json={"pin": "740163", "pin2": "740163", "telefoon": "1", "email": "j@p.be"})
    assert r.status == 400 and "Te veel" in (await r.json())["fout"]
    # uit dienst: sessie en vaste link werken niet meer
    reg.uit_dienst("test", p["id"])
    assert (await client.get(sessie + "/info")).status == 404
    assert (await client.get(f"{API}/portaal/{vast}/info")).status == 404
