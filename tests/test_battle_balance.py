"""Темп похода: бот (4 тапа/с, блокирует 85% ударов) проходит все четыре мира.

Задумка: мир занимает 2–3 часа, первый — короче, потому что первые волны учебные.
Если после правки цифр эти тесты упали — поход стал слишком быстрым или слишком нудным.
"""

import math

import battle_bot as bot
import pytest

from game import battle as B


@pytest.fixture(scope="module")
def campaign():
    return bot.play(hours=20)


def _world_hours(cleared_at: dict[int, float]) -> list[float]:
    marks = [0.0] + [cleared_at[n * B.WAVES] for n in range(1, len(cleared_at) // B.WAVES + 1)]
    return [b - a for a, b in zip(marks, marks[1:])]


def test_first_waves_are_a_quick_tutorial(campaign):
    _, cleared_at, _ = campaign
    assert cleared_at[1] * 60 < 2, "первая волна — пара минут"
    assert cleared_at[4] * 60 < 8, "первые четыре волны проходятся подряд, без фарма"


def test_first_world_takes_about_an_hour_and_a_half(campaign):
    _, cleared_at, _ = campaign
    assert 0.9 <= cleared_at[B.WAVES] <= 2.2, cleared_at[B.WAVES]


def test_later_worlds_take_two_to_three_hours(campaign):
    b, cleared_at, _ = campaign
    assert b.finished, f"за 20 часов поход не пройден: {b.cleared} волн"
    hours = _world_hours(cleared_at)
    assert len(hours) == 4
    for n, h in enumerate(hours[1:], start=2):
        assert 1.8 <= h <= 3.6, f"мир {n}: {h:.1f} ч ({hours})"


def test_whole_campaign_is_about_ten_hours(campaign):
    _, cleared_at, _ = campaign
    assert 7.5 <= cleared_at[B.TOTAL_WAVES] <= 12.5, cleared_at[B.TOTAL_WAVES]


def test_no_wave_is_an_endless_wall(campaign):
    _, cleared_at, _ = campaign
    marks = [0.0] + [cleared_at[n] for n in range(1, B.TOTAL_WAVES + 1)]
    longest = max(b - a for a, b in zip(marks, marks[1:]))
    assert longest * 60 <= 60, f"на одну волну ушло {longest * 60:.0f} мин"


def test_waves_have_to_be_farmed(campaign):
    """Поход «чуть более длительный»: новую волну не взять с ходу, прошлые нужно пофармить."""
    b, _, _ = campaign
    assert 6 <= b.stats.waves / B.TOTAL_WAVES <= 20, b.stats.waves


def test_a_wave_takes_a_minute_or_two(campaign):
    b, _, spent = campaign
    seconds = spent * 3600 / (b.stats.waves + b.stats.deaths)
    assert 40 <= seconds <= 120, seconds


def test_all_gear_is_collected_by_the_end(campaign):
    b, _, _ = campaign
    assert set(b.tiers.values()) == {B.MAX_TIER}
    assert max(b.levels.values()) < B.LEVEL_CAP / 2, "до потолка улучшений далеко"
    assert all(math.isfinite(x) for x in (b.attack(), b.max_hp()))


def test_careful_player_rarely_dies(campaign):
    b, _, _ = campaign
    assert b.stats.deaths <= b.stats.waves * 0.1


def test_blocking_pays_off_but_is_not_mandatory(campaign, monkeypatch):
    """Игрок, который вообще не жмёт «Блок», тоже проходит мир — просто заметно дольше."""
    _, cleared_at, _ = campaign
    monkeypatch.setattr(bot, "BLOCK_SKILL", 0.0)
    _, lazy_at, _ = bot.play(hours=10, until_wave=B.WAVES)
    assert B.WAVES in lazy_at, "без блока первый мир не пройти и за 10 часов"
    assert 1.5 <= lazy_at[B.WAVES] / cleared_at[B.WAVES] <= 5


def test_casual_player_is_not_stuck(monkeypatch):
    """3 тапа в секунду и блок через раз — первый мир всё равно проходится за вечер-другой."""
    monkeypatch.setattr(bot, "TAPS_PER_SEC", 3)
    monkeypatch.setattr(bot, "BLOCK_SKILL", 0.4)
    _, cleared_at, _ = bot.play(hours=8, until_wave=B.WAVES)
    assert cleared_at.get(3, 99) * 60 < 30, "первые три волны — не дольше получаса"
    assert B.WAVES in cleared_at, cleared_at


def test_overnight_farm_is_a_bonus_not_a_replacement():
    """8 часов офлайна дают осколков примерно как полчаса-час ручной игры."""
    b = bot.Battle()
    b.cleared = 15
    offline = b.passive_rate() * 8 * 3600
    active_hour = B.wave_shards(b.cleared - 1) * 3600 / 75  # волна в ~75 секунд
    assert 0.3 <= offline / active_hour <= 1.0
