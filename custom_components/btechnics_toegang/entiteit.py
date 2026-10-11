from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE


class ToegangEntiteit(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, reg, sleutel: str, naam: str, toestel: str) -> None:
        self.reg = reg
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sleutel)}, name=toestel,
                                            manufacturer="Btechnics", model="Btechnics Toegang")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()
