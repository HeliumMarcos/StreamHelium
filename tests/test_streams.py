from types import SimpleNamespace

from sgd.streams import Streams


class FakeGDrive:
    """Minimal stand-in for GoogleDrive: no results, no real Drive/OAuth calls."""

    results = []

    def get_acc_token(self):
        return "fake-access-token"


def make_streams(**meta_kwargs):
    meta = SimpleNamespace(
        type="movie",
        stream_type="movie",
        titles=["Pirates of the Goolag"],
        year="2016",
        id="tt1234567",
        se=0,
        ep=0,
    )
    meta.__dict__.update(meta_kwargs)
    return Streams(FakeGDrive(), meta)


# --- is_semi_valid_title -----------------------------------------------

def test_title_matches_filename():
    s = make_streams()
    s.item = {"name": "Pirates.of.the.Goolag.2016.1080p.WEB-DL.mkv"}
    assert s.is_semi_valid_title({"sortkeys": {"title": "Pirates of the Goolag"}})


def test_title_does_not_match_unrelated_filename():
    s = make_streams()
    s.item = {"name": "Totally.Different.Movie.2016.mkv"}
    assert not s.is_semi_valid_title({"sortkeys": {"title": "Totally Different Movie"}})


def test_imdb_id_in_filename_always_matches():
    s = make_streams(id="tt9999999")
    s.item = {"name": "video_tt9999999_source.mkv"}
    assert s.is_semi_valid_title({"sortkeys": {}})


def test_rejects_when_parsed_title_has_unexplained_extra_words():
    # PTN picked up extra meaningful words in the title field that aren't
    # part of the searched title and aren't a known release-tag word -
    # this is the "leaked other content" guard.
    s = make_streams()
    s.item = {"name": "Pirates.of.the.Goolag.2016.BONUSCONTENT.mkv"}
    matched = s.is_semi_valid_title(
        {"sortkeys": {"title": "Pirates of the Goolag BONUSCONTENT"}}
    )
    assert not matched


def test_apostrophe_in_filename_matches_spaced_search_title():
    # A release can style a short title with an apostrophe instead of a
    # space ("Dia'D" for "Dia D"). clean_str() turns any non-alphanumeric
    # character (including "'") into a space on both sides of the
    # comparison, so this should still match. Use an id that isn't in the
    # filename so the id shortcut doesn't mask this path.
    s = make_streams(titles=["Dia D"], id="tt0000000")
    s.item = {"name": "Dia'D (2026) WEB-DL 2160p DV HDR10+ DDP5.1 H.265.mkv"}
    assert s.is_semi_valid_title({"sortkeys": {"title": "Dia'D"}})


def test_get_title_uses_metadata_name_when_filename_has_no_real_title():
    # File was matched by id alone (name has no real title text, just
    # quality tags): "WEB-DL 2160p DV HDR10+ DDP5.1 H.265 tt15047880.mkv".
    # PTN parses "DV" as the (bogus) leftover title. The displayed line3
    # title should come from the metadata name instead of that fragment.
    from sgd.ptn import parse_title

    s = make_streams(name="Dia D", titles=["dia d"], id="tt15047880")
    s.item = {"name": "WEB-DL 2160p DV HDR10+ DDP5.1 H.265 tt15047880.mkv"}
    s.parsed = parse_title(s.item["name"])

    title = s.get_title("2160p")
    line3 = title.splitlines()[0]

    assert line3 == "🎬 Dia D - (2016)"


def test_apostrophe_dropped_and_merged_into_word_still_matches():
    # sgd.meta.Meta stores titles after running them through sanitize(),
    # which already turns "'" into a space - so by the time a title reaches
    # is_semi_valid_title, "Margo's Got Money Troubles" has already become
    # "margo s got money troubles" (a real production example, confirmed
    # from logs). Some releases then drop the apostrophe entirely and fuse
    # the letters together instead: "Margos.Got.Money.Troubles...". Without
    # fusing that lone "s" back onto "margo", the title's strong words
    # ("margo", ...) never line up with the filename's single "margos"
    # token, and "margos" then gets flagged as an unexplained extra word.
    from sgd.ptn import parse_title

    s = make_streams(titles=["margo s got money troubles"], id="tt0000000")
    name = (
        "Margos.Got.Money.Troubles.S01E01.The.Hungry.Ghost.1080p.ATVP."
        "WEB-DL.DDP5.1.Atmos.H.264.DUAL-JHOM.mkv"
    )
    s.item = {"name": name}
    parsed = parse_title(name)
    assert s.is_semi_valid_title({"sortkeys": parsed.sortkeys})


def test_apostrophe_kept_in_filename_still_matches_sanitized_title():
    # Same show, but this release keeps the apostrophe in the filename
    # ("Margo's..."). Should still match the same (already-sanitized,
    # space-separated) search title.
    from sgd.ptn import parse_title

    s = make_streams(titles=["margo s got money troubles"], id="tt0000000")
    name = "Margo's.Got.Money.Troubles.S01E01.2160p.ATVP.WEB-DL.DD5.1.DV.HDR.H.265.mkv"
    s.item = {"name": name}
    parsed = parse_title(name)
    assert s.is_semi_valid_title({"sortkeys": parsed.sortkeys})


def test_no_titles_never_matches():
    s = make_streams(titles=[])
    s.item = {"name": "Pirates.of.the.Goolag.2016.mkv"}
    assert not s.is_semi_valid_title({"sortkeys": {}})


# --- is_valid_year -------------------------------------------------------

def test_valid_year_exact_match():
    s = make_streams(year="2016")
    assert s.is_valid_year({"sortkeys": {"year": "2016"}})


def test_valid_year_off_by_one_is_tolerated():
    s = make_streams(year="2016")
    assert s.is_valid_year({"sortkeys": {"year": "2017"}})


def test_valid_year_rejects_far_mismatch():
    s = make_streams(year="2016")
    assert not s.is_valid_year({"sortkeys": {"year": "1999"}})


def test_valid_year_missing_file_year_defaults_to_valid():
    s = make_streams(year="2016")
    assert s.is_valid_year({"sortkeys": {}})


# --- is_valid_episode ------------------------------------------------------

def test_valid_episode_matches_sortkeys():
    s = make_streams(stream_type="series", se=1, ep=2)
    s.item = {"name": "irrelevant.mkv"}
    assert s.is_valid_episode({"sortkeys": {"se": 1, "ep": 2}})


def test_valid_episode_rejects_other_season():
    s = make_streams(stream_type="series", se=1, ep=2)
    s.item = {"name": "irrelevant.mkv"}
    assert not s.is_valid_episode({"sortkeys": {"se": 2, "ep": 2}})


def test_valid_episode_falls_back_to_filename_regex():
    s = make_streams(stream_type="series", se=1, ep=2)
    s.item = {"name": "Show.Name.S01E02.mkv"}
    assert s.is_valid_episode({"sortkeys": {}})


def test_valid_episode_no_episode_info_anywhere_is_invalid():
    s = make_streams(stream_type="series", se=1, ep=2)
    s.item = {"name": "Show.Name.mkv"}
    assert not s.is_valid_episode({"sortkeys": {}})


# --- best_res --------------------------------------------------------------

def test_best_res_prefers_higher_resolution():
    s = make_streams()
    low = {"filename": "Movie.720p.WEB-DL.mkv", "sortkeys": {"res": "720p"}}
    high = {"filename": "Movie.2160p.WEB-DL.mkv", "sortkeys": {"res": "2160p"}}
    assert s.best_res(high) > s.best_res(low)


def test_best_res_prefers_remux_over_webdl_at_same_resolution():
    s = make_streams()
    webdl = {"filename": "Movie.1080p.WEB-DL.mkv", "sortkeys": {"res": "1080p"}}
    remux = {"filename": "Movie.1080p.BluRay.REMUX.mkv", "sortkeys": {"res": "1080p"}}
    assert s.best_res(remux) > s.best_res(webdl)


def test_best_res_never_raises_on_malformed_item():
    s = make_streams()
    assert s.best_res(None) == 1
