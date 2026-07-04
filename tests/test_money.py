from kart_scraper.money import CURRENCY_TO_EUR, to_eur


def test_to_eur_known_currencies():
    assert to_eur(100, "EUR") == 100
    assert to_eur(100, "USD") == 100 * CURRENCY_TO_EUR["USD"]
    assert to_eur(100, "chf") == 100 * CURRENCY_TO_EUR["CHF"]  # case-insensitive


def test_to_eur_unknown_currency_defaults_to_identity():
    assert to_eur(100, "JPY") == 100


def test_to_eur_none():
    assert to_eur(None, "EUR") is None
