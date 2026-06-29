from kart_scraper.sources._util import clean_text, parse_price


def test_parse_price_european():
    assert parse_price("1 299,00 €") == 1299.0
    assert parse_price("2\xa0800 €") == 2800.0
    assert parse_price("1.250 €") == 1250.0  # thousands dot, no decimals


def test_parse_price_us():
    assert parse_price("$1,299.00") == 1299.0
    assert parse_price("3,500") == 3500.0


def test_parse_price_none():
    assert parse_price("Nous contacter") is None
    assert parse_price("") is None
    assert parse_price(None) is None


def test_clean_text():
    assert clean_text("  hello\n  world ") == "hello world"
    assert clean_text(None) == ""
