from types import SimpleNamespace

from sgd.gdrive import GoogleDrive


def test_qgen_joins_words_with_and_by_default():
    q = GoogleDrive.qgen("Pirates of the Goolag")
    # stop words ("of", "the") are dropped when there's more than one
    # meaningful word left.
    assert q == "name contains 'Pirates' and name contains 'Goolag'"


def test_qgen_keeps_stop_words_when_nothing_else_is_left():
    q = GoogleDrive.qgen("The")
    assert q == "name contains 'The'"


def test_qgen_custom_chain_and_method():
    q = GoogleDrive.qgen("S01E02", chain="or", method="fullText")
    assert q == "fullText contains 'S01E02'"


def test_qgen_splits_on_custom_splitter():
    q = GoogleDrive.qgen("s1, s2, s3", chain="or", splitter=", ", method="name")
    assert q == "name contains 's1' or name contains 's2' or name contains 's3'"


def test_qgen_drops_single_letter_words():
    # Single-letter, non-digit tokens are noise and get filtered out...
    q = GoogleDrive.qgen("a, b, c", chain="or", splitter=", ", method="name")
    assert q == ""


def test_qgen_keeps_single_letter_word_in_short_titles():
    # ...but not when the title itself is short: dropping "d" from "Dia D"
    # (Portuguese for "D-Day") would turn a distinctive title into a much
    # more common word ("dia") and match far too many unrelated files.
    q = GoogleDrive.qgen("Dia D")
    assert q == "name contains 'Dia' and name contains 'D'"


def test_qgen_strips_punctuation():
    q = GoogleDrive.qgen("Spider-Man: Homecoming")
    assert "Spider" in q and "Man" in q and "Homecoming" in q
    assert "-" not in q and ":" not in q


# --- get_id_query / search: the IMDb id is its own search query, not just a
# post-fetch filter --------------------------------------------------------

def test_get_id_query_movie_searches_name_by_id():
    sm = SimpleNamespace(id="tt15047880", stream_type="movie")
    gd = GoogleDrive.__new__(GoogleDrive)
    assert gd.get_id_query(sm) == "name contains 'tt15047880'"


def test_get_id_query_series_covers_common_id_notations():
    sm = SimpleNamespace(id="tt15047880", stream_type="series", se="1", ep="2")
    gd = GoogleDrive.__new__(GoogleDrive)
    q = gd.get_id_query(sm)
    assert "name contains 'tt15047880:1:2'" in q
    assert "name contains 'tt15047880 S01E02'" in q


def test_search_queries_by_id_even_when_titles_dont_match_the_file():
    # The id query must be its own independent Drive search - added to
    # self.query alongside the title-based queries - so a file is found
    # by id alone even if none of the metadata titles appear in its name
    # (e.g. a release named after something other than the official title).
    sm = SimpleNamespace(
        id="tt15047880",
        stream_type="movie",
        titles=["Something Completely Unrelated"],
    )
    gd = GoogleDrive.__new__(GoogleDrive)
    gd.page_size = 1000
    captured = {}

    def fake_file_list(fields):
        captured["query"] = list(gd.query)
        return []

    gd.file_list = fake_file_list
    gd.get_drive_names = lambda: {}

    gd.search(sm)

    assert "name contains 'tt15047880'" in captured["query"]
