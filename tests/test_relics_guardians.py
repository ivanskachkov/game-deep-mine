import pytest

from game import content as C
from game import engine as E
from game.engine import Game


def at_guardian(game: Game, depth: int = 24) -> Game:
    game.skip_meters(depth - game.depth)
    assert game.at_guardian
    return game


# ───── стражи ─────

def test_guardian_every_25_meters_beats_vein():
    assert [d for d in range(100) if E.is_guardian(d)] == [24, 49, 74, 99]
    assert not E.is_vein(49)  # на 50-м метре страж, а не жила
    base = E.BLOCK_BASE_HP * E.BLOCK_HP_GROWTH**24
    assert E.block_max_hp(24) == pytest.approx(base * E.GUARDIAN_HP_MULT)
    assert E.guardian_at(24).name == C.GUARDIANS[0].name
    assert E.guardian_at(C.ABYSS_START + 24) == C.ABYSS_GUARDIAN


def test_reaching_guardian_starts_timer(game):
    at_guardian(game)
    assert game.guardian_left == E.GUARDIAN_TIME


def test_guardian_heals_when_timer_runs_out(game):
    at_guardian(game)
    game.block_hp /= 2
    rep = game.tick(E.GUARDIAN_TIME)
    assert rep.guardian_failed
    assert game.block_hp == E.block_max_hp(24)
    assert game.guardian_left == E.GUARDIAN_TIME
    assert game.stats.guardians_failed == 1
    assert game.depth == 24


def test_guardian_pays_triple_gold(game):
    at_guardian(game)
    assert game.gold_rate() == pytest.approx(E.biome_gold_mult(24) * E.GUARDIAN_GOLD_MULT)


def test_new_guardian_gives_one_trophy_relic(game):
    at_guardian(game)
    game._apply_damage(game.block_hp)
    assert game.depth == 25
    assert (game.relics, game.stats.guardians, game.stats.max_guardian) == (1, 1, 24)

    # тот же страж ещё раз (например, после перерождения) — уже без реликвии
    game.depth, game.block_hp = 24, E.block_max_hp(24)
    game._apply_damage(game.block_hp)
    assert (game.relics, game.stats.guardians) == (1, 2)


def test_tap_report_counts_guardians(game):
    game.pickaxe_level = 3000  # один тап пробивает сразу много метров
    rep = game.tap()
    assert rep.guardians >= 2
    assert rep.trophies == rep.guardians
    assert game.relics == rep.trophies


def test_skipping_meters_is_not_a_victory(game):
    game.skip_meters(100)
    assert game.stats.guardians == 0 and game.relics == 0


def test_offline_stops_at_guardian_it_cannot_beat(game):
    at_guardian(game)
    game.diggers["gnome"] = 1
    rep = game.apply_offline(3600)
    assert rep.meters == 0 and rep.stuck_at_guardian
    assert rep.gold > 0  # но золото со стража всё равно капает
    assert game.guardian_left == E.GUARDIAN_TIME


def test_offline_passes_guardian_when_strong_enough(game):
    at_guardian(game)
    game.diggers["blackhole"] = 10
    rep = game.apply_offline(60)
    assert rep.meters > 0 and game.depth > 24


# ───── реликварий ─────

def test_perk_prices():
    assert [E.perk_cost("autotap", lvl) for lvl in range(4)] == [3, 6, 12, 24]
    assert {E.perk_cost("power", lvl) for lvl in range(50)} == {1}


def test_buying_perks(game):
    assert not game.buy_perk("autotap")
    game.relics = 9
    assert game.buy_perk("autotap") and game.buy_perk("autotap")
    assert game.perk("autotap") == 2 and game.relics == 0


def test_perk_max_level(game):
    game.relics = 10**6
    while game.buy_perk("eye"):
        pass
    assert game.perk("eye") == C.PERKS_BY_ID["eye"].max_level
    assert game.perk_maxed("eye")


def test_respec_refunds_everything(game):
    game.relics = 12
    game.buy_perk("autotap")
    game.buy_perk("autotap")
    for _ in range(3):
        game.buy_perk("power")
    assert game.relics == 0
    assert game.respec() == 12
    assert game.relics == 12 and game.perks == {}


def test_autotap_hits_on_its_own(game):
    game.perks["autotap"] = 2
    expected = 2 * game.tap_damage() * (1 + game.crit_chance() * (E.CRIT_MULT - 1))
    rep = game.tick(1.0)
    assert rep.auto_taps == 2
    assert game.block_hp == pytest.approx(E.BLOCK_BASE_HP - expected)
    assert game.stats.taps == 0  # в статистику ручных тапов не идёт


def test_autotap_accumulates_fractional_seconds(game):
    game.perks["autotap"] = 1
    assert sum(game.tick(0.1).auto_taps for _ in range(10)) == 1


def test_perk_effects(game):
    game.diggers["gnome"] = 10
    dps, cost = game.dps(), game.digger_cost("gnome")
    game.perks.update(veterans=2, union=3, eye=2, slayer=1, luck=1, blaze=2, night=3, archeo=2)
    fx = game.effects()
    assert game.dps() == pytest.approx(dps * 1.5**2)
    assert game.digger_cost("gnome") == pytest.approx(cost * 0.95**3)
    assert fx.crit_chance == pytest.approx(E.BASE_CRIT_CHANCE + 0.06)
    assert (fx.guardian_time, fx.guardian_dmg) == (35.0, 1.25)
    assert (fx.nugget_life, fx.fever_time) == (12.0, 50.0)
    assert fx.offline_eff == pytest.approx(0.8)
    assert fx.offline_cap == E.OFFLINE_CAP + 3 * 3600
    assert fx.relic_mult == pytest.approx(1.3)


def test_offline_efficiency_is_capped(game):
    game.upgrades.add("nightshift")
    game.perks["night"] = 5
    assert game.effects().offline_eff == E.MAX_OFFLINE_EFF


def test_slayer_hits_guardians_harder(game):
    at_guardian(game)
    game.perks["slayer"] = 2
    hp = game.block_hp
    game._apply_damage(100)
    assert game.block_hp == pytest.approx(hp - 150)


def test_inheritance_and_archeologist_on_prestige(game):
    game.perks.update(inherit=2, archeo=1)
    game.max_depth_run = 150
    expected = int(E.relics_for_depth(150) * 1.15)
    assert game.prestige() == expected
    assert game.gold == 10_000
    assert game.relics == expected
    assert game.stats.relics_total == expected
