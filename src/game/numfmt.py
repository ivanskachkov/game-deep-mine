"""Форматирование больших чисел и длительностей для интерфейса."""

import math

SUFFIXES = ["", "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc"]


def _suffix(tier: int) -> str:
    if tier < len(SUFFIXES):
        return SUFFIXES[tier]
    # После Dc идут двухбуквенные: aa, ab, ... zz
    i = tier - len(SUFFIXES)
    return chr(ord("a") + (i // 26) % 26) + chr(ord("a") + i % 26)


def _trunc(x: float, digits: int) -> float:
    """Отбрасывает лишние знаки, а не округляет — чтобы не показывать больше, чем есть."""
    k = 10**digits
    return math.floor(x * k + 1e-9) / k


def fmt(n: float) -> str:
    """1234 -> '1.23K', 0.5 -> '0.5', 999 -> '999'."""
    if n != n:  # NaN
        return "?"
    if math.isinf(n):
        return "∞" if n > 0 else "-∞"
    if n < 0:
        return "-" + fmt(-n)
    if n < 10 and n != int(n):
        s = f"{_trunc(n, 1):.1f}"
        return s[:-2] if s.endswith(".0") else s
    if n < 1000:
        return str(int(n))

    tier = int(math.log10(n) // 3)
    scaled = n / 1000**tier
    if scaled >= 1000:  # защита от погрешности log10 на границах
        tier += 1
        scaled /= 1000
    elif scaled < 1:
        tier -= 1
        scaled *= 1000

    if scaled < 10:
        body = f"{_trunc(scaled, 2):.2f}"
    elif scaled < 100:
        body = f"{_trunc(scaled, 1):.1f}"
    else:
        body = f"{int(scaled)}"
    return body + _suffix(tier)


def fmt_duration(seconds: float) -> str:
    """75 -> '1 мин 15 с', 7300 -> '2 ч 1 мин'."""
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} ч {m} мин" if m else f"{h} ч"
    if m:
        return f"{m} мин {sec} с" if sec else f"{m} мин"
    return f"{sec} с"
