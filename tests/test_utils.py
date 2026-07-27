from sgd.utils import hr_size, safe_get, num_extract, is_year, sanitize, strip_accents


def test_hr_size_bytes():
    assert hr_size(500) == "500.00B"


def test_hr_size_mib():
    assert hr_size(5 * 1024 * 1024) == "5.00MiB"


def test_hr_size_gib():
    assert hr_size(2 * 1024 ** 3) == "2.00GiB"


def test_safe_get_in_range():
    assert safe_get(["a", "b"], 1) == "b"


def test_safe_get_out_of_range_returns_default():
    assert safe_get(["a"], 5) == ""
    assert safe_get(["a"], 5, default="x") == "x"


def test_num_extract():
    assert num_extract("S01E02 (2020)") == ["01", "02", "2020"]


def test_is_year_valid():
    assert is_year("2020") is True
    assert is_year("1999") is True


def test_is_year_invalid():
    assert is_year("99") is False
    assert is_year("20200") is False
    assert is_year("abcd") is False
    assert is_year("1500") is False


def test_sanitize_strips_symbols_keeps_dots_and_spaces():
    assert sanitize("Pirates.of.the_Goolag!!") == "pirates.of.the goolag"


def test_strip_accents():
    assert strip_accents("ação") == "acao"
    assert strip_accents("Café") == "Cafe"
