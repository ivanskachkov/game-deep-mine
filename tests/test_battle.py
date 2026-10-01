import json
import random

import pytest
from conftest import FixedRng

from game import battle as B
from game import content as C
from game import engine as E
from game.battle import Battle
from game.engine import Game


@pytest.fixture
def battle() -> Battle:
    """Поход, в котором случайные осколки и вещи не выпадают (кроме гарантированных)."""
    return Battle(rng=FixedRng(0.99))


def clear_wave(b: Battle) -> list[B.StrikeReport]:
    """Проходит текущую волну одним ударом на моба. Возвращает отчёты об убийствах."""
    bonus, b.hero_bonus = b.hero_bonus, 1e30
    reports = []
    while True:
        rep = b.hit()
        reports.append(rep)
        if rep.wave_cleared:
            b.hero_bonus = bonus
            b.hero_hp = b.max_hp()
            return reports


# ── волны и мобы ──

def test_campaign_is_four_worlds_of_ten_waves_of_ten_mobs():
    assert len(C.WORLDS) == 4 and B.WAVES == 10 and B.MOBS == 10
    assert B.TOTAL_WAVES == 40


@pytest.mark.parametrize("wave", range(B.TOTAL_WAVES))
def test_mobs_inside_a_wave_are_close_in_hp(wave):
    """«В рамках одной волны хп меняется не сильно»: между самым хилым и самым крепким
    из девяти обычных мобов — меньше чем 1.7 раза."""
    regular = [B.mob_max_hp(wave, i) for i in range(B.MOBS - 1)]
    assert max(regular) / min(regular) < 1.7
    assert B.mob_max_hp(wave, B.MOBS - 1) > max(regular) * 2, "десятый — элитный, он заметно крепче"


def test_each_wave_is_stronger_than_the_previous():
    for wave in range(B.TOTAL_WAVES - 1):
        if B.is_boss_wave(wave) or B.is_boss_wave(wave + 1):
            continue  # босса сравниваем отдельно
        hp = [sum(B.mob_max_hp(w, i) for i in range(B.MOBS)) for w in (wave, wave + 1)]
        assert hp[1] / hp[0] == pytest.approx(B.WAVE_GROWTH, rel=0.1)
        assert B.shard_value(wave + 1) >= B.shard_value(wave)


def test_last_mob_is_elite_and_tenth_wave_ends_with_a_boss():
    for w, world in enumerate(C.WORLDS):
        first = w * B.WAVES
        assert B.mob_at(first, B.MOBS - 1) is world.elite
        assert B.mob_at(first + B.WAVES - 1, B.MOBS - 1) is world.boss
        assert B.mob_at(first, 0) in world.mobs
        assert world.boss.hp > world.elite.hp


# ── комбо ──

def test_damage_grows_with_every_hit_in_a_row(battle):
    battle.mob_hp = 1e9
    dmg = [battle.hit().damage for _ in range(4)]
    assert dmg == pytest.approx([10, 11, 12, 13])


def test_combo_is_capped(battle):
    battle.mob_hp = 1e9
    for _ in range(B.COMBO_CAP + 15):
        rep = battle.hit()
    assert rep.combo == B.COMBO_CAP
    assert rep.damage == pytest.approx(B.HERO_ATK * B.combo_mult(B.COMBO_CAP))
    assert B.combo_mult(B.COMBO_CAP) == pytest.approx(3.0)


def test_combo_burns_out_without_hits(battle):
    battle.mob_hp = 1e9
    battle.blocking = True  # чтобы удар моба не мешал проверке
    for _ in range(5):
        battle.hit()
    battle.advance(B.COMBO_HOLD - 0.1)
    assert battle.combo_now == 5
    battle.blocking = True
    battle.advance(0.2)
    assert battle.combo_now == 0
    assert battle.hit().damage == pytest.approx(B.HERO_ATK)


# ── удары мобов и блок ──

def test_mobs_wait_until_the_hero_strikes_first(battle):
    assert battle.waiting
    rep = battle.advance(60)
    assert rep.hits == 0 and battle.hero_hp == battle.max_hp()
    battle.mob_hp = 1e9
    battle.hit()
    assert not battle.waiting
    assert battle.time_to_attack == pytest.approx(battle.current_mob.speed)


def test_unblocked_hit_hurts_and_resets_combo(battle):
    battle.mob_hp = 1e9
    for _ in range(6):
        battle.hit()
    rep = battle.advance(battle.current_mob.speed)
    assert (rep.hits, rep.blocked) == (1, 0)
    assert rep.damage == pytest.approx(B.mob_attack(0, 0))
    assert battle.hero_hp == pytest.approx(battle.max_hp() - rep.damage)
    assert battle.combo_now == 0


def test_block_during_windup_cuts_damage_and_keeps_combo(battle):
    battle.mob_hp = 1e9
    for _ in range(6):
        battle.hit()
    speed, windup = battle.current_mob.speed, C.WORLDS[0].windup
    battle.advance(speed - windup + 0.05)
    assert battle.windup
    for _ in range(3):  # комбо не должно сгореть, пока ждём удара
        battle.hit()
    assert battle.block() == "ok"
    rep = battle.advance(windup)
    assert (rep.hits, rep.blocked) == (0, 1)
    assert rep.damage == pytest.approx(B.mob_attack(0, 0) * (1 - B.BLOCK_ABSORB))
    assert battle.combo == 9
    assert battle.stats.blocks == 1
    assert not battle.blocking, "блок — на один удар"


def test_early_block_resets_combo_and_goes_on_cooldown(battle):
    battle.mob_hp = 1e9
    for _ in range(6):
        battle.hit()
    assert not battle.windup
    assert battle.block() == "early"
    assert battle.combo_now == 0
    assert battle.block() == "cooldown"
    battle.advance(B.BLOCK_COOLDOWN)
    assert battle.block_ready


def test_block_does_nothing_before_the_fight(battle):
    assert battle.block() == "idle"


def test_attack_rhythm_is_shared_by_the_whole_wave(battle):
    """Убил моба — следующий «донашивает» его замах: быстрые убийства не делают героя неуязвимым."""
    battle.hero_bonus = 1e30
    battle.hit()                        # первый удар: бой начался, первый моб убит
    left = battle.time_to_attack
    battle.advance(1.0)
    battle.hit()                        # второй моб убит
    assert battle.mob == 2
    assert battle.time_to_attack <= left - 1.0 + 1e-9


def test_death_restarts_the_wave_but_keeps_progress(battle):
    clear_wave(battle)
    shards = battle.shards
    assert battle.wave == 1
    battle.mob_hp = 1e30
    battle.hit()
    battle.mob, battle.hero_hp = 4, 0.5
    rep = battle.advance(30)
    assert rep.died
    assert battle.stats.deaths == 1
    assert (battle.wave, battle.mob, battle.cleared) == (1, 0, 1)
    assert battle.hero_hp == battle.max_hp() and battle.waiting
    assert battle.shards == shards
    assert battle.advance(100).hits == 0, "после гибели мобы снова ждут первого удара"


def test_health_is_restored_only_between_waves(battle):
    battle.hero_bonus = 1e30
    battle.hit()
    battle.hero_hp = 40.0
    for _ in range(B.MOBS - 2):
        battle.hit()
    assert battle.mob == B.MOBS - 1 and battle.hero_hp == 40.0
    assert battle.hit().wave_cleared
    assert battle.hero_hp == battle.max_hp()


# ── прохождение ──

def test_clearing_a_wave_opens_the_next_one(battle):
    assert battle.frontier == 0
    assert not battle.select(1), "дальше непройденной волны не пустят"
    reports = clear_wave(battle)
    assert len(reports) == B.MOBS
    last = reports[-1]
    assert last.wave_cleared and last.first_clear and not last.world_cleared
    assert (battle.cleared, battle.wave, battle.mob) == (1, 1, 0)
    assert battle.select(0) and battle.wave == 0


def test_first_clear_bonus_is_paid_once(battle):
    first = sum(r.shards for r in clear_wave(battle))
    assert first == B.ELITE_SHARDS + B.FIRST_CLEAR_SHARDS + B.FIRST_CLEAR_FLAT
    battle.select(0)
    again = clear_wave(battle)
    assert sum(r.shards for r in again) == B.ELITE_SHARDS
    assert not again[-1].first_clear
    assert battle.wave == 0, "при фарме остаёмся на выбранной волне"
    assert battle.cleared == 1


def test_world_cleared_after_ten_waves(battle):
    for n in range(B.WAVES):
        last = clear_wave(battle)[-1]
        assert last.world_cleared == (n == B.WAVES - 1)
    assert battle.world == 1 and battle.cleared == B.WAVES


def test_campaign_can_be_finished(battle):
    for _ in range(B.TOTAL_WAVES):
        clear_wave(battle)
    assert battle.finished
    assert battle.wave == B.TOTAL_WAVES - 1, "дальше волн нет — остаёмся на последней"
    clear_wave(battle)
    assert battle.cleared == B.TOTAL_WAVES


# ── добыча ──

def test_regular_mobs_drop_shards_by_chance():
    lucky = Battle(rng=FixedRng(0.0))
    lucky.hero_bonus = 1e30
    assert lucky.hit().shards == B.shard_value(0)
    unlucky = Battle(rng=FixedRng(0.99))
    unlucky.hero_bonus = 1e30
    assert unlucky.hit().shards == 0


def test_shard_drop_rate_matches_the_chance():
    b = Battle(rng=random.Random(3))
    b.hero_bonus = 1e30
    drops = total = 0
    for _ in range(300):
        b.select(0)
        for rep in clear_wave(b)[:-1]:
            total += 1
            drops += rep.shards > 0
    assert drops / total == pytest.approx(B.SHARD_CHANCE, abs=0.04)


def test_gear_is_guaranteed_once_in_ten_waves(battle):
    """Без удачи вещь всё равно выпадает — на каждой GEAR_PITY-й волне."""
    dropped = []
    for n in range(1, 2 * B.GEAR_PITY):
        battle.select(0)
        gear = clear_wave(battle)[-1].gear
        if gear:
            dropped.append(n)
            assert gear[1] == 1 and battle.tiers[gear[0]] == 1
    assert dropped == [B.GEAR_PITY]


def test_gear_only_drops_from_the_elite():
    b = Battle(rng=FixedRng(0.0))  # удача максимальная
    reports = clear_wave(b)
    assert [bool(r.gear) for r in reports] == [False] * (B.MOBS - 1) + [True]


def test_boss_gives_gear_on_first_kill(battle):
    for _ in range(B.WAVES - 1):
        clear_wave(battle)
    battle.pity = 0
    before = sum(battle.tiers.values())
    last = clear_wave(battle)[-1]
    assert last.gear and last.world_cleared
    assert sum(battle.tiers.values()) == before + 1


def test_full_set_turns_extra_gear_into_shards():
    b = Battle(rng=FixedRng(0.0))
    for _ in range(len(B.SLOTS)):
        b.select(0)
        clear_wave(b)
    assert all(t == 1 for t in b.tiers.values())
    b.select(0)
    last = clear_wave(b)[-1]
    assert last.duplicate and last.gear is None
    assert last.shards == (B.ELITE_SHARDS + B.DUPLICATE_SHARDS) * B.shard_value(0)


def test_gear_rank_matches_the_world():
    b = Battle(rng=FixedRng(0.0))
    for _ in range(B.WAVES):
        clear_wave(b)
    gear = clear_wave(b)[-1].gear
    assert gear[1] == 2
    assert b.power(gear[0]) == pytest.approx(B.TIER_MULT[2])


# ── снаряжение ──

def test_upgrade_costs_shards_and_makes_gear_stronger(battle):
    assert not battle.upgrade("weapon"), "осколков нет"
    battle.shards = 1000
    price = battle.upgrade_price("weapon")
    assert price == B.UPGRADE_COST
    assert battle.upgrade("weapon")
    assert battle.shards == 1000 - price
    assert battle.attack() == pytest.approx(B.HERO_ATK * B.LEVEL_GROWTH)
    assert battle.upgrade_price("weapon") > price


def test_upgrade_max_never_overspends(battle):
    for shards in (0, 74, 75, 500, 12_345, 10**9):
        battle.shards, battle.levels["chest"] = shards, 0
        n = battle.upgrade_max("chest")
        assert B.upgrade_cost(0, n) <= shards < B.upgrade_cost(0, n + 1)
        if n:
            assert battle.upgrade("chest", n)
            assert battle.shards >= 0


def test_armor_upgrade_heals_for_the_added_health(battle):
    battle.shards = 10_000
    battle.hero_hp = 30.0
    before = battle.max_hp()
    battle.upgrade("chest", 5)
    assert battle.max_hp() > before
    assert battle.hero_hp == pytest.approx(30.0 + battle.max_hp() - before)


def test_weapon_is_attack_and_armor_is_health(battle):
    battle.levels["weapon"] = 10
    assert battle.max_hp() == B.HERO_HP
    battle.levels["weapon"], battle.tiers["helmet"] = 0, 1
    assert battle.attack() == B.HERO_ATK
    assert battle.max_hp() == pytest.approx(B.HERO_HP * (3 + B.TIER_MULT[1]) / 4)


def test_every_slot_has_a_name_for_every_rank():
    assert len(B.TIER_MULT) == B.MAX_TIER + 1
    for slot in C.GEAR:
        assert len(slot.names) == B.MAX_TIER + 1
    assert [s.stat for s in C.GEAR].count("atk") == 1


# ── пассивный фарм ──

def test_passive_farm_needs_a_cleared_wave(battle):
    assert battle.passive_rate() == 0
    assert battle.idle(8 * 3600) == 0
    clear_wave(battle)
    assert battle.passive_rate() == pytest.approx(B.PASSIVE_SHARE * B.wave_shards(0) / B.WAVE_TIME_REF)


def test_passive_farm_accumulates_fractions(battle):
    clear_wave(battle)
    shards = battle.shards
    rate = battle.passive_rate()
    for _ in range(1000):
        battle.idle(0.25)
    assert battle.shards - shards == pytest.approx(rate * 250, abs=1)


def test_passive_farm_is_a_fraction_of_active_play(battle):
    for _ in range(5):
        clear_wave(battle)
    per_hour = battle.passive_rate() * 3600
    active_per_hour = B.wave_shards(battle.cleared - 1) * 3600 / B.WAVE_TIME_REF
    assert per_hour == pytest.approx(active_per_hour * B.PASSIVE_SHARE)


# ── связь с шахтой ──

def test_battle_opens_at_the_bottom_and_survives_prestige(game):
    assert not game.battle_unlocked
    game.skip_meters(E.MINE_DEPTH)
    assert game.battle_unlocked
    clear_wave(game.battle)
    game.battle.shards = 77
    game.prestige()
    assert game.battle_unlocked and game.depth == 0
    assert game.battle.cleared == 1 and game.battle.shards == 77


def test_gear_ranks_boost_mine_damage(game):
    base = game.damage_mult()
    game.battle.tiers["weapon"] = 2
    game.battle.tiers["boots"] = 1
    assert game.damage_mult() == pytest.approx(base * (1 + 3 * B.GEAR_MINE_BONUS))
    tap = game.tap_damage()
    game.battle.tiers = dict.fromkeys(B.SLOTS, B.MAX_TIER)
    assert game.tap_damage() == pytest.approx(tap / (1 + 3 * B.GEAR_MINE_BONUS) * 2)


def test_achievements_make_the_hero_stronger(game):
    assert game.battle.attack() == B.HERO_ATK
    game.tap()  # «Первый удар»
    assert len(game.achievements) == 1
    assert game.battle.attack() == pytest.approx(B.HERO_ATK * (1 + E.ACHIEVEMENT_BONUS))


def test_battle_achievements(game):
    game.battle.hero_bonus = 1e30
    rep = game.battle_hit()
    assert [a.id for a in rep.new_achievements] == ["mob_1"]
    assert game.battle.hero_bonus == pytest.approx(1 + E.ACHIEVEMENT_BONUS), "бонус пересчитан"
    for _ in range(B.WAVES):
        clear_wave(game.battle)
    game.battle.tiers = dict.fromkeys(B.SLOTS, 1)
    game.check_achievements()
    assert {"world_1", "gear_set"} <= game.achievements
    assert "world_2" not in game.achievements


def test_mine_tick_and_offline_bring_shards(game):
    clear_wave(game.battle)
    shards = game.battle.shards
    game.tick(3600)
    hour = game.battle.shards - shards
    assert hour == pytest.approx(game.battle.passive_rate() * 3600, abs=1) and hour > 0
    rep = game.apply_offline(2 * 3600)
    assert rep.shards == pytest.approx(2 * hour, abs=1)


def test_offline_shards_respect_the_offline_cap(game):
    clear_wave(game.battle)
    cap = game.effects().offline_cap
    assert game.apply_offline(cap * 10).shards == pytest.approx(game.battle.passive_rate() * cap, abs=1)


# ── сохранение ──

def test_battle_roundtrip_through_json():
    g = Game(rng=random.Random(5))
    for _ in range(13):
        clear_wave(g.battle)
    g.battle.shards += 5000
    g.battle.upgrade("weapon", 4)
    g.battle.select(7)
    g.tick(100)
    restored = Game.from_dict(json.loads(json.dumps(g.to_dict())))
    assert restored.to_dict() == g.to_dict()
    b = restored.battle
    assert (b.cleared, b.wave, b.levels["weapon"]) == (13, 7, 4)
    assert b.waiting and b.mob == 0 and b.hero_hp == b.max_hp(), "бой после загрузки начинается заново"


def test_v3_save_gets_a_fresh_battle():
    g = Game.from_dict({"v": 3, "depth": 250, "stats": {"max_depth": 250}, "achievements": ["tap_1", "bottom"]})
    assert g.battle_unlocked
    assert g.battle.to_dict() == Battle().to_dict()
    assert g.battle.hero_bonus == pytest.approx(1.02), "достижения из сейва уже работают в походе"


def test_battle_save_tolerates_garbage():
    b = Battle.from_dict({
        "shards": -5, "cleared": 999, "wave": 999, "pity": "x",
        "tiers": {"weapon": 99, "hat": 3, "boots": True},
        "levels": {"weapon": 10**9, "chest": 2.7},
        "stats": {"kills": "много", "deaths": 3},
    })
    assert b.shards == 0
    assert b.cleared == B.TOTAL_WAVES and b.wave == B.TOTAL_WAVES - 1
    assert b.tiers == {"weapon": B.MAX_TIER, "helmet": 0, "chest": 0, "legs": 0, "boots": 0}
    assert b.levels["weapon"] == B.LEVEL_CAP and b.levels["chest"] == 2
    assert b.stats.kills == 0 and b.stats.deaths == 3
    assert Battle.from_dict("мусор").to_dict() == Battle().to_dict()
