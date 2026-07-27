"""Regression tests for matching only the title portion of a filename.

is_semi_valid_title() used to compare a search title against the whole
cleaned filename, including anything after the year/SxxEyy marker (episode
title, quality tags, release group). That let a short/generic search title
spuriously match a word that only appears in that trailing text rather than
in the actual title. It now matches against the title portion PTN parses
out of the filename (see sgd/streams.py:is_semi_valid_title).
"""

from types import SimpleNamespace

from sgd.ptn import parse_title
from sgd.streams import Streams


class FakeGDrive:
    results = []

    def get_acc_token(self):
        return "fake-access-token"


def is_match(file_name, titles, stream_type="movie"):
    meta = SimpleNamespace(
        type=stream_type, stream_type=stream_type, titles=titles,
        year=None, id="ttNOPE", se=0, ep=0,
    )
    s = Streams(FakeGDrive(), meta)
    s.item = {"name": file_name}
    parsed = parse_title(file_name)
    return s.is_semi_valid_title({"sortkeys": parsed.sortkeys})


# Real-world filename samples, each pinned to the show/movie they belong to.
SAMPLES = [
    ("Ardente Vingança (2026) 2160p AMZN 10bit WEB-DL HDR10+ DDP5.1 H.265 tt34379307.mkv", "movie", "ardente vinganca"),
    ("O Aniversário (2025) 2160p iT 10bit WEB-DL DV HDR10+ DDP5.1 H.265 tt12583926.mkv", "movie", "o aniversario"),
    ("Tom.Clancy's.Jack.Ryan.Ghost.War.2026.2160p.AMZN.WEB-DL.DDP5.1.HDR.H.265.mkv", "movie", "Tom Clancy's Jack Ryan Ghost War"),
    ("A Casa do Dragão S03E05 2160p HMAX WEB-DL DDP5.1 DV HDR10 H.265 tt11198330.mkv", "series", "a casa do dragao"),
    ("House.of.the.Dragon.S01E10.A.Rainha.Preta.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-JHOM.mkv", "series", "house of the dragon"),
    ("Backrooms.Um.Nao-Lugar.2026.1080p.iT.WEB-DL.DD5.1.H.264.DUAL-C76.mkv", "movie", "backrooms um nao lugar"),
    ("Cape.Fear.S01E08.Los.tiempos.de.Dios.son.Perfectos.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265.DUAL-DonnyFiles.mkv", "series", "cape fear"),
    ("Silo.S03E03.A.Dark.Web.1080p.ATVP.WEB-DL.DDP5.1.Atmos.H.264.DUAL-JHOM.mkv", "series", "silo"),
    ("Daemons.do.Reino.das.Sombras.S01E11.1080p.CR.WEB-DL.AAC2.0.H.264.tt37532356.MKV", "series", "daemons do reino das sombras"),
    ("Rooster.Fighter.S01E06.Built.Like.a.Brick.Coop.1080P.10bit.DSNP.WEB-DL.AAC2.0.AV1.mkv", "series", "rooster fighter"),
]


def test_all_samples_match_their_own_title():
    for file_name, stream_type, title in SAMPLES:
        assert is_match(file_name, [title], stream_type), f"{title!r} should match {file_name!r}"


def test_episode_title_words_do_not_cause_cross_show_matches():
    # "Dark" only appears in Silo's episode title ("A Dark Web"), not in
    # Silo's own title - a search for a show called "Dark" must not match.
    assert not is_match(
        "Silo.S03E03.A.Dark.Web.1080p.ATVP.WEB-DL.DDP5.1.Atmos.H.264.DUAL-JHOM.mkv",
        ["dark"], "series",
    )


def test_episode_title_words_do_not_match_other_searches():
    cases = [
        ("House.of.the.Dragon.S01E10.A.Rainha.Preta.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-JHOM.mkv", "rainha", "series"),
        ("Cape.Fear.S01E08.Los.tiempos.de.Dios.son.Perfectos.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265.DUAL-DonnyFiles.mkv", "perfectos", "series"),
        ("Rooster.Fighter.S01E06.Built.Like.a.Brick.Coop.1080P.10bit.DSNP.WEB-DL.AAC2.0.AV1.mkv", "brick", "series"),
    ]
    for file_name, title, stream_type in cases:
        assert not is_match(file_name, [title], stream_type), f"{title!r} should NOT match {file_name!r}"


def test_quality_tags_do_not_cause_matches():
    cases = [
        ("Ardente Vingança (2026) 2160p AMZN 10bit WEB-DL HDR10+ DDP5.1 H.265 tt34379307.mkv", "amzn"),
        ("Daemons.do.Reino.das.Sombras.S01E11.1080p.CR.WEB-DL.AAC2.0.H.264.tt37532356.MKV", "web dl"),
    ]
    for file_name, title in cases:
        assert not is_match(file_name, [title], "movie"), f"{title!r} should NOT match {file_name!r}"


def test_short_title_with_single_letter_word_matches_its_own_file():
    # "Dia D" (Portuguese for "D-Day") must still match its own release,
    # with or without the single-letter "D" joined by a different
    # separator (dot/dash/underscore all normalize to a space).
    for file_name in [
        "Dia.D.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264.tt15047880.mkv",
        "Dia-D.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264.mkv",
        "Dia_D.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264.mkv",
        "Dia D (2026) 1080p AMZN WEB-DL DDP5.1 H.264.mkv",
    ]:
        assert is_match(file_name, ["dia d"], "movie"), f"'dia d' should match {file_name!r}"


def test_short_title_single_letter_word_is_not_dropped_as_noise():
    # Regression: "dia d" used to be reduced to just "dia" (single-letter
    # words were stripped as noise), so any unrelated file containing the
    # common word "dia" anywhere would incorrectly match.
    assert not is_match(
        "Um.Dia.De.Sorte.Em.Nova.York.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264.DUAL-JHOM.mkv",
        ["dia d"], "movie",
    )
