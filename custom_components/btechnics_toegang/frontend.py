"""De dashboardkaart automatisch beschikbaar maken, zoals bij Btechnics VTO."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
KAART_URL = "/btechnics_toegang_static"
KAART_BESTANDEN = ["btechnics-toegang-kaarten.js", "btechnics-toegang.js"]


async def async_registreer_kaarten(hass: HomeAssistant) -> None:
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.http import StaticPathConfig

    versie = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(KAART_URL, str(Path(__file__).parent / "frontend"), False)]
    )
    # Enkel als dashboardresource, niet via add_extra_js_url: dat script draait voor het kaartregister van
    # de frontend klaar is, en dan bestaat de kaart niet voor het dashboard (vastgesteld op HA 2026.10).

    async def _bij_start(_hass):
        await _resource(hass, versie)

    async_at_started(hass, _bij_start)


async def _resource(hass: HomeAssistant, versie: str) -> None:
    """Ook als dashboardresource, anders toont een oude bewaarde pagina 'Configuratiefout'."""
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None or not hasattr(resources, "async_create_item"):
            return
        if not getattr(resources, "loaded", False):
            await resources.async_load()
            resources.loaded = True
        for bestand in KAART_BESTANDEN:
            basis = f"{KAART_URL}/{bestand}"
            url = f"{basis}?v={versie}"
            mijn = [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == basis]
            if not mijn:
                await resources.async_create_item({"res_type": "module", "url": url})
            elif mijn[0].get("url") != url:
                await resources.async_update_item(mijn[0]["id"], {"res_type": "module", "url": url})
            for extra in mijn[1:]:
                await resources.async_delete_item(extra["id"])
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Dashboardresource registreren mislukt")
