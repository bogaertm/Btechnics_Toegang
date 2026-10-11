from custom_components.btechnics_toegang.hashing import hash_waarde, kaart_norm, pin_norm, zout


def test_testvector_met_app():
    # Afgesproken testvector, nagerekend in de app (Java) en hier (Python)
    assert hash_waarde("test", "1234") == "78ddd1e09efdd4c9c9b044ffee6ea076b05e95441fc2ead652e0c46809e31824"


def test_zout_24_hex():
    z = zout()
    assert len(z) == 24 and int(z, 16) >= 0


def test_normen():
    assert pin_norm(" 12-34 ") == "1234"
    assert kaart_norm("0a:1b-2c 3d") == "0A1B2C3D"
