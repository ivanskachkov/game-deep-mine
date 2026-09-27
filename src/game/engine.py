"""Игровая логика «Бездонной шахты». Чистый Python — никакого Flet, всё тестируется.

Экономика в двух словах:
  * урон (тапы + бригада) пробивает блоки; каждый блок = 1 метр глубины;
  * каждая единица урона приносит золото по курсу текущего биома (глубже — дороже);
  * здоровье блоков растёт экспоненциально, поэтому рано или поздно упираешься в стену;
  * перерождение сбрасывает забег, но даёт реликвии (+10% урона каждая).
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, fields

from . import content as C

SAVE_VERSION = 1

BLOCK_BASE_HP = 5.0
BLOCK_HP_GROWTH = 1.16
HP_CAP = 1e300
VEIN_EVERY = 10
VEIN_HP_MULT = 2.5
VEIN_GOLD_MULT = 5.0
BIOME_GOLD_GROWTH = 1.3

DIGGER_COST_GROWTH = 1.15
# Пороги бригады: каждые 25 копателей одного типа умножают их урон.
# Множители растут от порога к порогу (последний повторяется дальше).
MILESTONE_EVERY = 25
MILESTONE_FACTORS = (2, 4, 6, 8)
MILESTONE_CAP = 1e300
PICKAXE_MILESTONE_EVERY = 25  # кирка: каждые 25 уровней урон ×2
PICKAXE_BASE_COST = 10.0
PICKAXE_COST_GROWTH = 1.16

BASE_CRIT_CHANCE = 0.04
CRIT_MULT = 5.0

RELIC_BONUS = 0.10
ACHIEVEMENT_BONUS = 0.01
PRESTIGE_MIN_DEPTH = 100
PRESTIGE_DIVISOR = 30

FEVER_DURATION = 30.0
FEVER_MULT = 7.0
NUGGET_LIFETIME = 10.0
NUGGET_INTERVAL = (45.0, 120.0)
FIRST_NUGGET_AT = 40.0
NUGGET_GOLD_SECONDS = 90.0

OFFLINE_CAP = 8 * 3600.0
BASE_OFFLINE_EFF = 0.5
MAX_BLOCKS_PER_HIT = 10_000


# ───────────────────────── чистые формулы ─────────────────────────

def is_vein(depth: int) -> bool:
    """Каждый 10-й метр — жила: крепче, но золота впятеро больше."""
    return (depth + 1) % VEIN_EVERY == 0


def block_max_hp(depth: int) -> float:
    try:
        hp = BLOCK_BASE_HP * BLOCK_HP_GROWTH**depth
    except OverflowError:
        hp = HP_CAP
    hp = min(hp, HP_CAP)
    return hp * VEIN_HP_MULT if is_vein(depth) else hp


def biome_index(depth: int) -> int:
    if depth >= C.ABYSS_START:
        return len(C.BIOMES) + (depth - C.ABYSS_START) // C.ABYSS_STEP
    idx = 0
    for i, b in enumerate(C.BIOMES):
        if depth >= b.start:
            idx = i
    return idx


def biome_at(depth: int) -> C.Biome:
    i = biome_index(depth)
    if i < len(C.BIOMES):
        return C.BIOMES[i]
    k = i - len(C.BIOMES)
    top, bottom, block = C.ABYSS_PALETTES[k % len(C.ABYSS_PALETTES)]
    num = C.ROMAN[k] if k < len(C.ROMAN) else str(k + 1)
    return C.Biome(C.ABYSS_START + k * C.ABYSS_STEP, f"Бездна {num}", "🌑", top, bottom, block)


def next_biome_start(depth: int) -> int:
    i = biome_index(depth) + 1
    if i < len(C.BIOMES):
        return C.BIOMES[i].start
    return C.ABYSS_START + (i - len(C.BIOMES)) * C.ABYSS_STEP


def biome_gold_mult(depth: int) -> float:
    return BIOME_GOLD_GROWTH ** biome_index(depth)


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


def relics_for_depth(depth: int) -> int:
    if depth < PRESTIGE_MIN_DEPTH:
        return 0
    return int((depth / PRESTIGE_DIVISOR) ** 2)


def pickaxe_base_damage(level: int) -> float:
    """Ур. 0 = 1 урон, +1 за уровень, ×2 каждые 25 уровней."""
    return (1 + level) * 2 ** (level // PICKAXE_MILESTONE_EVERY)


def milestone_factor(k: int) -> float:
    """Множитель k-го порога бригады (k = 1 — это 25 шт., k = 2 — 50 шт. …)."""
    f = MILESTONE_FACTORS
    return f[k - 1] if k <= len(f) else f[-1]


def milestone_mult(n: int) -> float:
    """Итоговый множитель для n копателей одного типа — произведение пройденных порогов."""
    m = 1.0
    for k in range(1, n // MILESTONE_EVERY + 1):
        m = min(m * milestone_factor(k), MILESTONE_CAP)
    return m


def next_milestone(n: int) -> tuple[int, float]:
    """(сколько штук нужно для следующего порога, его множитель)."""
    k = n // MILESTONE_EVERY + 1
    return k * MILESTONE_EVERY, milestone_factor(k)


# ───────────────────────── состояние ─────────────────────────

@dataclass
class Stats:
    taps: int = 0
    crits: int = 0
    blocks: int = 0
    veins: int = 0
    nuggets: int = 0
    prestiges: int = 0
    max_depth: int = 0
    gold_total: float = 0.0
    play_time: float = 0.0


@dataclass
class Effects:
    tap_mult: float = 1.0
    tap_dps_share: float = 0.0
    crit_chance: float = BASE_CRIT_CHANCE
    gold_mult: float = 1.0
    digger_mult: float = 1.0
    nugget_freq: float = 1.0
    offline_eff: float = BASE_OFFLINE_EFF


@dataclass
class HitReport:
    damage: float
    gold: float
    broken: int
    crit: bool = False
    new_achievements: list = field(default_factory=list)


@dataclass
class TickReport:
    gold: float = 0.0
    broken: int = 0
    nugget_spawned: bool = False
    nugget_expired: bool = False
    fever_ended: bool = False
    new_achievements: list = field(default_factory=list)


@dataclass
class NuggetReport:
    kind: str  # "gold" | "fever"
    gold: float = 0.0
    new_achievements: list = field(default_factory=list)


@dataclass
class OfflineReport:
    seconds: float
    gold: float
    meters: int


@dataclass
class Game:
    gold: float = 0.0
    depth: int = 0
    block_hp: float = -1.0  # -1 = «возьми полное HP блока»
    max_depth_run: int = 0
    pickaxe_level: int = 0
    diggers: dict = field(default_factory=dict)
    upgrades: set = field(default_factory=set)
    relics: int = 0
    achievements: set = field(default_factory=set)
    stats: Stats = field(default_factory=Stats)
    time: float = 0.0
    fever_until: float = 0.0
    nugget_until: float = 0.0
    next_nugget_at: float = FIRST_NUGGET_AT
    rng: random.Random = field(default_factory=random.Random, repr=False, compare=False)

    def __post_init__(self):
        if self.block_hp <= 0:
            self.block_hp = block_max_hp(self.depth)

    # ── производные величины ──

    def effects(self) -> Effects:
        fx = Effects()
        for uid in self.upgrades:
            u = C.UPGRADES_BY_ID[uid]
            fx.tap_mult *= u.tap_mult
            fx.tap_dps_share += u.tap_dps_share
            fx.crit_chance += u.crit_chance
            fx.gold_mult *= u.gold_mult
            fx.digger_mult *= u.digger_mult
            fx.nugget_freq *= u.nugget_freq
            fx.offline_eff += u.offline_eff
        return fx

    def damage_mult(self) -> float:
        return (1 + RELIC_BONUS * self.relics) * (1 + ACHIEVEMENT_BONUS * len(self.achievements))

    def digger_dps_each(self, did: str, fx: Effects | None = None) -> float:
        fx = fx or self.effects()
        d = C.DIGGERS_BY_ID[did]
        n = self.diggers.get(did, 0)
        return d.base_dps * milestone_mult(n) * fx.digger_mult * self.damage_mult()

    def dps(self) -> float:
        fx = self.effects()
        return sum(n * self.digger_dps_each(did, fx) for did, n in self.diggers.items() if n > 0)

    def tap_damage(self) -> float:
        fx = self.effects()
        base = pickaxe_base_damage(self.pickaxe_level) * fx.tap_mult * self.damage_mult()
        return base + self.dps() * fx.tap_dps_share

    def crit_chance(self) -> float:
        return min(self.effects().crit_chance, 0.95)

    @property
    def fever_active(self) -> bool:
        return self.time < self.fever_until

    @property
    def nugget_active(self) -> bool:
        return self.nugget_until > 0 and self.time < self.nugget_until

    def gold_rate(self, depth: int | None = None) -> float:
        """Сколько золота даёт 1 единица урона на этой глубине."""
        depth = self.depth if depth is None else depth
        rate = biome_gold_mult(depth) * self.effects().gold_mult
        if is_vein(depth):
            rate *= VEIN_GOLD_MULT
        if self.fever_active:
            rate *= FEVER_MULT
        return rate

    def income_per_sec(self) -> float:
        """Пассивный доход «на обычном блоке» — без учёта жил, чтобы цифра не прыгала."""
        rate = biome_gold_mult(self.depth) * self.effects().gold_mult
        if self.fever_active:
            rate *= FEVER_MULT
        return self.dps() * rate

    # ── урон и золото ──

    def _earn(self, amount: float) -> None:
        self.gold += amount
        self.stats.gold_total += amount

    def _break_block(self) -> None:
        if is_vein(self.depth):
            self.stats.veins += 1
        self.stats.blocks += 1
        self.depth += 1
        self.max_depth_run = max(self.max_depth_run, self.depth)
        self.stats.max_depth = max(self.stats.max_depth, self.depth)
        self.block_hp = block_max_hp(self.depth)

    def _apply_damage(self, dmg: float) -> tuple[float, int]:
        gold = 0.0
        broken = 0
        steps = 0
        while dmg > 0 and steps < MAX_BLOCKS_PER_HIT:
            take = min(dmg, self.block_hp)
            gold += take * self.gold_rate()
            self.block_hp -= take
            dmg -= take
            if self.block_hp <= 0:
                self._break_block()
                broken += 1
            steps += 1
        self._earn(gold)
        return gold, broken

    def tap(self) -> HitReport:
        self.stats.taps += 1
        crit = self.rng.random() < self.crit_chance()
        dmg = self.tap_damage() * (CRIT_MULT if crit else 1)
        if crit:
            self.stats.crits += 1
        gold, broken = self._apply_damage(dmg)
        return HitReport(dmg, gold, broken, crit, self.check_achievements())

    def tick(self, dt: float) -> TickReport:
        rep = TickReport()
        if dt <= 0:
            return rep
        was_fever = self.fever_active
        self.time += dt
        self.stats.play_time += dt
        rep.gold, rep.broken = self._apply_damage(self.dps() * dt)
        rep.fever_ended = was_fever and not self.fever_active

        if self.nugget_until > 0 and self.time >= self.nugget_until:
            self.nugget_until = 0.0
            self._schedule_nugget()
            rep.nugget_expired = True
        elif self.nugget_until <= 0 and self.time >= self.next_nugget_at:
            self.nugget_until = self.time + NUGGET_LIFETIME
            rep.nugget_spawned = True

        rep.new_achievements = self.check_achievements()
        return rep

    # ── самородки ──

    def _schedule_nugget(self) -> None:
        lo, hi = NUGGET_INTERVAL
        self.next_nugget_at = self.time + self.rng.uniform(lo, hi) / self.effects().nugget_freq

    def spawn_nugget_now(self) -> None:
        self.nugget_until = self.time + NUGGET_LIFETIME

    def claim_nugget(self) -> NuggetReport | None:
        if not self.nugget_active:
            return None
        self.nugget_until = 0.0
        self._schedule_nugget()
        self.stats.nuggets += 1
        if self.rng.random() < 0.5:
            self.fever_until = self.time + FEVER_DURATION
            rep = NuggetReport("fever")
        else:
            amount = max(
                self.income_per_sec() * NUGGET_GOLD_SECONDS,
                self.tap_damage() * self.gold_rate() * 30,
                25.0,
            )
            self._earn(amount)
            rep = NuggetReport("gold", amount)
        rep.new_achievements = self.check_achievements()
        return rep

    # ── покупки ──

    def digger_cost(self, did: str, n: int = 1) -> float:
        d = C.DIGGERS_BY_ID[did]
        return geometric_cost(d.base_cost, DIGGER_COST_GROWTH, self.diggers.get(did, 0), n)

    def digger_max(self, did: str) -> int:
        d = C.DIGGERS_BY_ID[did]
        return max_affordable(d.base_cost, DIGGER_COST_GROWTH, self.diggers.get(did, 0), self.gold)

    def buy_digger(self, did: str, n: int = 1) -> bool:
        cost = self.digger_cost(did, n)
        if n <= 0 or cost > self.gold:
            return False
        self.gold -= cost
        self.diggers[did] = self.diggers.get(did, 0) + n
        return True

    def pickaxe_cost(self, n: int = 1) -> float:
        return geometric_cost(PICKAXE_BASE_COST, PICKAXE_COST_GROWTH, self.pickaxe_level, n)

    def pickaxe_max(self) -> int:
        return max_affordable(PICKAXE_BASE_COST, PICKAXE_COST_GROWTH, self.pickaxe_level, self.gold)

    def buy_pickaxe(self, n: int = 1) -> bool:
        cost = self.pickaxe_cost(n)
        if n <= 0 or cost > self.gold:
            return False
        self.gold -= cost
        self.pickaxe_level += n
        return True

    def upgrade_unlocked(self, uid: str) -> bool:
        return self.stats.max_depth >= C.UPGRADES_BY_ID[uid].unlock_depth

    def buy_upgrade(self, uid: str) -> bool:
        u = C.UPGRADES_BY_ID[uid]
        if uid in self.upgrades or not self.upgrade_unlocked(uid) or u.cost > self.gold:
            return False
        self.gold -= u.cost
        self.upgrades.add(uid)
        return True

    # ── достижения ──

    def check_achievements(self) -> list[C.Achievement]:
        new = []
        for a in C.ACHIEVEMENTS:
            if a.id not in self.achievements and a.check(self):
                self.achievements.add(a.id)
                new.append(a)
        return new

    # ── перерождение ──

    def relics_on_prestige(self) -> int:
        return relics_for_depth(self.max_depth_run)

    def can_prestige(self) -> bool:
        return self.relics_on_prestige() > 0

    def prestige(self) -> int:
        gain = self.relics_on_prestige()
        if gain <= 0:
            return 0
        self.relics += gain
        self.stats.prestiges += 1
        self.gold = 0.0
        self.depth = 0
        self.block_hp = block_max_hp(0)
        self.max_depth_run = 0
        self.pickaxe_level = 0
        self.diggers = {}
        self.upgrades = set()
        self.fever_until = 0.0
        self.nugget_until = 0.0
        self.next_nugget_at = self.time + FIRST_NUGGET_AT
        self.check_achievements()
        return gain

    # ── для режима тестировщика ──

    def skip_meters(self, n: int) -> None:
        """Мгновенно пробить n блоков (без золота)."""
        for _ in range(max(0, n)):
            self._break_block()

    # ── офлайн ──

    def apply_offline(self, seconds: float) -> OfflineReport:
        seconds = max(0.0, min(float(seconds), OFFLINE_CAP))
        # бонусы не тикают, пока игрока нет
        self.fever_until = min(self.fever_until, self.time)
        if self.nugget_until > 0:
            self.nugget_until = 0.0
            self._schedule_nugget()
        depth_before = self.depth
        gold, _ = self._apply_damage(self.dps() * seconds * self.effects().offline_eff)
        return OfflineReport(seconds, gold, self.depth - depth_before)

    # ── сохранение ──

    def to_dict(self) -> dict:
        return {
            "v": SAVE_VERSION,
            "gold": self.gold,
            "depth": self.depth,
            "block_hp": self.block_hp,
            "max_depth_run": self.max_depth_run,
            "pickaxe_level": self.pickaxe_level,
            "diggers": {k: v for k, v in self.diggers.items() if v > 0},
            "upgrades": sorted(self.upgrades),
            "relics": self.relics,
            "achievements": sorted(self.achievements),
            "stats": asdict(self.stats),
            "time": self.time,
            "fever_until": self.fever_until,
            "nugget_until": self.nugget_until,
            "next_nugget_at": self.next_nugget_at,
        }

    @classmethod
    def from_dict(cls, data: dict, rng: random.Random | None = None) -> "Game":
        """Терпимо к мусору: неизвестные ключи игнорируются, битые значения → по умолчанию."""

        def num(key, default=0.0, src=data):
            v = src.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                return default
            return v

        g = cls(rng=rng or random.Random())
        g.gold = float(num("gold"))
        g.depth = int(num("depth", 0))
        g.max_depth_run = max(int(num("max_depth_run", 0)), g.depth)
        g.pickaxe_level = int(num("pickaxe_level", 0))
        g.relics = int(num("relics", 0))
        g.time = float(num("time"))
        g.fever_until = float(num("fever_until"))
        g.nugget_until = float(num("nugget_until"))
        g.next_nugget_at = float(num("next_nugget_at", g.time + FIRST_NUGGET_AT))

        raw_diggers = data.get("diggers") if isinstance(data.get("diggers"), dict) else {}
        g.diggers = {k: int(num(k, 0, raw_diggers)) for k in raw_diggers if k in C.DIGGERS_BY_ID}
        g.diggers = {k: v for k, v in g.diggers.items() if v > 0}
        g.upgrades = {u for u in data.get("upgrades", []) if u in C.UPGRADES_BY_ID}
        g.achievements = {a for a in data.get("achievements", []) if a in C.ACHIEVEMENTS_BY_ID}

        raw_stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        for f in fields(Stats):
            default = getattr(g.stats, f.name)
            setattr(g.stats, f.name, type(default)(num(f.name, default, raw_stats)))
        g.stats.max_depth = max(g.stats.max_depth, g.max_depth_run)

        full = block_max_hp(g.depth)
        hp = num("block_hp", full)
        g.block_hp = hp if 0 < hp <= full else full
        return g
