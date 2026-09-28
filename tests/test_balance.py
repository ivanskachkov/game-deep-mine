"""Бот играет «идеально» (4 тапа/с, покупает самое выгодное) и проверяет темп игры.

Если после правки баланса этот тест упал — игра стала слишком быстрой или слишком медленной.
"""

import random

import pytest

from game import content as C
from game import engine as E
from game.engine import Game

TAPS_PER_SEC = 4
PERK_PLAN = {"autotap": 2, "veterans": 1}  # сначала это, остальное — в «Силу предков»


def _value(g: Game) -> float:
    tap = g.tap_damage() * (1 + g.crit_chance() * (E.CRIT_MULT - 1))
    return (g.total_dps() + TAPS_PER_SEC * tap) * E.biome_gold_mult(g.depth) * g.effects().gold_mult


def spend_relics(g: Game) -> None:
    for pid, level in PERK_PLAN.items():
        while g.perk(pid) < level and g.buy_perk(pid):
            pass
    if all(g.perk(pid) >= level for pid, level in PERK_PLAN.items()):
        while g.buy_perk("power"):
            pass


def _best_purchase(g: Game):
    base = _value(g)
    options = []
    for d in C.DIGGERS:
        n = g.diggers.get(d.id, 0)
        if g.digger_maxed(d.id):
            continue
        step = E.next_milestone(n)
        to_milestone = (step[0] if step else E.DIGGER_CAP) - n
        # как живой игрок: либо один копатель, либо докупить до следующей ступени
        for k in {1, to_milestone}:
            g.diggers[d.id] = n + k
            gain = _value(g) - base
            g.diggers[d.id] = n
            cost = g.digger_cost(d.id, k)
            options.append((gain / cost, cost, lambda d=d, k=k: g.buy_digger(d.id, k)))
    if g.pickaxe_level < E.PICKAXE_CAP:
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
    if not options:  # всё докачано
        return 0.0, float("inf"), lambda: None
    return max(options, key=lambda o: o[0])


def play(minutes: float, relics: int = 0, seed: int = 1) -> tuple[Game, dict[int, float]]:
    """Возвращает игру и словарь «глубина → на какой минуте достигнута»."""
    g = Game(rng=random.Random(seed), relics=relics)
    reached: dict[int, float] = {}
    for sec in range(int(minutes * 60)):
        spend_relics(g)
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
        for mark in (10, 25, 50, 75, 100, 125, 150):
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


def test_first_run_pacing(first_run):
    g, reached = first_run
    assert 3 <= reached[100] <= 15, reached
    assert g.stats.max_depth < 225, "за полчаса предпоследний слой — слишком быстро"
    assert g.can_prestige()


@pytest.fixture(scope="module")
def full_game():
    """Вся игра: бот перерождается, когда упёрся на 10 минут, и играет, пока не дойдёт до дна
    и не докачает всех до максимума (или не выйдет время)."""
    g = Game(rng=random.Random(1))
    step, t, last_progress, best = 2, 0, 0, 0
    bottom_at, maxed = None, {}  # id → на каком часу впервые докачан
    while t < 7 * 3600 and not (bottom_at and len(maxed) == len(C.DIGGERS) + 1):
        spend_relics(g)
        for _ in range(TAPS_PER_SEC * step):
            g.tap()
        g.tick(step)
        t += step
        if g.nugget_active:
            g.claim_nugget()
        while True:
            _, cost, buy = _best_purchase(g)
            if cost > g.gold:
                break
            buy()
        for did in [d.id for d in C.DIGGERS if g.digger_maxed(d.id)] + ["pickaxe"] * (g.pickaxe_level >= E.PICKAXE_CAP):
            maxed.setdefault(did, t / 3600)
        if g.at_bottom and bottom_at is None:
            bottom_at = t / 3600
        if g.depth > best:
            best, last_progress = g.depth, t
        if not g.at_bottom and g.can_prestige() and t - last_progress > 600 and g.relics_on_prestige() >= 8:
            g.prestige()
            best, last_progress = 0, t
    return g, bottom_at, maxed


def test_bottom_is_reachable_in_a_few_hours(full_game):
    g, bottom_at, _ = full_game
    assert bottom_at is not None, f"до дна не дошли, рекорд {g.stats.max_depth} м"
    assert 1.5 <= bottom_at <= 5, bottom_at
    assert g.stats.prestiges >= 2, "дно без перерождений — слишком просто"


def test_every_digger_and_pickaxe_reach_the_cap(full_game):
    _, _, maxed = full_game
    missing = ({d.id for d in C.DIGGERS} | {"pickaxe"}) - set(maxed)
    assert not missing, missing


def test_cheap_diggers_max_out_first(full_game):
    """Сначала до 500 доходят дешёвые копатели, дорогие — ближе к финалу."""
    _, _, maxed = full_game
    cheap = [maxed[d.id] for d in C.DIGGERS[:3]]
    pricey = [maxed[d.id] for d in C.DIGGERS[-3:]]
    assert max(cheap) < min(pricey), maxed


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


def test_expensive_crews_still_matter(hour_run):
    """Через час главный вклад — у самых дорогих из нанятых копателей."""
    shares = _dps_shares(hour_run)
    owned = [d.id for d in C.DIGGERS if hour_run.diggers.get(d.id)]
    top = max(shares, key=shares.get)
    assert top in owned[-4:], shares


def test_numbers_stay_finite_for_long_sessions():
    g, _ = play(minutes=120, relics=200)
    assert all(map(lambda x: x == x and x != float("inf"), [g.gold, g.dps(), g.tap_damage()]))
