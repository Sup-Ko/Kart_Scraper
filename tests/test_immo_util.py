from geneva_immo.sources._util import parse_rooms, parse_surface


def test_parse_rooms_swiss_comma():
    assert parse_rooms("Appartement 3,5 pièces à Champel") == 3.5


def test_parse_rooms_dot_and_english():
    assert parse_rooms("4.5 rooms, 105 m²") == 4.5


def test_parse_rooms_half_fraction_and_german():
    assert parse_rooms("2½ pièces") == 2.5
    assert parse_rooms("Schöne 3.5 Zimmer Wohnung") == 3.5


def test_parse_rooms_singular():
    assert parse_rooms("1 pièce — 30 m²") == 1.0


def test_parse_rooms_absent_or_absurd():
    assert parse_rooms("Studio lumineux aux Pâquis") is None
    assert parse_rooms(None) is None
    assert parse_rooms("25 pièces") is None  # parsing noise, not a real flat


def test_parse_surface():
    assert parse_surface("82 m²") == 82.0
    assert parse_surface("105m2 habitables") == 105.0
    assert parse_surface("surface de 120.5 m²") == 120.5


def test_parse_surface_absent_or_absurd():
    assert parse_surface("3,5 pièces") is None
    assert parse_surface(None) is None
    # A "m2" figure below 10 is noise (e.g. balcony fragment), not living space.
    assert parse_surface("5 m²") is None
