import json
import math
import random

import pytest

from game import engine as E
from game.engine import Game


def _played_game() -> Game:
    g = Game(rng=random.Random(7))
    g.gold = 1e6
    for _ in range(200):
        g.tap()
    g.buy_digger("hamster", 5)
    g.buy_pickaxe(3)
    g.stats.max_depth = max(g.stats.max_depth, 30)
    g.buy_upgrade("helmet")
    g.tick(12.5)
    return g


def test_roundtrip_through_json():
    g = _played_game()
    blob = json.dumps(g.to_dict())
    restored = Game.from_dict(json.loads(blob))
    assert restored.to_dict() == g.to_dict()


def test_load_empty_dict_gives_new_game():
    g = Game.from_dict({})
    assert g.to_dict() == Game().to_dict()


def test_load_tolerates_garbage():
    g = Game.from_dict(
        {
            "gold": "много",
            "depth": -5,
            "block_hp": math.nan,
            "relics": math.inf,
            "diggers": {"hamster": 3, "dragon": 99, "gnome": "x"},
            "upgrades": ["helmet", "time_machine"],
            "achievements": ["tap_1", "???"],
            "stats": {"taps": True, "crits": 4},
        }
    )
    assert g.gold == 0
    assert g.depth == 0
    assert g.block_hp == E.block_max_hp(0)
    assert g.relics == 0
    assert g.diggers == {"hamster": 3}
    assert g.upgrades == {"helmet"}
    assert g.achievements == {"tap_1"}
    assert g.stats.taps == 0 and g.stats.crits == 4


def test_perks_and_guardian_roundtrip():
    g = Game(rng=random.Random(1))
    g.relics = 20
    g.buy_perk("autotap")
    g.buy_perk("slayer")
    g.skip_meters(24)
    g.tick(7)
    restored = Game.from_dict(json.loads(json.dumps(g.to_dict())))
    assert restored.perks == {"autotap": 1, "slayer": 1}
    assert restored.guardian_left == pytest.approx(g.guardian_left)
    assert restored.to_dict() == g.to_dict()


def test_v1_relics_become_power_levels():
    """В первой версии каждая реликвия сама давала +10% — переносим это в «Силу предков»."""
    g = Game.from_dict({"v": 1, "relics": 7})
    assert g.relics == 0
    assert g.perks == {"power": 7}
    assert g.damage_mult() == pytest.approx(1.7)


def test_bad_perks_are_ignored_or_capped():
    g = Game.from_dict({"v": 2, "perks": {"autotap": 99, "time_travel": 3, "eye": -2}})
    assert g.perks == {"autotap": 10}


def test_v2_save_fits_into_the_new_limits():
    """До потолков: шахта была бесконечной, копатели и кирка — без предела."""
    g = Game.from_dict({"v": 2, "depth": 400, "max_depth_run": 420, "pickaxe_level": 180,
                        "diggers": {"hamster": 900, "gnome": 20}, "stats": {"max_depth": 700}})
    assert g.depth == E.MINE_DEPTH and g.at_bottom
    assert g.max_depth_run == E.MINE_DEPTH and g.stats.max_depth == E.MINE_DEPTH
    assert g.pickaxe_level == E.PICKAXE_CAP
    assert g.diggers == {"hamster": E.DIGGER_CAP, "gnome": 20}


def test_block_hp_is_clamped_to_block_max():
    g = Game.from_dict({"depth": 10, "block_hp": 1e30})
    assert g.block_hp == E.block_max_hp(10)
