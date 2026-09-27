"""Бот играет «идеально» (4 тапа/с, покупает самое выгодное) и проверяет темп игры.

Если после правки баланса этот тест упал — игра стала слишком быстрой или слишком медленной.
"""

import random

import pytest

from game import content as C
from game import engine as E
from game.engine import Game

TAPS_PER_SEC = 4


def _value(g: Game) -> float:
    tap = g.tap_damage() * (1 + g.crit_chance() * (E.CRIT_MULT - 1))
    return (g.dps() + TAPS_PER_SEC * tap) * E.biome_gold_mult(g.depth) * g.effects().gold_mult


def _best_purchase(g: Game):
    base = _value(g)
    options = []
    for d in C.DIGGERS:
        n = g.diggers.get(d.id, 0)
        to_milestone = E.next_milestone(n)[0] - n
        # как живой игрок: либо один копатель, либо докупить до следующего порога
        for k in {1, to_milestone}:
            g.diggers[d.id] = n + k
            gain = _value(g) - base
            g.diggers[d.id] = n
            cost = g.digger_cost(d.id, k)
            options.append((gain / cost, cost, lambda d=d, k=k: g.buy_digger(d.id, k)))
    g.pickaxe_level += 1
    gain = _value(g) - base
    g.pickaxe_level -= 1
    options.append((gain / g.pickaxe_cost(), g.pickaxe_cost(), g.buy_pickaxe))
    for u in C.UPGRADES:
        if u.id in g.upgrades or not g.upgrade_unlocked(u.id):
            continue
        g.upgrades.add(u.id)
        gain = _value(g) - base
        g.upgrades.discard(u.id)
        options.append((gain / u.cost, u.cost, lambda u=u: g.buy_upgrade(u.id)))
    return max(options, key=lambda o: o[0])


def play(minutes: float, relics: int = 0, seed: int = 1) -> tuple[Game, dict[int, float]]:
    """Возвращает игру и словарь «глубина → на какой минуте достигнута»."""
    g = Game(rng=random.Random(seed), relics=relics)
    reached: dict[int, float] = {}
    for sec in range(int(minutes * 60)):
        for _ in range(TAPS_PER_SEC):
            g.tap()
        g.tick(1.0)
        if g.nugget_active:
            g.claim_nugget()
        while True:
            _, cost, buy = _best_purchase(g)
            if cost > g.gold:
                break
            buy()
        for mark in (10, 25, 50, 100, 150):
            if g.depth >= mark and mark not in reached:
                reached[mark] = sec / 60
    return g, reached


@pytest.fixture(scope="module")
def first_run():
    return play(minutes=30)


def test_early_game_is_snappy(first_run):
    _, reached = first_run
    assert reached[10] < 1
    assert reached[50] < 5


def test_first_prestige_takes_a_while_but_is_reachable(first_run):
    g, reached = first_run
    assert 8 <= reached[100] <= 30, reached
    assert 150 not in reached, "за полчаса 150 м — слишком быстро"
    assert g.can_prestige()


def test_relics_speed_up_next_run():
    _, fresh = play(minutes=12)
    _, boosted = play(minutes=12, relics=E.relics_for_depth(100))
    assert boosted[50] < fresh[50]
    assert 100 in boosted


def _dps_shares(g: Game) -> dict[str, float]:
    total = g.dps()
    return {did: n * g.digger_dps_each(did) / total for did, n in g.diggers.items() if n}


@pytest.fixture(scope="module")
def hour_run():
    return play(minutes=60)[0]


def test_weak_crews_pull_their_weight(hour_run):
    """Растущие пороги держат в деле и дешёвых копателей (при ×2 за 25 шт. их было 5)."""
    shares = _dps_shares(hour_run)
    assert sum(s >= 0.01 for s in shares.values()) >= 7, shares


def test_expensive_crews_still_matter(hour_run):
    """…но пороги не настолько сильны, чтобы дорогие копатели стали бесполезны."""
    shares = _dps_shares(hour_run)
    owned = [d.id for d in C.DIGGERS if hour_run.diggers.get(d.id)]
    top = max(shares, key=shares.get)
    assert top in owned[-4:], shares


def test_numbers_stay_finite_for_long_sessions():
    g, _ = play(minutes=120, relics=200)
    assert all(map(lambda x: x == x and x != float("inf"), [g.gold, g.dps(), g.tap_damage()]))
