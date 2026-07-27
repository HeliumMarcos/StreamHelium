from sgd.ptn import parse_title


def test_parse_movie_filename():
    parsed = parse_title("Pirates of the Goolag 2016.mkv")
    assert parsed.title == "Pirates of the Goolag"
    assert parsed.year == 2016
    assert parsed.sortkeys["title"] == "Pirates of the Goolag"
    assert parsed.sortkeys["year"] == 2016


def test_parse_series_filename():
    parsed = parse_title("The.Show.Name.S01E02.1080p.WEB-DL.x264-GROUP.mkv")
    assert parsed.title == "The Show Name"
    assert parsed.season == 1
    assert parsed.episode == 2
    assert parsed.resolution == "1080p"
    assert parsed.sortkeys["se"] == 1
    assert parsed.sortkeys["ep"] == 2


def test_remux_overrides_quality():
    parsed = parse_title("Some.Movie.2020.1080p.BluRay.REMUX.mkv")
    assert parsed.quality == "REMUX"


def test_missing_fields_default_to_none():
    parsed = parse_title("randomfile.mkv")
    assert parsed.season is None
    assert parsed.episode is None
