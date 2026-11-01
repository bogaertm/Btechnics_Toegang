"""v0.15.3: alarmen van de deurtoegang. Altijd een melding in het HA scherm (persistent_notification) en, als ingesteld
(optie gsm_melding, bv. "mobile_app_boogie_phone"), ook een gsm-melding via notify. Een fout bij de gsm-melding mag
het alarm in HA nooit tegenhouden."""
from __future__ import annotations

import logging
import re

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def gsm_doel(hass: HomeAssistant) -> str:
    entry = (hass.data.get(DOMAIN) or {}).get("entry")
    doel = str((entry.options.get("gsm_melding") if entry else "") or "").strip()
    return doel.removeprefix("notify.")


def alarm(hass: HomeAssistant, tekst: str, title: str, notification_id: str, gsm: bool = True) -> None:
    """Melding in HA en (indien ingesteld) op de gsm. Mag vanuit de event loop aangeroepen worden."""
    persistent_notification.async_create(hass, tekst, title=title, notification_id=notification_id)
    doel = gsm_doel(hass) if gsm else ""
    if not doel:
        return
    if not hass.services.has_service("notify", doel):
        _LOGGER.warning("Gsm-melding naar notify.%s kan niet: die dienst bestaat niet", doel)
        return
    kort = re.sub(r"\*\*(.+?)\*\*", r"\1", tekst)[:500]

    async def _stuur() -> None:
        try:
            await hass.services.async_call("notify", doel, {"title": title.replace("Btechnics Toegang: ", "Deur: "), "message": kort,
                                                             "data": {"tag": notification_id, "group": "btechnics_toegang"}},
                                           blocking=True)
        except Exception as e:  # noqa: BLE001  een gsm-melding mag nooit iets breken
            _LOGGER.warning("Gsm-melding naar notify.%s mislukt: %s", doel, e)

    hass.async_create_task(_stuur())
