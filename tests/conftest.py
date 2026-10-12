import glob
import os

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def schoon_archief(hass):
    """Elke test met een leeg archief, foutenlog en zonder foto's van een vorige test."""
    for pad in glob.glob(hass.config.path("btechnics_toegang_*.db*")):
        os.remove(pad)
    for pad in glob.glob(hass.config.path("btechnics_toegang", "fotos", "*")):
        os.remove(pad)
    yield
