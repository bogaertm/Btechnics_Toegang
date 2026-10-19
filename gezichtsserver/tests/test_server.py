"""Gezichtsserver: keten tegen de referentie van de gsm, ontsleutelen, beslissen."""
import base64
import hashlib
import json
import logging
import os
import stat
import sys
import threading
from pathlib import Path

import pytest
import requests

import cv2
import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from btx_gezicht.beslissing import Beslisser, Instellingen, Meting  # noqa: E402
from btx_gezicht.ha import HA, OAEP, GeenOmleiding  # noqa: E402
from btx_gezicht.herkenning import Gezicht, Herkenner, VORM, gelijkvormig, vorm_afwijking  # noqa: E402
from btx_gezicht import __main__ as hoofd  # noqa: E402

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
    # sleutel en staat enkel leesbaar voor de eigenaar
    assert stat.S_IMODE(os.stat(tmp_path / "sleutel.pem").st_mode) == 0o600
    ha.staat["x"] = 1
    ha._bewaar()
    assert stat.S_IMODE(os.stat(tmp_path / "staat.json").st_mode) == 0o600


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


def _gezicht(oog, afdruk, echt, vlak=False, schuin=False, midden=(550.0, 550.0)):
    rs = np.random.RandomState(int(oog * 10) % 1000)
    basis = np.random.RandomState(7).rand(478, 3) * 100
    s = oog / 60.0
    p = basis.copy()
    p[:, :2] = basis[:, :2] * s + 500
    if not vlak:   # perspectief: hoe dichter, hoe meer de neus naar voren komt
        p[:, :2] += (basis[:, 2:3] - 50) * (s ** 2) * 0.08
    if schuin:     # vlak (foto of scherm) schuin gehouden: projectieve vervorming die verandert bij naderen
        c = np.array([550.0, 550.0])
        q = p[:, :2] - c
        w = 1 + 0.0015 * s * q[:, 0] + 0.0008 * s * q[:, 1]
        p[:, :2] = q / w[:, None] + c
    p[:, :2] += rs.randn(478, 2) * 0.15
    return Gezicht(punten=p, vijf=np.zeros((5, 2)), oog=oog, draai=1, kantel=2, vak=(500, 500, 100, 100), midden=midden,
                   extra={"afdruk": afdruk, "echt": echt})


def _loop(b, personen, afdruk, echt, vlak=False, naderen=True, schuin=False, dicht=20, anderen=None, t=100.0):
    besluiten = []
    ogen = list(np.linspace(40, 100, 12)) if naderen else []
    ogen += [100.0] * dicht
    for oog in ogen:
        a = afdruk(oog) if callable(afdruk) else afdruk
        r = b.stap(NepH(), None, _gezicht(oog, a, echt, vlak, schuin), personen, t, anderen)
        if r:
            besluiten.append(r)
        t += 1 / 6
    return besluiten


def _personen():
    rs = np.random.RandomState(3)
    jan = rs.randn(192); jan /= np.linalg.norm(jan)
    lien = rs.randn(192); lien /= np.linalg.norm(lien)
    personen = {"p_jan": {"naam": "Jan", "afdrukken": np.stack([jan])}, "p_lien": {"naam": "Lien", "afdrukken": np.stack([lien])}}
    bijna = jan + 0.3 * rs.randn(192) / np.sqrt(192); bijna /= np.linalg.norm(bijna)
    return personen, jan, lien, bijna


def _met_cos(doel, c, ook=()):
    """Afdruk met cosinus c tegenover doel en (bijna) 0 tegenover de andere."""
    u = np.random.RandomState(11).randn(192)
    for v in (doel, *ook):
        u -= (u @ v) * v
    u /= np.linalg.norm(u)
    a = c * doel + np.sqrt(1 - c * c) * u
    return a / np.linalg.norm(a)


def test_vorm_homografie():
    """Punt 3: een vlak, ook schuin, wordt volledig verklaard door een homografie; een echt hoofd niet."""
    rs = np.random.RandomState(5)
    xy = rs.uniform(-60, 60, (len(VORM), 2))           # mm, rond het midden van het gezicht
    diep = rs.uniform(0, 40, len(VORM))                # neus tot 40 mm voor het vlak van de ogen

    def projecteer(punten3, afstand, f=500.0):
        return f * punten3[:, :2] / (afstand - punten3[:, 2:3]) + 352

    hoofd = np.c_[xy, diep]
    vlak = np.c_[xy, 0.6 * xy[:, 0] + 0.3 * xy[:, 1]]  # schuin gehouden foto of scherm
    for z_ver, z_dicht in ((900, 350), (600, 300)):
        f_vlak = vorm_afwijking(projecteer(vlak, z_ver), projecteer(vlak, z_dicht))
        f_hoofd = vorm_afwijking(projecteer(hoofd, z_ver), projecteer(hoofd, z_dicht))
        assert f_vlak < 1e-6, f_vlak
        assert f_hoofd > 0.01, f_hoofd
        # ter vergelijking: na enkel gelijkvormigheid bleef er bij het schuine vlak een grote rest over
        a, b = projecteer(vlak, z_ver), projecteer(vlak, z_dicht)
        m = gelijkvormig(a, b)
        assert np.sqrt((((a @ m[:, :2].T + m[:, 2]) - b) ** 2).sum(1).mean()) / np.sqrt(((b - b.mean(0)) ** 2).sum(1).mean()) > 0.01
    # geen homografie mogelijk: nan, nooit een groot getal
    assert np.isnan(vorm_afwijking(np.zeros((3, 2)), np.zeros((3, 2))))
    # enkel rigide punten: geen mond (61, 291, 13, 14) of kaak (172, 397, 234, 454, 58, 288)
    assert not set(VORM) & {61, 291, 13, 14, 172, 397, 234, 454, 58, 288, 152}


def test_beslissen():
    personen, jan, lien, bijna = _personen()
    # echt hoofd dat nadert: open voor Jan, een keer
    r = _loop(Beslisser(Instellingen()), personen, bijna, 0.93)
    assert [x["actie"] for x in r] == ["open"] and r[0]["persoon_id"] == "p_jan" and r[0]["diepte"] > 2.5, r
    # vlakke foto die nadert, recht of schuin: geen 3D, dus nooit open
    for schuin in (False, True):
        r = _loop(Beslisser(Instellingen()), personen, bijna, 0.99, vlak=True, schuin=schuin)
        assert [x["actie"] for x in r] == ["onzeker"] and r[0]["diepte"] < 2.5, r
        # zonder 3D maar echtheid boven echt_min: onzeker (nooit open, geen beschuldiging van een foto)
        r = _loop(Beslisser(Instellingen()), personen, bijna, 0.93, vlak=True, schuin=schuin)
        assert [x["actie"] for x in r] == ["onzeker"], r
    # punt 2: hoge echtheid zonder naderen (geen diepte) opent nooit meer
    r = _loop(Beslisser(Instellingen()), personen, bijna, 0.999, naderen=False)
    assert [x["actie"] for x in r] == ["onzeker"] and r[0]["diepte"] is None, r
    # lage echtheid: vermoedelijke foto, maar niemand wordt daarna buitengesloten (punt 7)
    b = Beslisser(Instellingen())
    assert [x["actie"] for x in _loop(b, personen, bijna, 0.3)] == ["foto"]
    assert not hasattr(b, "geblokkeerd")
    b.sessie = None
    assert [x["actie"] for x in _loop(b, personen, bijna, 0.93, t=200.0)] == ["open"]
    assert b.stats["foto"] == 1
    # onbekend gezicht
    vreemd = np.random.RandomState(4).randn(192); vreemd /= np.linalg.norm(vreemd)
    assert [x["actie"] for x in _loop(Beslisser(Instellingen()), personen, vreemd, 0.99)] == ["onbekend"]
    # te ver weg: niets
    b = Beslisser(Instellingen(oog_min=200))
    assert _loop(b, personen, bijna, 0.99) == []


def test_sessie_is_een_spoor():
    """Punt 1: een sprong in plaats, schaal of tijd start een nieuwe sessie; diepte enkel binnen hetzelfde spoor."""
    personen, jan, lien, bijna = _personen()

    def loop(sprong):
        b = Beslisser(Instellingen())
        t, uit = 100.0, []
        ogen = list(np.linspace(40, 100, 12)) + [100.0] * 20
        if sprong == "schaal":
            ogen = list(np.linspace(40, 65, 8)) + [100.0] * 20
        for i, oog in enumerate(ogen):
            dicht = oog >= 100 and i >= len(ogen) - 20
            midden = (700.0, 550.0) if (sprong == "midden" and dicht) else (550.0, 550.0)
            if sprong == "tijd" and dicht and i == len(ogen) - 20:
                t += 1.5
            g = _gezicht(oog, bijna, 0.99, midden=midden)
            r = b.stap(NepH(), None, g, personen, t)
            if r:
                uit.append(r)
            t += 1 / 6
        return b, uit

    b, r = loop(None)
    assert [x["actie"] for x in r] == ["open"] and b.stats["sessies"] == 1
    for sprong in ("midden", "schaal", "tijd"):
        b, r = loop(sprong)
        assert b.stats["sessies"] == 2, sprong
        assert [x["actie"] for x in r] == ["onzeker"] and r[0]["diepte"] is None, (sprong, r)


def test_diepte_enkel_zelfde_persoon():
    """Punt 1: verre beelden met een afdruk van een andere persoon tellen niet voor de diepte."""
    personen, jan, lien, bijna = _personen()
    lien_bijna = _met_cos(lien, 0.8, (jan,))
    r = _loop(Beslisser(Instellingen()), personen, lambda oog: lien_bijna if oog < 70 else bijna, 0.99)
    assert [x["actie"] for x in r] == ["onzeker"] and r[0]["persoon_id"] == "p_jan" and r[0]["diepte"] is None, r
    b = Beslisser(Instellingen())
    _loop(b, personen, lambda oog: lien_bijna if oog < 70 else bijna, 0.99)
    assert any(m.persoon == "p_lien" for m in b.sessie.metingen if not m.dichtbij)


def test_max_beslisduur():
    """Punt 4: twijfel (cos net onder de drempel) eindigt na 6 s in een keer onbekend, daarna niets meer."""
    personen, jan, lien, bijna = _personen()
    twijfel = _met_cos(jan, 0.62, (lien,))
    b = Beslisser(Instellingen())
    r = _loop(b, personen, twijfel, 0.99, naderen=False, dicht=80)
    assert [x["actie"] for x in r] == ["onbekend"] and r[0]["reden"] == "te_lang", r
    assert b.sessie.gemeld and b.stats["onbekend"] == 1
    # het besluit viel na 6 s dichtbij en recht, niet eerder
    b = Beslisser(Instellingen())
    assert _loop(b, personen, twijfel, 0.99, naderen=False, dicht=35) == []


def test_cohorte_en_standaarden():
    """Punt 5: met een persoon valt de tweede niet naar 0 maar op de cohorte."""
    inst = Instellingen()
    assert (inst.drempel, inst.marge, inst.cohorte) == (0.70, 0.10, 0.30)
    personen, jan, lien, bijna = _personen()
    een = {"p_jan": personen["p_jan"]}
    b = Beslisser(inst)
    pid, cos, tweede = b.match(_met_cos(jan, 0.75), een)
    assert pid == "p_jan" and abs(cos - 0.75) < 1e-6 and tweede == 0.30
    # cos 0.38 haalt met een persoon de marge tegenover de cohorte niet meer (vroeger: tweede 0)
    pid, cos, tweede = Beslisser(Instellingen(cohorte=0.30)).match(_met_cos(jan, 0.38), een)
    assert cos - tweede < 0.10
    # een tweede persoon boven de cohorte telt gewoon
    pid, cos, tweede = b.match(0.6 * jan + 0.8 * lien, personen)
    assert pid == "p_lien" and 0.55 < tweede < 0.65
    # een persoon: open blijft mogelijk
    r = _loop(Beslisser(inst), een, bijna, 0.93)
    assert [x["actie"] for x in r] == ["open"] and r[0]["tweede"] == 0.3, r


def test_meerdere_gezichten():
    """Punt 6: een tweede groot gezicht: geen besluit, sessie gemarkeerd, vermeld in de samenvatting."""
    personen, jan, lien, bijna = _personen()
    groot = _gezicht(70, lien, 0.99)
    b = Beslisser(Instellingen(), meten=True)
    assert _loop(b, personen, bijna, 0.93, anderen=[groot]) == []
    assert b.sessie.meerdere and b.stats["meerdere"] == 1
    b.stap(NepH(), None, None, personen, 200.0)
    assert b.samenvatting["meerdere"] is True
    assert "meerdere gezichten" in hoofd.samenvatting_tekst(b.samenvatting, "Jan")
    # klein gezicht in de achtergrond (oog 40 < 0,6 x 100): gewoon open
    klein = _gezicht(40, lien, 0.99)
    r = _loop(Beslisser(Instellingen()), personen, bijna, 0.93, anderen=[klein])
    assert [x["actie"] for x in r] == ["open"], r


def test_meten_en_samenvatting():
    """Punt 13: eigen teller, deque op maat van fps, cos mediaan apart voor dichtbij en ver."""
    personen, jan, lien, bijna = _personen()
    assert Beslisser(Instellingen(stil_s=2), fps=50).maxlen == 300
    assert Beslisser(Instellingen(stil_s=2), fps=8).maxlen == 200
    b = Beslisser(Instellingen(), meten=True, fps=8)
    _loop(b, personen, bijna, 0.93, dicht=300)
    s = b.sessie
    assert s.teller == 312 and len(s.metingen) == 200
    b.stap(NepH(), None, None, personen, 1000.0)
    z = b.samenvatting
    assert z["beelden"] == 312 and z["cos_mediaan_dicht"] is not None and z["meerdere"] is False
    # de verre beelden vielen uit de begrensde deque; met een korte sessie zijn ze er wel
    b = Beslisser(Instellingen(), meten=True)
    _loop(b, personen, bijna, 0.93)
    b.stap(NepH(), None, None, personen, 1000.0)
    z = b.samenvatting
    assert z["cos_mediaan_ver"] is not None and z["cos_mediaan_dicht"] is not None
    assert "ver" in hoofd.samenvatting_tekst(z, "Jan")


def test_meting_eq_identiteit():
    """Punt 14: Meting vergelijkt op identiteit (geen numpy vergelijking van vorm)."""
    a = Meting(t=1, oog=1, midden=(0, 0), vorm=np.zeros((3, 2)), recht=True, dichtbij=True)
    b = Meting(t=1, oog=1, midden=(0, 0), vorm=np.zeros((3, 2)), recht=True, dichtbij=True)
    assert a != b and a == a and [a, b].index(b) == 1


def test_herverpakken(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
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
    logs = []
    ha.log = lambda soort, **v: logs.append((soort, v))

    class Antw:
        status_code = 200
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
    assert logs == [("systeem", {"tekst": "Afdruk doorgezet naar 1 toestel(len)"})]

    # punt 11: zwakke sleutel, andere server en onleesbare sleutel worden geweigerd zonder de rest over te slaan
    def doelsleutel(bits):
        k = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        d = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return k, {"pubkey_id": hashlib.sha256(d).hexdigest(), "pubkey": base64.b64encode(d).decode()}

    _, zwak = doelsleutel(1024)
    _, server = doelsleutel(2048)
    server["soort"] = "server"
    rommel = {"pubkey_id": hashlib.sha256(b"rommel").hexdigest(), "pubkey": base64.b64encode(b"rommel").decode()}
    goed_k, goed = doelsleutel(3072)
    goed["soort"] = "tablet"
    taak["doel"] = [zwak, rommel, server, goed]
    gepost.clear(); logs.clear()
    assert ha.herverpakken() == 1
    assert list(gepost[0]["sleutels"]) == [goed["pubkey_id"]]
    assert goed_k.decrypt(base64.b64decode(gepost[0]["sleutels"][goed["pubkey_id"]]), OAEP) == aes
    assert len(logs) == 1 and "goed" not in str(logs) and "p_1" not in str(logs)
    # enkel slechte doelen: niets gepost
    taak["doel"] = [zwak, server]
    gepost.clear()
    assert ha.herverpakken() == 0 and gepost == []


class _Antw:
    def __init__(self, code, d=None, headers=None):
        self.status_code, self.d, self.headers = code, d, headers or {}
        self.text = json.dumps(d)
    def json(self):
        if self.d is None:
            raise ValueError("geen json")
        return self.d


def _ha(tmp_path):
    ha = HA("https://127.0.0.1:1", tmp_path, "Test", "0")
    ha.staat["sleutel"] = "x"
    return ha


def test_ha_https_en_geen_omleiding(tmp_path, monkeypatch):
    """Punt 10: enkel https, ook na koppelen; nooit een omleiding volgen."""
    with pytest.raises(SystemExit):
        HA("http://192.168.1.117:8123", tmp_path, "Test", "0")
    ha = _ha(tmp_path)
    ha._bewaar()
    with pytest.raises(SystemExit):   # gekoppeld, maar nu met http gestart
        HA("http://127.0.0.1:1", tmp_path, "Test", "0")
    assert isinstance(ha.sessie, GeenOmleiding) and ha.sessie.max_redirects == 0
    gezien = {}

    def nep_request(self, method, url, **kw):
        gezien.update(kw)
        return _Antw(302, {}, {"Location": "https://elders.example/"})

    monkeypatch.setattr(requests.Session, "request", nep_request)
    r = ha.sessie.get("https://127.0.0.1:1/x", allow_redirects=True)
    assert gezien["allow_redirects"] is False and r.status_code == 302
    # een omleiding bij /lijst is een fout, geen geldige lijst
    with pytest.raises(requests.HTTPError):
        ha.lijst_halen()


def test_openen(tmp_path, caplog):
    """Punt 10: status controleren, weigering of fout als WARNING en beperkt melden; onbereikbaar = (False, 'onbereikbaar')."""
    ha = _ha(tmp_path)
    logs = []
    ha.log = lambda soort, **v: logs.append((soort, v))
    antwoorden = []

    class Sessie:
        def post(self, url, **kw):
            a = antwoorden.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

    ha.sessie = Sessie()
    antwoorden[:] = [_Antw(200, {"geopend": True})]
    assert ha.openen("p_1", 0.8) == (True, None)
    with caplog.at_level(logging.WARNING, logger="btx.ha"):
        antwoorden[:] = [_Antw(403, {"geopend": False, "reden": "buiten_uren"}), _Antw(403, {"reden": "buiten_uren"})]
        assert ha.openen("p_1", 0.8) == (False, "buiten_uren")
        assert ha.openen("p_1", 0.8) == (False, "buiten_uren")
        assert any(r.levelno == logging.WARNING and "buiten_uren" in r.getMessage() for r in caplog.records)
    assert len([x for x in logs if "opende niet" in x[1]["tekst"]]) == 1   # beperkt: een melding
    antwoorden[:] = [_Antw(500, None)]
    assert ha.openen("p_1", 0.8) == (False, "http 500")
    # 200 maar geopend false telt ook als geweigerd
    antwoorden[:] = [_Antw(200, {"geopend": False, "reden": "inactief"})]
    assert ha.openen("p_1", 0.8) == (False, "inactief")
    antwoorden[:] = [requests.ConnectionError("weg"), requests.Timeout("traag")]
    assert ha.openen("p_1", 0.8) == (False, "onbereikbaar")
    assert ha.openen("p_1", 0.8) == (False, "onbereikbaar")
    assert ha.stats["open_fout"] == 3 and ha.stats["open_geweigerd"] == 3
    assert len([x for x in logs if "onbereikbaar" in x[1]["tekst"]]) == 1


def test_ontsleutel_fouten(tmp_path):
    """Punt 12: onleesbare gezichten tellen en worden een keer per lijstversie gemeld."""
    ha = _ha(tmp_path)
    logs = []
    ha.log = lambda soort, **v: logs.append((soort, v))
    lijst = {"versie": 5, "personen": [{"id": "p_1", "naam": "Jan", "gezicht": {"pubkey_id": "ander"}},
                                       {"id": "p_2", "naam": "Lien"}]}

    class Sessie:
        def get(self, url, **kw):
            return _Antw(200, lijst if url.endswith("/lijst") else {"taken": []})

    ha.sessie = Sessie()
    ha.lijst_halen()
    ha.lijst_halen()
    assert ha.stats["ontsleutel_fouten"] == 2
    assert len(logs) == 1 and "Jan" in logs[0][1]["tekst"] and "versie 5" in logs[0][1]["tekst"]
    lijst["versie"] = 6
    ha.lijst_halen()
    assert ha.stats["ontsleutel_fouten"] == 3 and len(logs) == 2


class _NepHA:
    def __init__(self, geopend=True, fout=None):
        self.personen = {"p_jan": {"naam": "Jan"}}
        self.logs, self.geopend, self.fout = [], geopend, fout
    def log(self, soort, **v):
        self.logs.append((soort, v))
    def openen(self, pid, score):
        if self.fout:
            raise self.fout
        return self.geopend, None if self.geopend else "buiten_uren"


def test_verwerk_en_stats():
    """Punt 12: geopend enkel bij een gelukte live opening, proef_open apart."""
    stats = Beslisser(Instellingen()).stats
    besluit = {"actie": "open", "persoon_id": "p_jan", "cos": 0.8, "tweede": 0.3, "echt": 0.99, "diepte": 4.0, "oog": 90}
    hoofd.verwerk(_NepHA(), besluit, True, stats)
    assert stats["proef_open"] == 1 and stats["geopend"] == 0
    hoofd.verwerk(_NepHA(geopend=True), besluit, False, stats)
    hoofd.verwerk(_NepHA(geopend=False), besluit, False, stats)
    assert stats["geopend"] == 1
    # onbekend na te lang wachten: velden mogen leeg zijn
    ha = _NepHA()
    hoofd.verwerk(ha, {"actie": "onbekend", "persoon_id": None, "cos": None, "tweede": None, "echt": None, "diepte": None,
                       "oog": 80, "reden": "te_lang"}, True, stats)
    assert "onbekend" in ha.logs[0][1]["tekst"]


def test_foutlog_beperkt(caplog):
    """Punt 8: dezelfde stacktrace hoogstens een keer per 60 s."""
    fl = hoofd.Foutlog(60.0)

    def gooi(tekst):
        try:
            raise RuntimeError(tekst)
        except RuntimeError:
            return fl.fout("verwerking", nu=nu)

    with caplog.at_level(logging.ERROR, logger="btx"):
        nu = 0.0
        assert gooi("a") is True
        nu = 10.0
        assert gooi("a") is False          # zelfde stacktrace binnen 60 s
        assert gooi("b") is True           # andere fout
        nu = 61.0
        assert gooi("a") is True
    assert len([r for r in caplog.records if "verwerking" in r.getMessage()]) == 3 and fl.overgeslagen == 1
    # verwerk(): een sleutel voor alle fouten
    try:
        raise ValueError("x")
    except ValueError:
        assert fl.fout("verwerk", sleutel="verwerk", nu=0.0) is True
    try:
        raise KeyError("y")
    except KeyError:
        assert fl.fout("verwerk", sleutel="verwerk", nu=30.0) is False


def test_waakhond():
    """Punt 9: meer dan 10 s geen beeld: sessie dicht, opnieuw verbinden, camera_ok uit."""
    cam = hoofd.Camera("rtsp://x", threading.Event())
    b = Beslisser(Instellingen())
    personen, jan, lien, bijna = _personen()
    _loop(b, personen, bijna, 0.93, naderen=False, dicht=3)
    cam._wacht_vanaf = 0.0   # vaste testtijden, los van de klok
    cam.t, cam.ok, cam.laatst_wand, cam.n = 1000.0, True, 1.7e9, 5
    assert hoofd.waakhond(cam, b, 1005.0) is False and b.sessie is not None
    assert hoofd.waakhond(cam, b, 1010.5) is True
    assert b.sessie is None and cam._opnieuw.is_set()
    st = cam.status()
    assert st["camera_ok"] is False and st["camera_laatst"].startswith("2023") and st["camera_beelden"] == 5
    assert hoofd.waakhond(cam, b, 1011.0) is False      # niet bij elk beeld opnieuw
    assert hoofd.waakhond(cam, b, 1021.0) is True       # wel weer na 10 s zonder beeld
    assert cam.status()["camera_herstarts"] == 2


def test_camera_opties():
    """Punt 9: ffmpeg met tcp en timeout (geen stimeout meer)."""
    import inspect
    bron = inspect.getsource(hoofd.Camera.run)
    assert "rtsp_transport;tcp|timeout;10000000" in bron and "stimeout" not in bron
    assert hoofd.VERSIE == "0.3.0"
