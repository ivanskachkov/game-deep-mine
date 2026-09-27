import math

import pytest

from game import content as C
from game import engine as E
from game.engine import Game


# ───── блоки и биомы ─────

def test_new_game_starts_at_surface(game):
    assert game.depth == 0
    assert game.gold == 0
    assert game.block_hp == E.BLOCK_BASE_HP
    assert E.biome_at(0).name == "Дёрн"


def test_every_tenth_meter_is_a_vein():
    assert [d for d in range(30) if E.is_vein(d)] == [9, 19, 29]
    assert E.block_max_hp(9) == pytest.approx(E.BLOCK_BASE_HP * E.BLOCK_HP_GROWTH**9 * E.VEIN_HP_MULT)


def test_block_hp_does_not_overflow_at_absurd_depth():
    hp = E.block_max_hp(1_000_000)
    assert math.isfinite(hp) and hp > 0


def test_biome_boundaries_and_endless_abyss():
    for b in C.BIOMES:
        assert E.biome_at(b.start).name == b.name
        if b.start > 0:
            assert E.biome_at(b.start - 1).name != b.name
    assert E.biome_at(C.ABYSS_START).name == "Бездна I"
    assert E.biome_at(C.ABYSS_START + C.ABYSS_STEP).name == "Бездна II"
    assert E.biome_at(C.ABYSS_START + 50 * C.ABYSS_STEP).name == "Бездна 51"
    # каждый следующий биом платит больше
    assert E.biome_gold_mult(C.BIOMES[1].start) > E.biome_gold_mult(0)


def test_next_biome_start():
    assert E.next_biome_start(0) == C.BIOMES[1].start
    assert E.next_biome_start(C.BIOMES[-1].start) == C.ABYSS_START
    assert E.next_biome_start(C.ABYSS_START) == C.ABYSS_START + C.ABYSS_STEP


# ───── тапы, урон, золото ─────

def test_tap_deals_damage_and_pays_gold(game):
    rep = game.tap()
    assert rep.damage == 1
    assert rep.gold == 1  # на поверхности 1 урон = 1 золото
    assert game.block_hp == E.BLOCK_BASE_HP - 1
    assert game.stats.taps == 1


def test_breaking_block_goes_one_meter_down(game):
    taps = 0
    while game.depth == 0:
        game.tap()
        taps += 1
    assert taps == int(E.BLOCK_BASE_HP)
    assert game.depth == 1
    # лишний урон последнего тапа ушёл в следующий блок (первое достижение даёт +1%)
    assert E.block_max_hp(1) - game.tap_damage() < game.block_hp <= E.block_max_hp(1)
    assert game.stats.blocks == 1


def test_overflow_damage_carries_to_next_blocks(game):
    total = sum(E.block_max_hp(d) for d in range(5)) + 1
    gold, broken = game._apply_damage(total)
    assert broken == 5
    assert game.depth == 5
    assert game.block_hp == pytest.approx(E.block_max_hp(5) - 1)


def test_vein_pays_five_times_more(game):
    game.depth = 9
    game.block_hp = E.block_max_hp(9)
    rep = game.tap()
    assert rep.gold == pytest.approx(rep.damage * E.biome_gold_mult(9) * E.VEIN_GOLD_MULT)


def test_crit_multiplies_damage(crit_game):
    rep = crit_game.tap()
    assert rep.crit
    assert rep.damage == E.CRIT_MULT
    assert crit_game.stats.crits == 1


def test_idle_tick_uses_dps(game):
    game.diggers["gnome"] = 3  # 3 урона/с, блок на поверхности не пробивается
    rep = game.tick(1.0)
    assert rep.gold == pytest.approx(3.0)
    assert game.block_hp == pytest.approx(E.BLOCK_BASE_HP - 3)
    assert game.stats.play_time == 1.0


def test_zero_or_negative_dt_is_ignored(game):
    game.diggers["gnome"] = 10
    game.tick(0)
    game.tick(-5)
    assert game.gold == 0 and game.time == 0


# ───── покупки ─────

def test_cost_formula_matches_sum_of_single_purchases():
    base, growth = 15, 1.15
    for owned in (0, 3, 40):
        for n in (1, 10, 25):
            singles = sum(base * growth ** (owned + i) for i in range(n))
            assert E.geometric_cost(base, growth, owned, n) == pytest.approx(singles)


@pytest.mark.parametrize("gold", [0, 14.9, 15, 100, 12_345, 1e9, 1e30])
def test_max_affordable_is_exact(gold):
    n = E.max_affordable(15, 1.15, 7, gold)
    assert E.geometric_cost(15, 1.15, 7, n) <= gold
    assert E.geometric_cost(15, 1.15, 7, n + 1) > gold


def test_buy_digger(game):
    assert not game.buy_digger("hamster")  # денег нет
    game.gold = 15
    assert game.buy_digger("hamster")
    assert game.diggers["hamster"] == 1
    assert game.gold == 0
    assert game.digger_cost("hamster") == pytest.approx(15 * 1.15)


def test_buy_max_diggers(game):
    game.gold = 10_000
    n = game.digger_max("hamster")
    assert game.buy_digger("hamster", n)
    assert game.digger_cost("hamster") > game.gold


@pytest.mark.parametrize("count, factor", [(25, 2), (50, 4), (75, 6), (100, 8), (125, 8), (500, 8)])
def test_digger_milestones_grow(game, count, factor):
    game.diggers["hamster"] = count - 1
    before = game.digger_dps_each("hamster")
    game.diggers["hamster"] = count
    assert game.digger_dps_each("hamster") == pytest.approx(before * factor)


def test_milestone_totals():
    assert [E.milestone_mult(n) for n in (0, 24, 25, 50, 75, 100, 125)] == [1, 1, 2, 8, 48, 384, 3072]
    assert E.next_milestone(0) == (25, 2)
    assert E.next_milestone(60) == (75, 6)
    assert E.next_milestone(1000) == (1025, 8)
    assert math.isfinite(E.milestone_mult(100_000))


def test_milestones_never_outpace_prices():
    """Порог должен возвращать меньше, чем выросла цена за эти 25 штук.

    Иначе каждый следующий копатель окупается лучше предыдущего,
    и экономика улетает в бесконечность.
    """
    price_growth = E.DIGGER_COST_GROWTH**E.MILESTONE_EVERY  # ≈ ×33
    assert all(f < price_growth * 0.7 for f in E.MILESTONE_FACTORS)


def test_pickaxe_levels(game):
    assert E.pickaxe_base_damage(0) == 1
    assert E.pickaxe_base_damage(24) == 25
    assert E.pickaxe_base_damage(25) == 52  # (1+25) × 2
    game.gold = 1_000
    assert game.buy_pickaxe(3)
    assert game.pickaxe_level == 3
    assert game.tap_damage() == 4 * game.damage_mult()


def test_upgrade_locked_until_depth(game):
    game.gold = 1e12
    assert not game.buy_upgrade("steel_pick")  # откроется на 25 м
    game.stats.max_depth = 25
    assert game.buy_upgrade("steel_pick")
    assert not game.buy_upgrade("steel_pick")  # второй раз не продаётся


def test_upgrades_apply_effects(game):
    game.stats.max_depth = 1000
    game.gold = 1e15
    base_tap = game.tap_damage()
    game.buy_upgrade("steel_pick")
    assert game.tap_damage() == pytest.approx(base_tap * 2)

    game.diggers["gnome"] = 10
    dps = game.dps()
    game.buy_upgrade("coffee")
    assert game.dps() == pytest.approx(dps * 1.5)

    rate = game.gold_rate(0)
    game.buy_upgrade("map")
    assert game.gold_rate(0) == pytest.approx(rate * 1.5)

    game.buy_upgrade("helmet")
    assert game.tap_damage() == pytest.approx(2 * game.damage_mult() + game.dps() * 0.01)


# ───── самородки и лихорадка ─────

def test_nugget_spawns_and_expires(game):
    rep = game.tick(E.FIRST_NUGGET_AT)
    assert rep.nugget_spawned and game.nugget_active
    rep = game.tick(E.NUGGET_LIFETIME)
    assert rep.nugget_expired and not game.nugget_active
    assert game.next_nugget_at > game.time


def test_nugget_gives_gold(game):
    game.spawn_nugget_now()
    rep = game.claim_nugget()
    assert rep.kind == "gold" and rep.gold >= 25
    assert game.gold == rep.gold
    assert game.claim_nugget() is None  # второй раз не поймать


def test_nugget_gives_fever(crit_game):
    g = crit_game
    g.spawn_nugget_now()
    rep = g.claim_nugget()
    assert rep.kind == "fever" and g.fever_active
    assert g.gold_rate(0) == pytest.approx(E.FEVER_MULT)
    tick = g.tick(E.FEVER_DURATION)
    assert tick.fever_ended and not g.fever_active


# ───── офлайн ─────

def test_offline_income_is_half_and_capped(game):
    game.depth = 100  # блоки тут толстые — за 100 с ни один не пробить
    game.block_hp = E.block_max_hp(100)
    game.diggers["gnome"] = 10
    rep = game.apply_offline(100)
    assert rep.meters == 0
    assert rep.gold == pytest.approx(10 * 100 * 0.5 * E.biome_gold_mult(100))
    capped = Game(rng=game.rng, diggers={"gnome": 10})
    assert capped.apply_offline(10**9).seconds == E.OFFLINE_CAP


def test_offline_does_not_extend_fever(crit_game):
    g = crit_game
    g.spawn_nugget_now()
    g.claim_nugget()
    assert g.fever_active
    g.apply_offline(3600)
    assert not g.fever_active


def test_nightshift_doubles_offline(game):
    game.diggers["gnome"] = 10
    game.stats.max_depth = 1000
    assert game.effects().offline_eff == 0.5
    game.upgrades.add("nightshift")
    assert game.effects().offline_eff == 1.0


# ───── перерождение и достижения ─────

def test_prestige_requires_depth(game):
    game.max_depth_run = E.PRESTIGE_MIN_DEPTH - 1
    assert not game.can_prestige()
    assert game.prestige() == 0


def test_prestige_resets_run_but_keeps_meta(game):
    game.tap()
    game.depth = game.max_depth_run = game.stats.max_depth = 150
    game.gold = 1e9
    game.diggers = {"gnome": 50}
    game.upgrades = {"helmet"}
    game.pickaxe_level = 40
    expected = E.relics_for_depth(150)
    assert expected == 25

    assert game.prestige() == expected
    assert game.relics == expected
    assert (game.gold, game.depth, game.pickaxe_level) == (0, 0, 0)
    assert game.diggers == {} and game.upgrades == set()
    assert game.stats.max_depth == 150
    assert "tap_1" in game.achievements and "prestige_1" in game.achievements


def test_unspent_relics_do_nothing_power_perk_boosts_damage(game):
    game.relics = 10
    assert game.damage_mult() == 1.0
    for _ in range(10):
        assert game.buy_perk("power")
    assert game.relics == 0
    assert game.achievements == {"perk_1"}  # «Коллекционер» даёт ещё +1%
    assert game.damage_mult() == pytest.approx(2.0 * 1.01)
    assert game.tap_damage() == pytest.approx(2.0 * 1.01)


def test_first_tap_unlocks_achievement(game):
    rep = game.tap()
    assert [a.id for a in rep.new_achievements] == ["tap_1"]
    assert game.tap().new_achievements == []


def test_skip_meters_for_testers(game):
    game.skip_meters(120)
    assert game.depth == game.max_depth_run == game.stats.max_depth == 120
    assert game.gold == 0
    assert game.block_hp == E.block_max_hp(120)
    assert game.can_prestige()


def test_all_achievement_checks_run_on_fresh_game(game):
    # ни одна проверка не должна падать и ничего не должно открыться «даром»
    assert game.check_achievements() == []
