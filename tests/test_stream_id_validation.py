import pytest

from sgd.utils import split_stream_id
from sgd.routes import is_valid_stream_id


@pytest.mark.parametrize("stream_id", ["tt1234567:1:2", "tt1234567%3A1%3A2"])
def test_split_stream_id_handles_both_colon_encodings(stream_id):
    assert split_stream_id(stream_id) == ["tt1234567", "1", "2"]


def test_split_stream_id_no_separator():
    assert split_stream_id("tt1234567") == ["tt1234567"]


@pytest.mark.parametrize(
    "stream_id",
    [
        "tt1234567",
        "tt1234567:1:2",
        "tt1234567%3A1%3A2",
        "tmdb:12345",
        "tmdb%3A12345",
        "tmdb:12345:1:2",
        "TT1234567",
    ],
)
def test_valid_stream_ids(stream_id):
    assert is_valid_stream_id(stream_id)


@pytest.mark.parametrize(
    "stream_id",
    [
        "garbage",
        "tt12",
        "tmdb",
        "tmdb:notanumber",
        "tt1234567:abc:2",
        "tt1234567:1:2:3",
    ],
)
def test_invalid_stream_ids(stream_id):
    assert not is_valid_stream_id(stream_id)
