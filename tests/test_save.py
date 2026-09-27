import json
import math
import random

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


def test_block_hp_is_clamped_to_block_max():
    g = Game.from_dict({"depth": 10, "block_hp": 1e30})
    assert g.block_hp == E.block_max_hp(10)
