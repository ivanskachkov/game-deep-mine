import pytest

from game.engine import Game


class FixedRng:
    """Предсказуемый ГСЧ: random() всегда возвращает value, uniform() — середину."""

    def __init__(self, value: float = 0.99):
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, lo: float, hi: float) -> float:
        return (lo + hi) / 2


@pytest.fixture
def game() -> Game:
    """Новая игра, в которой крит не выпадает никогда, а самородок даёт золото."""
    return Game(rng=FixedRng(0.99))


@pytest.fixture
def crit_game() -> Game:
    """Игра, в которой каждый тап — крит, а самородок даёт лихорадку."""
    return Game(rng=FixedRng(0.0))
