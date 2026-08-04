import csv

from rich.console import Console

from geneva_immo.config import DEFAULT_WEIGHTS
from geneva_immo.report import print_table, write_csv, write_html
from geneva_immo.scoring import score_listings
from geneva_immo.sources.sample import SampleSource


def _ranked():
    return score_listings(SampleSource().fetch(None), DEFAULT_WEIGHTS)


def test_write_csv(tmp_path):
    path = tmp_path / "apartments.csv"
    ranked = _ranked()
    write_csv(ranked, path)
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(ranked)
    assert rows[0]["rank"] == "1"
    assert float(rows[0]["score"]) >= float(rows[-1]["score"])
    assert rows[0]["currency"] == "CHF"
    assert rows[0]["price_per_m2"]


def test_write_html(tmp_path):
    path = tmp_path / "apartments.html"
    ranked = _ranked()
    write_html(ranked, path, "Genève, Suisse")
    html = path.read_text(encoding="utf-8")
    assert "Genève, Suisse" in html
    assert ranked[0].title in html
    assert "CHF/m²" in html


def test_print_table_smoke(capsys):
    print_table(_ranked(), Console())
    out = capsys.readouterr().out
    assert "ranked by score" in out


def test_print_table_empty(capsys):
    print_table([], Console())
    assert "No listings found" in capsys.readouterr().out
