import math

import pytest

from game.numfmt import fmt, fmt_duration


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0"),
        (0.1, "0.1"),
        (2.5, "2.5"),
        (7, "7"),
        (999, "999"),
        (999.99, "999"),
        (1000, "1.00K"),
        (1234, "1.23K"),
        (1239, "1.23K"),  # отбрасываем, а не округляем
        (12_345, "12.3K"),
        (999_999, "999K"),
        (1e6, "1.00M"),
        (2.5e9, "2.50B"),
        (1.5e12, "1.50T"),
        (1e15, "1.00Qa"),
        (1e33, "1.00Dc"),
        (1e36, "1.00aa"),
        (1e39, "1.00ab"),
        (-1500, "-1.50K"),
        (math.inf, "∞"),
        (math.nan, "?"),
    ],
)
def test_fmt(n, expected):
    assert fmt(n) == expected


def test_fmt_never_overstates():
    """Показанное число не больше реального — иначе игрок увидит «хватает», а купить не сможет."""
    for n in [1999.999, 9999.99, 99_999.9, 999_999.9, 1_999_999, 123_456_789]:
        s = fmt(n)
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        assert float(s[:-1]) * mult <= n


@pytest.mark.parametrize(
    "sec, expected",
    [(0, "0 с"), (45, "45 с"), (60, "1 мин"), (75, "1 мин 15 с"), (3600, "1 ч"), (7300, "2 ч 1 мин")],
)
def test_fmt_duration(sec, expected):
    assert fmt_duration(sec) == expected
