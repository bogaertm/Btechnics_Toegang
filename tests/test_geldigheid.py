from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.btechnics_toegang.geldigheid import controleer_blok, reden_geweigerd

TZ = ZoneInfo("Europe/Brussels")


def t(s):  # 2026-10-12 is een maandag
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def test_altijd():
    assert reden_geweigerd({"schema": []}, t("2026-10-12T03:00")) is None


def test_datums():
    p = {"van": "2026-10-12", "tot": "2026-10-13", "schema": []}
    assert reden_geweigerd(p, t("2026-10-11T23:59")) == "buiten_schema"
    assert reden_geweigerd(p, t("2026-10-13T23:59")) is None
    assert reden_geweigerd(p, t("2026-10-14T00:00")) == "verlopen"


def test_werkuren():
    p = {"schema": [{"dagen": [1, 2, 3, 4, 5], "van": "08:00", "tot": "18:00"}]}
    assert reden_geweigerd(p, t("2026-10-12T08:00")) is None
    assert reden_geweigerd(p, t("2026-10-12T18:00")) == "buiten_schema"
    assert reden_geweigerd(p, t("2026-10-17T10:00")) == "buiten_schema"   # zaterdag


def test_over_middernacht_in_een_blok():
    p = {"schema": [{"dagen": [5], "van": "22:00", "tot": "06:00"}]}   # vrijdagnacht
    assert reden_geweigerd(p, t("2026-10-16T23:00")) is None            # vrijdag 23 u
    assert reden_geweigerd(p, t("2026-10-17T05:59")) is None            # zaterdag 5:59
    assert reden_geweigerd(p, t("2026-10-17T06:00")) == "buiten_schema"
    assert reden_geweigerd(p, t("2026-10-16T05:00")) == "buiten_schema" # vrijdag vroeg: blok van donderdag bestaat niet


def test_van_gelijk_aan_tot_is_24_uur():
    p = {"schema": [{"dagen": [1], "van": "07:00", "tot": "07:00"}]}
    assert reden_geweigerd(p, t("2026-10-13T06:59")) is None            # dinsdag 6:59
    assert reden_geweigerd(p, t("2026-10-13T07:00")) == "buiten_schema"


def test_blok_controle():
    assert controleer_blok({"dagen": [3, 1, 1], "van": "8:00", "tot": "17:30"}) == {
        "dagen": [1, 3], "van": "08:00", "tot": "17:30"}
    with pytest.raises(ValueError):
        controleer_blok({"dagen": [8], "van": "08:00", "tot": "09:00"})
    with pytest.raises(ValueError):
        controleer_blok({"dagen": [1], "van": "25:00", "tot": "09:00"})
