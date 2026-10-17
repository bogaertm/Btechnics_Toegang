"""Eigen https poort voor de tablets (standaard 8443), volledig lokaal, met een zelf gemaakt certificaat.

Enkel de tabletroutes (/api/btechnics_toegang/koppel, wacht, lijst, log, modus, status, open) draaien hier. De rest van HA
blijft op 8123. Het certificaat is niet "geldig" voor een browser; de tablet zet de vingerafdruk (sha256 van het certificaat
in DER) vast bij het koppelen. HA toont dezelfde vingerafdruk bij de koppelcode, zodat je ze kan vergelijken.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import logging
import os
import ssl
from pathlib import Path

from aiohttp import web

from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS

_LOGGER = logging.getLogger(__name__)
POORT_STANDAARD = 8443


def _maak_certificaat(map_: Path, ips: list[str]) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    sleutel = ec.generate_private_key(ec.SECP256R1())
    naam = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Btechnics Toegang"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Btechnics")])
    alt = [x509.DNSName("btechnics-toegang.local")]
    for ip in ips:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    nu = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(naam).issuer_name(naam).public_key(sleutel.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nu - dt.timedelta(days=1))
            .not_valid_after(nu + dt.timedelta(days=365 * 20))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(sleutel, hashes.SHA256()))
    map_.mkdir(parents=True, exist_ok=True)
    sleutelpad = map_ / "key.pem"
    sleutelpad.write_bytes(sleutel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
    os.chmod(sleutelpad, 0o600)
    (map_ / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _vingerafdruk(certpad: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_pem_x509_certificate(certpad.read_bytes())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def kort(vinger: str | None) -> str:
    return " ".join(vinger[i:i + 4].upper() for i in range(0, 16, 4)) if vinger else ""


class HttpsPoort:
    def __init__(self, hass: HomeAssistant, poort: int = POORT_STANDAARD) -> None:
        self.hass, self.poort = hass, poort
        self.map = Path(hass.config.path("btechnics_toegang", "tls"))
        self.vingerafdruk: str | None = None
        self.fout: str | None = None
        self._runner: web.AppRunner | None = None

    def _voorbereid(self, ips: list[str]) -> ssl.SSLContext:
        if not (self.map / "cert.pem").exists() or not (self.map / "key.pem").exists():
            _maak_certificaat(self.map, ips)
        self.vingerafdruk = _vingerafdruk(self.map / "cert.pem")
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.map / "cert.pem", self.map / "key.pem")
        return ctx

    async def async_start(self, views: list) -> None:
        try:
            from homeassistant.components.network import async_get_source_ip
            ips = [await async_get_source_ip(self.hass)]
        except Exception:  # noqa: BLE001
            ips = []
        try:
            ctx = await self.hass.async_add_executor_job(self._voorbereid, ips)
            app = web.Application(client_max_size=1024 * 1024)
            app[KEY_HASS] = self.hass
            for v in views:
                v.register(self.hass, app, app.router)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            await web.TCPSite(self._runner, None, self.poort, ssl_context=ctx).start()
            self.fout = None
            _LOGGER.info("Https voor de tablets op poort %s (vingerafdruk %s)", self.poort, kort(self.vingerafdruk))
        except Exception as e:  # noqa: BLE001
            self.fout = str(e)
            _LOGGER.error("Https poort %s kon niet starten: %s", self.poort, e)
            if self._runner:
                await self._runner.cleanup()
                self._runner = None

    async def async_stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
