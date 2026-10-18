"""Verbinding met Home Assistant (Btechnics Toegang), zelfde protocol als een deurtablet met soort "server".

Eerste start: eigen RSA sleutel maken, koppelen met de koppelcode, de vingerafdruk van het https certificaat van HA vastzetten.
Daarna: /lijst (ETag), /wacht (lange vraag), /status elke minuut, /open bij een herkenning, /log en /foto.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import urllib3
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from requests.adapters import HTTPAdapter

from .herkenning import VERSIE

LOG = logging.getLogger("btx.ha")
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
API = "/api/btechnics_toegang"


def nu_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def certificaat_sha256(url: str) -> str:
    """sha256 van het certificaat dat HA nu toont (enkel bij de eerste koppeling, daarna vast)."""
    from urllib.parse import urlparse
    u = urlparse(url)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((u.hostname, u.port or 443), timeout=10) as s, ctx.wrap_socket(s) as t:
        return hashlib.sha256(t.getpeercert(binary_form=True)).hexdigest()


class Vingerafdruk(HTTPAdapter):
    """https zonder geldig certificaat, maar enkel met het vastgezette certificaat van HA."""

    def __init__(self, sha256: str, **kw):
        self.sha256 = sha256
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kw["ssl_context"] = ctx
        kw["assert_fingerprint"] = self.sha256
        return super().init_poolmanager(*a, **kw)

    def cert_verify(self, conn, url, verify, cert):
        super().cert_verify(conn, url, False, cert)


class HA:
    def __init__(self, url: str, data: Path, naam: str, app_versie: str):
        self.url = url.rstrip("/")
        self.data = data
        self.naam = naam
        self.app_versie = app_versie
        data.mkdir(parents=True, exist_ok=True)
        self.staat_pad = data / "staat.json"
        self.staat = json.loads(self.staat_pad.read_text()) if self.staat_pad.exists() else {}
        self.sleutel = self._privesleutel()
        der = self.sleutel.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.pubkey = base64.b64encode(der).decode()
        self.pubkey_id = hashlib.sha256(der).hexdigest()
        self.sessie = requests.Session()
        self.sessie.verify = False   # zelf ondertekend certificaat; Vingerafdruk controleert het vastgezette certificaat
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        if self.staat.get("https_sha256"):
            self.sessie.mount(self.url, Vingerafdruk(self.staat["https_sha256"]))
        self.versie = 0
        self.etag = None
        self.lijst: dict | None = None
        self.personen: dict[str, dict] = {}   # persoon_id -> {naam, afdrukken (n,192), schema...}
        self.laatste_lijst = None
        self.wachtrij: list[dict] = []
        self._slot = threading.Lock()
        self.bij_lijst = None
        self.bij_fotovraag = None

    # ------------------------------------------------------------------ sleutel en koppelen

    def _privesleutel(self):
        pad = self.data / "sleutel.pem"
        if pad.exists():
            return serialization.load_pem_private_key(pad.read_bytes(), password=None)
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pad.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(pad, 0o600)
        return k

    def _bewaar(self):
        tmp = self.staat_pad.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.staat, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.staat_pad)

    @property
    def gekoppeld(self) -> bool:
        return bool(self.staat.get("sleutel"))

    def koppelen(self, code: str, toestel_id: str) -> None:
        if not self.url.startswith("https://"):
            raise RuntimeError("HA_URL moet https zijn (bv. https://192.168.1.117:8443)")
        vinger = certificaat_sha256(self.url)
        self.sessie.mount(self.url, Vingerafdruk(vinger))
        r = self.sessie.post(self.url + API + "/koppel", timeout=15, json={
            "code": code, "toestel_id": toestel_id, "naam": self.naam, "app_versie": self.app_versie,
            "pubkey": self.pubkey, "soort": "server"})
        if r.status_code != 200:
            raise RuntimeError(f"koppelen mislukt ({r.status_code}): {r.text[:200]}")
        a = r.json()
        https = a.get("https") or {}
        if https.get("sha256") and https["sha256"] != vinger:
            raise RuntimeError("het certificaat van HA verschilt van wat HA zelf opgeeft: niet gekoppeld")
        self.staat.update({"sleutel": a["sleutel"], "tablet_id": a["tablet_id"], "deur_id": a["deur_id"],
                           "https_sha256": vinger, "gekoppeld": nu_iso()})
        self._bewaar()
        LOG.info("gekoppeld als %s aan deur %s", a["tablet_id"], a["deur_id"])

    def _kop(self) -> dict:
        return {"Authorization": "Bearer " + self.staat["sleutel"]}

    # ------------------------------------------------------------------ lijst

    def lijst_halen(self) -> bool:
        kop = self._kop()
        if self.etag:
            kop["If-None-Match"] = self.etag
        r = self.sessie.get(self.url + API + "/lijst", headers=kop, timeout=20)
        if r.status_code == 304:
            return False
        r.raise_for_status()
        self.etag = r.headers.get("ETag")
        lijst = r.json()
        personen = {}
        for p in lijst.get("personen", []):
            gz = p.get("gezicht")
            if not gz:
                continue
            try:
                personen[p["id"]] = {"naam": p["naam"], "afdrukken": self.ontsleutel(gz), "schema": p.get("schema") or []}
            except Exception as e:  # noqa: BLE001
                LOG.warning("gezicht van %s niet leesbaar: %s", p.get("naam"), e)
        with self._slot:
            self.lijst, self.personen, self.versie = lijst, personen, lijst.get("versie", 0)
            self.laatste_lijst = nu_iso()
        LOG.info("lijst versie %s: %d personen, %d met gezicht", self.versie, len(lijst.get("personen", [])), len(personen))
        if self.bij_lijst:
            self.bij_lijst()
        try:
            self.herverpakken()
        except Exception as e:  # noqa: BLE001
            LOG.warning("doorzetten: %s", e)
        return True

    def ontsleutel(self, gz: dict) -> np.ndarray:
        if gz.get("pubkey_id") != self.pubkey_id:
            raise ValueError("pakket hoort bij een andere sleutel")
        aes = self.sleutel.decrypt(base64.b64decode(gz["sleutel"]), OAEP)
        klaar = json.loads(AESGCM(aes).decrypt(base64.b64decode(gz["iv"]), base64.b64decode(gz["data"]), gz["aad"].encode()))
        if klaar.get("versie") != VERSIE:
            raise ValueError(f"versie {klaar.get('versie')} past niet bij {VERSIE}")
        a = np.stack([np.frombuffer(base64.b64decode(x), "<f4") for x in klaar["afdrukken"]]).astype(np.float32)
        return a / np.linalg.norm(a, axis=1, keepdims=True)

    def herverpakken(self) -> int:
        """Afdrukken doorzetten naar nieuwe toestellen of deuren: AES sleutel uitpakken met de eigen sleutel en
        verpakken voor de publieke sleutel van elk doel. De afdrukken zelf blijven onaangeroerd in HA."""
        r = self.sessie.get(self.url + API + "/herverpak", headers=self._kop(), timeout=20)
        r.raise_for_status()
        n = 0
        for tk in r.json().get("taken") or []:
            try:
                aes = self.sleutel.decrypt(base64.b64decode(tk["sleutel"]), OAEP)
                sleutels = {}
                for d in tk["doel"]:
                    der = base64.b64decode(d["pubkey"])
                    if hashlib.sha256(der).hexdigest() != d["pubkey_id"]:
                        continue
                    pub = serialization.load_der_public_key(der)
                    sleutels[d["pubkey_id"]] = base64.b64encode(pub.encrypt(aes, OAEP)).decode()
                if sleutels:
                    a = self.sessie.post(self.url + API + "/herverpak", headers=self._kop(), timeout=20,
                                         json={"persoon_id": tk["persoon_id"], "sleutels": sleutels})
                    a.raise_for_status()
                    n += a.json().get("nieuw", 0)
            except Exception as e:  # noqa: BLE001
                LOG.warning("doorzetten voor %s: %s", tk.get("persoon_id"), e)
        if n:
            LOG.info("%d afdruk(ken) doorgezet naar nieuwe toestellen", n)
        return n

    # ------------------------------------------------------------------ lussen

    def wacht_lus(self, stop: threading.Event) -> None:
        """Lange vraag: HA antwoordt bij een nieuwe versie of een fotovraag."""
        fouten = 0
        while not stop.is_set():
            try:
                if self.lijst is None:
                    self.lijst_halen()
                r = self.sessie.get(self.url + API + "/wacht", params={"versie": self.versie}, headers=self._kop(), timeout=70)
                if r.status_code == 200:
                    a = r.json()
                    for uid in a.get("foto") or []:
                        if self.bij_fotovraag:
                            threading.Thread(target=self.bij_fotovraag, args=(uid,), daemon=True).start()
                    if a.get("versie") != self.versie:
                        self.lijst_halen()
                elif r.status_code == 401:
                    LOG.error("HA kent deze server niet meer (ontkoppeld?). Wis /data/staat.json en koppel opnieuw.")
                    stop.wait(300)
                fouten = 0
            except Exception as e:  # noqa: BLE001
                fouten += 1
                LOG.warning("wacht: %s", e)
                stop.wait(min(60, 2 ** min(fouten, 6)))

    def status_lus(self, stop: threading.Event, extra) -> None:
        while not stop.is_set():
            try:
                self.log_versturen()
                with self._slot:
                    n_pers = len((self.lijst or {}).get("personen", []))
                    n_gez = len(self.personen)
                body = {"app_versie": self.app_versie, "schema": 1, "versie": self.versie, "personen": n_pers, "gezichten": n_gez,
                        "laatste_lijst": self.laatste_lijst, "log_wachtrij": len(self.wachtrij), "pubkey": self.pubkey,
                        "echtheid": extra()}
                r = self.sessie.post(self.url + API + "/status", headers=self._kop(), json=body, timeout=15)
                if r.ok and r.json().get("pubkey_fout"):
                    LOG.error("HA weigert de sleutel: %s", r.json()["pubkey_fout"])
                self.herverpakken()   # vangnet: ook als er geen nieuwe lijstversie kwam
            except Exception as e:  # noqa: BLE001
                LOG.warning("status: %s", e)
            stop.wait(60)

    # ------------------------------------------------------------------ acties

    def openen(self, persoon_id: str, score: float) -> tuple[bool, str | None]:
        r = self.sessie.post(self.url + API + "/open", headers=self._kop(), timeout=15,
                             json={"methode": "gezicht", "persoon_id": persoon_id, "log": False, "score": round(score, 3)})
        try:
            a = r.json()
        except ValueError:
            a = {}
        return bool(a.get("geopend")), a.get("reden") or a.get("tekst")

    def log(self, soort: str, **velden) -> None:
        with self._slot:
            self.wachtrij.append({"uid": f"gs-{uuid.uuid4()}", "tijd": nu_iso(), "soort": soort, **velden})
            del self.wachtrij[:-200]
        threading.Thread(target=self.log_versturen, daemon=True).start()

    def log_versturen(self) -> None:
        with self._slot:
            items = list(self.wachtrij[:50])
        if not items:
            return
        try:
            r = self.sessie.post(self.url + API + "/log", headers=self._kop(), json={"items": items}, timeout=15)
            r.raise_for_status()
            ok = set(r.json().get("ontvangen") or [])
            with self._slot:
                self.wachtrij = [i for i in self.wachtrij if i["uid"] not in ok]
        except Exception as e:  # noqa: BLE001
            LOG.warning("log: %s", e)

    def foto(self, uid: str, jpeg: bytes) -> None:
        try:
            self.sessie.post(self.url + API + "/foto", headers=self._kop(), timeout=15,
                             json={"uid": uid, "foto": base64.b64encode(jpeg).decode()})
        except Exception as e:  # noqa: BLE001
            LOG.warning("foto: %s", e)


def wacht_tot(stop: threading.Event, functie, omschrijving: str) -> None:
    fouten = 0
    while not stop.is_set():
        try:
            functie()
            return
        except Exception as e:  # noqa: BLE001
            fouten += 1
            LOG.warning("%s: %s", omschrijving, e)
            stop.wait(min(60, 5 * fouten))
    time.sleep(0)
