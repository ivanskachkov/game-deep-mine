"""Бот для похода: тапает 4 раза в секунду, блокирует большую часть ударов, фармит и улучшает вещи.

Нужен тестам баланса: если после правки цифр бот проходит мир слишком быстро или застревает —
значит, и живому игроку будет не так, как задумано.
"""

import math
import random

from game import battle as B
from game.battle import Battle

TAPS_PER_SEC = 4
BLOCK_SKILL = 0.85        # доля ударов, которые бот успевает заблокировать
HERO_BONUS = 1.25         # к походу игрок приходит примерно с 25 достижениями
REST = 3.0                # секунд между волнами: посмотреть добычу, улучшить вещи
SAFETY = 0.85             # идём на волну, если ждём потерять не больше 85% здоровья


def average_combo() -> float:
    """Средний множитель комбо: пропущенный удар сбивает его, и разгон начинается заново."""
    if BLOCK_SKILL >= 1:
        return B.combo_mult(B.COMBO_CAP)
    taps = TAPS_PER_SEC * 3.0 / (1 - BLOCK_SKILL)  # тапов между пропущенными ударами
    ramp = min(taps, B.COMBO_CAP)
    return (ramp * B.combo_mult(ramp / 2) + (taps - ramp) * B.combo_mult(B.COMBO_CAP)) / taps


def expected_damage(b: Battle, wave: int) -> float:
    """Грубая оценка, сколько урона герой получит за волну."""
    dps = b.attack() * TAPS_PER_SEC * average_combo() * 0.9
    taken = 0.0
    for i in range(B.MOBS):
        attacks = B.mob_max_hp(wave, i) / dps / B.mob_at(wave, i).speed
        taken += attacks * B.mob_attack(wave, i) * (1 - BLOCK_SKILL * B.BLOCK_ABSORB)
    return taken


def pick_wave(b: Battle) -> int:
    """Самая дальняя волна, которую бот надеется пережить."""
    for wave in range(b.frontier, 0, -1):
        if expected_damage(b, wave) <= SAFETY * b.max_hp():
            return wave
    return 0


def spend(b: Battle) -> None:
    """Покупает улучшение с лучшей отдачей на осколок; если не хватает — копит на него."""
    while True:
        total_armor = sum(b.power(s) for s in B.ARMOR)
        best, best_ratio = None, 0.0
        for slot in B.SLOTS:
            share = 1.0 if slot == "weapon" else b.power(slot) / total_armor
            ratio = share / b.upgrade_price(slot)
            if ratio > best_ratio:
                best, best_ratio = slot, ratio
        if best is None or not b.upgrade(best):
            return


def fight(b: Battle, rng: random.Random) -> tuple[bool, float]:
    """Проходит выбранную волну. Возвращает (победа, сколько секунд это заняло)."""
    dt, t = 1 / TAPS_PER_SEC, 0.0
    decided_for = None  # для какого удара моба уже решили, блокировать или нет
    while True:
        if b.windup and decided_for != b.next_attack_at:
            decided_for = b.next_attack_at
            block = rng.random() < BLOCK_SKILL
        else:
            block = False
        if block:
            b.block()  # нажатие на блок — вместо удара
        elif b.hit().wave_cleared:
            return True, t
        died = b.advance(dt).died
        t += dt
        if died:
            return False, t


def play(hours: float, seed: int = 1, until_wave: int = B.TOTAL_WAVES) -> tuple[Battle, dict[int, float], float]:
    """Играет поход. Возвращает бой, словарь «пройдено волн → на каком часу» и потраченные часы."""
    rng = random.Random(seed)
    b = Battle(rng=rng, hero_bonus=HERO_BONUS)
    cleared_at: dict[int, float] = {}
    t = 0.0
    while t < hours * 3600 and b.cleared < until_wave:
        spend(b)
        b.select(pick_wave(b))
        before = b.cleared
        _, secs = fight(b, rng)
        t += secs + REST
        b.idle(secs + REST)
        if b.cleared > before:
            cleared_at[b.cleared] = t / 3600
    return b, cleared_at, t / 3600


def describe(b: Battle) -> str:
    gear = " ".join(f"{s[:2]}:r{b.tiers[s]}+{b.levels[s]}" for s in B.SLOTS)
    return (f"atk={b.attack():.3g} hp={b.max_hp():.3g} shards={b.shards} {gear} "
            f"deaths={b.stats.deaths} waves={b.stats.waves} kills={b.stats.kills}")


if __name__ == "__main__":
    battle, at, spent = play(40)
    prev = 0.0
    for n in sorted(at):
        mark = "  <- мир пройден" if n % B.WAVES == 0 else ""
        print(f"волна {n:2d}: {at[n]:6.2f} ч (+{(at[n] - prev) * 60:5.1f} мин){mark}")
        prev = at[n]
    print(f"итого {spent:.2f} ч, {describe(battle)}")
    assert math.isfinite(battle.attack())
