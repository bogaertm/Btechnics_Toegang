"""Gezichtsserver: keten tegen de referentie van de gsm, ontsleutelen, beslissen."""
import base64
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from btx_gezicht.beslissing import Beslisser, Instellingen  # noqa: E402
from btx_gezicht.ha import HA, OAEP  # noqa: E402
from btx_gezicht.herkenning import Gezicht, Herkenner, VORM  # noqa: E402

REF = Path("/home/claude/gezicht")


def test_keten_gelijk_aan_gsm():
    h = Herkenner()
    rgb = cv2.cvtColor(cv2.imread(str(REF / "astronaut.png")), cv2.COLOR_BGR2RGB)
    g = h.gezichten(rgb, 1)[0]
    a = h.afdruk(rgb, g)
    ref = np.fromfile(REF / "mp_ref.bin", "<f4")
    # punten via een vergrote uitsnede: kleine verschuiving tegenover de gsm (die het hele beeld gebruikt)
    assert float(a @ (ref / np.linalg.norm(ref))) > 0.995
    assert 0 <= h.echtheid(rgb, g) <= 1


def test_ontsleutelen(tmp_path):
    ha = HA("https://127.0.0.1:1", tmp_path, "Test", "0")
    af = np.random.RandomState(1).randn(3, 192).astype("<f4")
    sleutel, iv = AESGCM.generate_key(256), os.urandom(12)
    aad = "btx-gezicht-v1:p_1"
    klaar = json.dumps({"versie": "mfn112-arcface5-v1", "afdrukken": [base64.b64encode(x.tobytes()).decode() for x in af]}).encode()
    pub = serialization.load_der_public_key(base64.b64decode(ha.pubkey))
    gz = {"pubkey_id": ha.pubkey_id, "sleutel": base64.b64encode(pub.encrypt(sleutel, OAEP)).decode(), "aad": aad,
          "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(AESGCM(sleutel).encrypt(iv, klaar, aad.encode())).decode()}
    uit = ha.ontsleutel(gz)
    assert uit.shape == (3, 192) and np.allclose(np.linalg.norm(uit, axis=1), 1)
    # sleutel blijft bewaard: zelfde id na herstart
    assert HA("https://127.0.0.1:1", tmp_path, "Test", "0").pubkey_id == ha.pubkey_id


class NepH:
    """Herkenner zonder modellen: afdruk en echtheid komen uit het gezicht zelf."""
    def afdruk(self, rgb, g):
        g.scherpte = 100.0
        g.afdruk = g.extra["afdruk"]
        return g.afdruk

    def echtheid(self, rgb, g):
        g.echt = g.extra["echt"]
        return g.echt

    @staticmethod
    def vorm(g):
        return g.punten[VORM, :2].copy()


def _gezicht(oog, afdruk, echt, vlak=False):
    rs = np.random.RandomState(int(oog * 10) % 1000)
    basis = np.random.RandomState(7).rand(478, 3) * 100
    s = oog / 60.0
    p = basis.copy()
    p[:, :2] = basis[:, :2] * s + 500
    if not vlak:   # perspectief: hoe dichter, hoe meer de neus naar voren komt
        p[:, :2] += (basis[:, 2:3] - 50) * (s ** 2) * 0.08
    p[:, :2] += rs.randn(478, 2) * 0.15
    return Gezicht(punten=p, vijf=np.zeros((5, 2)), oog=oog, draai=1, kantel=2, vak=(500, 500, 100, 100), midden=(550.0, 550.0),
                   extra={"afdruk": afdruk, "echt": echt})


def _loop(b, personen, afdruk, echt, vlak=False, naderen=True):
    besluiten = []
    t = 100.0
    ogen = list(np.linspace(40, 100, 12)) if naderen else []
    ogen += [100.0] * 20
    for oog in ogen:
        r = b.stap(NepH(), None, _gezicht(oog, afdruk, echt, vlak), personen, t)
        if r:
            besluiten.append(r)
        t += 1 / 6
    return besluiten


def test_beslissen():
    rs = np.random.RandomState(3)
    jan = rs.randn(192); jan /= np.linalg.norm(jan)
    lien = rs.randn(192); lien /= np.linalg.norm(lien)
    personen = {"p_jan": {"naam": "Jan", "afdrukken": np.stack([jan])}, "p_lien": {"naam": "Lien", "afdrukken": np.stack([lien])}}
    bijna = jan + 0.3 * rs.randn(192) / np.sqrt(192); bijna /= np.linalg.norm(bijna)
    # echt hoofd dat nadert: open voor Jan, een keer
    r = _loop(Beslisser(Instellingen()), personen, bijna, 0.93)
    assert [x["actie"] for x in r] == ["open"] and r[0]["persoon_id"] == "p_jan" and r[0]["diepte"] > 2.5, r
    # vlakke foto die nadert, echtheid middelmatig: geen 3D, dus onzeker
    r = _loop(Beslisser(Instellingen()), personen, bijna, 0.93, vlak=True)
    assert [x["actie"] for x in r] == ["onzeker"], r
    # lage echtheid: vermoedelijke foto en daarna geblokkeerd
    b = Beslisser(Instellingen())
    assert [x["actie"] for x in _loop(b, personen, bijna, 0.3)] == ["foto"]
    b.sessie = None
    assert [x["actie"] for x in _loop(b, personen, bijna, 0.99)] == ["geblokkeerd"]
    # onbekend gezicht
    vreemd = rs.randn(192); vreemd /= np.linalg.norm(vreemd)
    assert [x["actie"] for x in _loop(Beslisser(Instellingen()), personen, vreemd, 0.99)] == ["onbekend"]
    # te ver weg: niets
    b = Beslisser(Instellingen(oog_min=200))
    assert _loop(b, personen, bijna, 0.99) == []


def test_herverpakken(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    import hashlib
    ha = HA("https://127.0.0.1:1", tmp_path, "Test", "0")
    ha.staat["sleutel"] = "x"
    aes = AESGCM.generate_key(256)
    eigen = serialization.load_der_public_key(base64.b64decode(ha.pubkey))
    doel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = doel.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    taak = {"persoon_id": "p_1", "sleutel": base64.b64encode(eigen.encrypt(aes, OAEP)).decode(), "aad": "btx-gezicht-v1:p_1",
            "doel": [{"pubkey_id": hashlib.sha256(der).hexdigest(), "pubkey": base64.b64encode(der).decode()},
                     {"pubkey_id": "0" * 64, "pubkey": base64.b64encode(der).decode()}]}   # tweede: id klopt niet, overslaan
    gepost = []

    class Antw:
        def __init__(self, d): self.d = d
        def raise_for_status(self): pass
        def json(self): return self.d

    class Sessie:
        def get(self, url, **kw): return Antw({"taken": [taak]})
        def post(self, url, json=None, **kw):
            gepost.append(json)
            return Antw({"nieuw": len(json["sleutels"])})

    ha.sessie = Sessie()
    assert ha.herverpakken() == 1
    (kid, verpakt), = gepost[0]["sleutels"].items()
    assert doel.decrypt(base64.b64decode(verpakt), OAEP) == aes
