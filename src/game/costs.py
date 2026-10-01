"""Цены, растущие в геометрической прогрессии, — общие для шахты и похода."""

import math


def geometric_cost(base: float, growth: float, owned: int, n: int) -> float:
    """Цена n штук подряд, если уже куплено owned."""
    if n <= 0:
        return 0.0
    try:
        return base * growth**owned * (growth**n - 1) / (growth - 1)
    except OverflowError:
        return math.inf


def max_affordable(base: float, growth: float, owned: int, gold: float) -> int:
    first = geometric_cost(base, growth, owned, 1)
    if gold < first:
        return 0
    n = int(math.log(gold * (growth - 1) / first + 1) / math.log(growth))
    # поправка на погрешность float
    while n > 0 and geometric_cost(base, growth, owned, n) > gold:
        n -= 1
    while geometric_cost(base, growth, owned, n + 1) <= gold:
        n += 1
    return n
