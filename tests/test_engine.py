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


def test_ten_layers_of_25_meters():
    assert [b.start for b in C.BIOMES] == list(range(0, E.MINE_DEPTH, E.LAYER_DEPTH))
    assert len(C.GUARDIANS) == len(C.BIOMES)
    for b in C.BIOMES:
        assert E.biome_at(b.start).name == b.name
        if b.start > 0:
            assert E.biome_at(b.start - 1).name != b.name
    # каждый следующий слой платит больше
    assert E.biome_gold_mult(C.BIOMES[1].start) > E.biome_gold_mult(0)


def test_next_biome_start():
    assert E.next_biome_start(0) == 25
    assert E.next_biome_start(C.BIOMES[-1].start) == E.MINE_DEPTH


# ───── дно ─────

def test_bottom_stops_digging(game):
    game.skip_meters(1000)
    assert game.depth == E.MINE_DEPTH and game.at_bottom
    assert not E.is_guardian(game.depth) and not E.is_vein(game.depth)


def test_damage_on_the_bottom_only_mines_gold(game):
    game.skip_meters(E.MINE_DEPTH)
    rep = game.tap()
    assert game.depth == E.MINE_DEPTH
    assert rep.broken == 0 and not rep.bottom
    assert rep.gold == pytest.approx(rep.damage * E.biome_gold_mult(E.MINE_DEPTH))


def test_killing_the_dragon_reaches_the_bottom_once(game):
    game.skip_meters(E.MINE_DEPTH - 1)
    assert game.at_guardian and E.guardian_at(game.depth).name == "Ядерный дракон"
    rep = game.tap()
    assert not rep.bottom
    game.block_hp = 1  # добиваем
    rep = game.tap()
    assert rep.bottom and game.at_bottom
    assert game.stats.bottoms == 1
    assert "bottom" in game.achievements
    assert not game.tap().bottom  # второй раз не сообщаем


def test_offline_on_the_bottom_still_pays(game):
    game.skip_meters(E.MINE_DEPTH)
    game.diggers["gnome"] = 10
    rep = game.apply_offline(3600)
    assert rep.meters == 0 and rep.gold > 0


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
    assert game.digger_cost("hamster") == pytest.approx(15 * E.DIGGER_COST_GROWTH)


def test_buy_max_diggers(game):
    game.gold = 10_000
    n = game.digger_max("hamster")
    assert game.buy_digger("hamster", n)
    assert game.digger_cost("hamster") > game.gold


@pytest.mark.parametrize("count, jump", [(25, 2), (75, 2.5), (175, 5), (425, 5), (500, 1)])
def test_digger_ladder_steps(game, count, jump):
    game.diggers["hamster"] = count - 1
    before = game.digger_dps_each("hamster")
    game.diggers["hamster"] = count
    assert game.digger_dps_each("hamster") == pytest.approx(before * jump)


def test_ladder_totals():
    """Лестница: ×2 на 25 шт., ×5 на 75, ×25 на 175, ×125 на 425 — итоговые множители."""
    assert [E.milestone_mult(n) for n in (0, 24, 25, 74, 75, 175, 424, 425, 500)] == [1, 1, 2, 2, 5, 25, 25, 125, 125]
    assert E.next_milestone(0) == (25, 2)
    assert E.next_milestone(100) == (175, 25)
    assert E.next_milestone(425) is None


def test_ladder_never_outpaces_prices():
    """Ступень должна окупать меньше, чем выросла цена за её штуки.

    Иначе каждый следующий копатель выгоднее предыдущего, и экономика улетает в бесконечность.
    """
    prev_count, prev_mult = 0, 1.0
    for count, mult in E.MILESTONES:
        price_growth = E.DIGGER_COST_GROWTH ** (count - prev_count)
        assert mult / prev_mult < price_growth, (count, mult)
        prev_count, prev_mult = count, mult


def test_digger_cap(game):
    game.gold = 1e30
    assert game.digger_max("hamster") == E.DIGGER_CAP
    assert game.buy_digger("hamster", E.DIGGER_CAP)
    assert game.digger_maxed("hamster")
    assert game.digger_cost("hamster") == math.inf
    assert game.digger_max("hamster") == 0
    assert not game.buy_digger("hamster")
    game.check_achievements()
    assert "max_1" in game.achievements


def test_every_digger_can_reach_500_for_sane_money():
    """При росте 1.03 даже 500 чёрных дыр стоят «всего» ~10²¹ — это реальная цифра к концу игры."""
    for d in C.DIGGERS:
        total = E.geometric_cost(d.base_cost, E.DIGGER_COST_GROWTH, 0, E.DIGGER_CAP)
        assert total < 1e22, d.id


def test_pickaxe_levels(game):
    assert E.pickaxe_base_damage(0) == 1
    assert E.pickaxe_base_damage(24) == 25
    assert E.pickaxe_base_damage(25) == 52  # (1+25) × 2
    game.gold = 1_000
    assert game.buy_pickaxe(3)
    assert game.pickaxe_level == 3
    assert game.tap_damage() == 4 * game.damage_mult()


def test_pickaxe_adds_share_of_crew_damage(game):
    """Кирка не отстаёт от бригады: каждый уровень — +0.5% её урона к тапу."""
    game.diggers["blackhole"] = 10
    dps = game.dps()
    flat = E.pickaxe_base_damage(40) * game.damage_mult()
    game.pickaxe_level = 40
    assert game.tap_damage() == pytest.approx(flat + dps * 0.20)


def test_pickaxe_cap(game):
    game.gold = 1e30
    assert game.pickaxe_max() == E.PICKAXE_CAP
    assert game.buy_pickaxe(E.PICKAXE_CAP)
    assert game.pickaxe_cost() == math.inf
    assert not game.buy_pickaxe()


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
