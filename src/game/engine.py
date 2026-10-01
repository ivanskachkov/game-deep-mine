"""Игровая логика «Бездонной шахты». Чистый Python — никакого Flet, всё тестируется.

Экономика в двух словах:
  * урон (тапы + бригада) пробивает блоки; каждый блок = 1 метр глубины;
  * каждая единица урона приносит золото по курсу текущего слоя (глубже — дороже);
  * шахта — 10 слоёв по 25 м, в конце каждого страж: 30 с на победу, иначе он восстанавливается;
  * на 250 м дно — дальше копать некуда, зато открывается поход (см. battle.py);
  * копателей каждого вида — не больше 500, множители растут по лестнице ×2/×5/×25/×125;
  * перерождение сбрасывает забег, но даёт реликвии — их тратят на артефакты реликвария.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, fields

from . import content as C
from .battle import Battle
from .costs import geometric_cost, max_affordable

SAVE_VERSION = 4

MINE_DEPTH = 250              # дно шахты
LAYER_DEPTH = 25              # 10 слоёв по 25 м

BLOCK_BASE_HP = 5.0
BLOCK_HP_GROWTH = 1.16
HP_CAP = 1e300
VEIN_EVERY = 10
VEIN_HP_MULT = 2.5
VEIN_GOLD_MULT = 5.0
BIOME_GOLD_GROWTH = 1.3

GUARDIAN_EVERY = LAYER_DEPTH  # страж замыкает каждый слой
GUARDIAN_HP_MULT = 4.0
GUARDIAN_TIME = 30.0          # секунд, чтобы его свалить
GUARDIAN_GOLD_MULT = 3.0
GUARDIAN_TROPHY = 1           # реликвий за нового (самого глубокого) стража

# Копатели: пологий рост цены, чтобы 500 штук каждого вида было реально докачать.
# При росте 1.15 500-й хомяк стоил бы ~10³¹ золота, при 1.03 — 38M.
DIGGER_COST_GROWTH = 1.03
DIGGER_CAP = C.DIGGER_CAP     # 500
# Лестница множителей: (сколько штук, итоговый множитель).
MILESTONES = ((25, 2.0), (75, 5.0), (175, 25.0), (425, 125.0))

# Кирка: 100 уровней. Базовый урон важен только в начале, дальше кирка сильна тем,
# что каждый уровень добавляет к тапу 0.5% урона бригады — она не отстаёт никогда.
PICKAXE_CAP = 100
PICKAXE_BASE_COST = 10.0
PICKAXE_COST_GROWTH = 1.3
PICKAXE_MILESTONE_EVERY = 25  # базовый урон ×2 каждые 25 уровней
PICKAXE_DPS_SHARE = 0.005

BASE_CRIT_CHANCE = 0.04
CRIT_MULT = 5.0

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
MAX_OFFLINE_EFF = 1.0
MAX_BLOCKS_PER_HIT = 10_000

# Эффект одного уровня артефактов реликвария (см. content.PERKS)
PERK_POWER = 0.10             # +10% урона
PERK_VETERANS = 1.5           # бригада ×1.5
PERK_EYE = 0.03               # +3% крита
PERK_SLAYER_TIME = 5.0
PERK_SLAYER_DMG = 0.25
PERK_LUCK_FREQ = 0.25
PERK_LUCK_LIFE = 2.0
PERK_BLAZE = 10.0
PERK_NIGHT_EFF = 0.10
PERK_NIGHT_HOURS = 1.0
PERK_UNION = 0.95             # цена копателей ×0.95
PERK_ARCHEO = 0.15


# ───────────────────────── чистые формулы ─────────────────────────

def is_guardian(depth: int) -> bool:
    """Последний метр каждого слоя — страж: 30 секунд на победу, иначе он восстанавливается."""
    return depth < MINE_DEPTH and (depth + 1) % GUARDIAN_EVERY == 0


def is_vein(depth: int) -> bool:
    """Каждый 10-й метр — жила: крепче, но золота впятеро больше (если там не страж)."""
    return depth < MINE_DEPTH and (depth + 1) % VEIN_EVERY == 0 and not is_guardian(depth)


def block_max_hp(depth: int) -> float:
    try:
        hp = BLOCK_BASE_HP * BLOCK_HP_GROWTH**depth
    except OverflowError:
        hp = HP_CAP
    hp = min(hp, HP_CAP)
    if is_guardian(depth):
        return hp * GUARDIAN_HP_MULT
    return hp * VEIN_HP_MULT if is_vein(depth) else hp


def biome_index(depth: int) -> int:
    idx = 0
    for i, b in enumerate(C.BIOMES):
        if depth >= b.start:
            idx = i
    return idx


def biome_at(depth: int) -> C.Biome:
    return C.BIOMES[biome_index(depth)]


def guardian_at(depth: int) -> C.Guardian:
    return C.GUARDIANS[biome_index(depth)]


def next_biome_start(depth: int) -> int:
    """Где начинается следующий слой (или дно, если это последний)."""
    i = biome_index(depth) + 1
    return C.BIOMES[i].start if i < len(C.BIOMES) else MINE_DEPTH


def biome_gold_mult(depth: int) -> float:
    return BIOME_GOLD_GROWTH ** biome_index(depth)


def perk_cost(perk_id: str, level: int) -> int:
    """Цена следующего уровня артефакта, если сейчас уровень level."""
    p = C.PERKS_BY_ID[perk_id]
    return math.ceil(p.base_cost * p.cost_growth**level)


def relics_for_depth(depth: int) -> int:
    if depth < PRESTIGE_MIN_DEPTH:
        return 0
    return int((depth / PRESTIGE_DIVISOR) ** 2)


def pickaxe_base_damage(level: int) -> float:
    """Ур. 0 = 1 урон, +1 за уровень, ×2 каждые 25 уровней."""
    return (1 + level) * 2 ** (level // PICKAXE_MILESTONE_EVERY)


def milestone_mult(n: int) -> float:
    """Итоговый множитель для n копателей одного вида по лестнице MILESTONES."""
    m = 1.0
    for count, total in MILESTONES:
        if n >= count:
            m = total
    return m


def next_milestone(n: int) -> tuple[int, float] | None:
    """(сколько штук нужно для следующей ступени, итоговый множитель на ней) или None."""
    for count, total in MILESTONES:
        if n < count:
            return count, total
    return None


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
    guardians: int = 0
    guardians_failed: int = 0
    max_guardian: int = -1       # глубина самого глубокого поверженного стража
    relics_total: int = 0        # реликвий заработано за всё время
    bottoms: int = 0             # сколько раз добирались до дна


@dataclass
class Effects:
    tap_mult: float = 1.0
    tap_dps_share: float = 0.0
    crit_chance: float = BASE_CRIT_CHANCE
    gold_mult: float = 1.0
    digger_mult: float = 1.0
    digger_cost_mult: float = 1.0
    nugget_freq: float = 1.0
    nugget_life: float = NUGGET_LIFETIME
    fever_time: float = FEVER_DURATION
    offline_eff: float = BASE_OFFLINE_EFF
    offline_cap: float = OFFLINE_CAP
    autotap: int = 0                  # ударов киркой в секунду
    guardian_time: float = GUARDIAN_TIME
    guardian_dmg: float = 1.0
    relic_mult: float = 1.0


@dataclass
class HitReport:
    damage: float
    gold: float
    broken: int
    crit: bool = False
    guardians: int = 0                # сколько стражей повержено этим ударом
    trophies: int = 0                 # реликвий за новых стражей
    new_achievements: list = field(default_factory=list)
    bottom: bool = False              # этим ударом добрались до дна


@dataclass
class TickReport:
    gold: float = 0.0
    broken: int = 0
    auto_taps: int = 0
    guardians: int = 0
    trophies: int = 0
    guardian_failed: bool = False
    bottom: bool = False
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
    stuck_at_guardian: bool = False
    shards: int = 0                   # осколки с пройденных волн похода


@dataclass
class Game:
    gold: float = 0.0
    depth: int = 0
    block_hp: float = -1.0  # -1 = «возьми полное HP блока»
    max_depth_run: int = 0
    pickaxe_level: int = 0
    diggers: dict = field(default_factory=dict)
    upgrades: set = field(default_factory=set)
    relics: int = 0                   # неистраченные реликвии
    perks: dict = field(default_factory=dict)
    achievements: set = field(default_factory=set)
    stats: Stats = field(default_factory=Stats)
    time: float = 0.0
    fever_until: float = 0.0
    nugget_until: float = 0.0
    next_nugget_at: float = FIRST_NUGGET_AT
    guardian_left: float = 0.0        # секунд до восстановления стража
    autotap_acc: float = 0.0
    battle: Battle = field(default_factory=Battle)
    rng: random.Random = field(default_factory=random.Random, repr=False, compare=False)

    def __post_init__(self):
        self._fx_key = None
        self._fx = Effects()
        self.battle.rng = self.rng
        self._sync_battle()
        if self.block_hp <= 0:
            self.block_hp = block_max_hp(self.depth)
        if is_guardian(self.depth) and self.guardian_left <= 0:
            self.guardian_left = self.effects().guardian_time

    # ── производные величины ──

    def perk(self, pid: str) -> int:
        return self.perks.get(pid, 0)

    def effects(self) -> Effects:
        """Суммарные эффекты улучшений и артефактов. Кэш сбрасывается сам при любом их изменении."""
        key = (frozenset(self.upgrades), frozenset(self.perks.items()))
        if key != self._fx_key:
            self._fx_key = key
            self._fx = self._compute_effects()
        return self._fx

    def _compute_effects(self) -> Effects:
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
        p = self.perk
        fx.digger_mult *= PERK_VETERANS ** p("veterans")
        fx.digger_cost_mult = PERK_UNION ** p("union")
        fx.crit_chance += PERK_EYE * p("eye")
        fx.nugget_freq *= 1 + PERK_LUCK_FREQ * p("luck")
        fx.nugget_life += PERK_LUCK_LIFE * p("luck")
        fx.fever_time += PERK_BLAZE * p("blaze")
        fx.offline_eff = min(fx.offline_eff + PERK_NIGHT_EFF * p("night"), MAX_OFFLINE_EFF)
        fx.offline_cap += PERK_NIGHT_HOURS * 3600 * p("night")
        fx.autotap = p("autotap")
        fx.guardian_time += PERK_SLAYER_TIME * p("slayer")
        fx.guardian_dmg += PERK_SLAYER_DMG * p("slayer")
        fx.relic_mult += PERK_ARCHEO * p("archeo")
        return fx

    def achievement_mult(self) -> float:
        return 1 + ACHIEVEMENT_BONUS * len(self.achievements)

    def damage_mult(self) -> float:
        return (1 + PERK_POWER * self.perk("power")) * self.achievement_mult() * (1 + self.battle.mine_bonus())

    def digger_dps_each(self, did: str, fx: Effects | None = None, dm: float | None = None) -> float:
        fx = fx or self.effects()
        d = C.DIGGERS_BY_ID[did]
        n = self.diggers.get(did, 0)
        return d.base_dps * milestone_mult(n) * fx.digger_mult * (self.damage_mult() if dm is None else dm)

    def dps(self) -> float:
        fx, dm = self.effects(), self.damage_mult()
        return sum(n * self.digger_dps_each(did, fx, dm) for did, n in self.diggers.items() if n > 0)

    def tap_damage(self) -> float:
        fx = self.effects()
        base = pickaxe_base_damage(self.pickaxe_level) * fx.tap_mult * self.damage_mult()
        # каждый уровень кирки добавляет к тапу долю урона бригады — кирка не отстаёт
        return base + self.dps() * (fx.tap_dps_share + PICKAXE_DPS_SHARE * self.pickaxe_level)

    def crit_chance(self) -> float:
        return min(self.effects().crit_chance, 0.95)

    def autotap_dps(self) -> float:
        """Урон автокирки в секунду (в среднем, с учётом критов)."""
        rate = self.effects().autotap
        if not rate:
            return 0.0
        return rate * self.tap_damage() * (1 + self.crit_chance() * (CRIT_MULT - 1))

    def total_dps(self) -> float:
        return self.dps() + self.autotap_dps()

    @property
    def fever_active(self) -> bool:
        return self.time < self.fever_until

    @property
    def nugget_active(self) -> bool:
        return self.nugget_until > 0 and self.time < self.nugget_until

    @property
    def at_guardian(self) -> bool:
        return is_guardian(self.depth)

    @property
    def at_bottom(self) -> bool:
        return self.depth >= MINE_DEPTH

    @property
    def battle_unlocked(self) -> bool:
        """Поход открывается, когда шахта пройдена до дна, и остаётся открытым после перерождений."""
        return self.stats.max_depth >= MINE_DEPTH

    def gold_rate(self, depth: int | None = None) -> float:
        """Сколько золота даёт 1 единица урона на этой глубине."""
        depth = self.depth if depth is None else depth
        rate = biome_gold_mult(depth) * self.effects().gold_mult
        if is_guardian(depth):
            rate *= GUARDIAN_GOLD_MULT
        elif is_vein(depth):
            rate *= VEIN_GOLD_MULT
        if self.fever_active:
            rate *= FEVER_MULT
        return rate

    def income_per_sec(self) -> float:
        """Пассивный доход «на обычном блоке» — без жил и стражей, чтобы цифра не прыгала."""
        rate = biome_gold_mult(self.depth) * self.effects().gold_mult
        if self.fever_active:
            rate *= FEVER_MULT
        return self.total_dps() * rate

    # ── урон и золото ──

    def _earn(self, amount: float) -> None:
        self.gold += amount
        self.stats.gold_total += amount

    def _break_block(self, reward: bool = True) -> None:
        if reward and is_guardian(self.depth):
            self.stats.guardians += 1
            if self.depth > self.stats.max_guardian:
                self.stats.max_guardian = self.depth
                self.relics += GUARDIAN_TROPHY
                self.stats.relics_total += GUARDIAN_TROPHY
        if reward and is_vein(self.depth):
            self.stats.veins += 1
        self.stats.blocks += 1
        self.depth += 1
        if self.depth >= MINE_DEPTH:
            self.depth = MINE_DEPTH
            if reward:
                self.stats.bottoms += 1
        self.max_depth_run = max(self.max_depth_run, self.depth)
        self.stats.max_depth = max(self.stats.max_depth, self.depth)
        self.block_hp = block_max_hp(self.depth)
        self.guardian_left = self.effects().guardian_time if is_guardian(self.depth) else 0.0

    def _apply_damage(self, dmg: float, offline_dps: float | None = None) -> tuple[float, int]:
        """Раздаёт урон по блокам подряд. offline_dps — офлайн-режим: стража, которого
        такой урон не свалит за отведённое время, не пробиваем — только добываем с него золото."""
        gold = 0.0
        broken = 0
        steps = 0
        fx = self.effects()
        while dmg > 0 and steps < MAX_BLOCKS_PER_HIT:
            if self.at_bottom:  # на дне копать нечего — урон только добывает золото
                gold += dmg * self.gold_rate()
                break
            mult = fx.guardian_dmg if self.at_guardian else 1.0
            if (
                offline_dps is not None
                and self.at_guardian
                and offline_dps * mult * fx.guardian_time < block_max_hp(self.depth)
            ):
                gold += dmg * mult * self.gold_rate()
                break
            take = min(dmg * mult, self.block_hp)
            gold += take * self.gold_rate()
            self.block_hp -= take
            dmg -= take / mult
            if self.block_hp <= 0:
                self._break_block()
                broken += 1
            steps += 1
        self._earn(gold)
        return gold, broken

    def _guardian_counters(self) -> tuple[int, int]:
        return self.stats.guardians, self.stats.relics_total

    def tap(self) -> HitReport:
        self.stats.taps += 1
        crit = self.rng.random() < self.crit_chance()
        dmg = self.tap_damage() * (CRIT_MULT if crit else 1)
        if crit:
            self.stats.crits += 1
        g0, r0, was_bottom = *self._guardian_counters(), self.at_bottom
        gold, broken = self._apply_damage(dmg)
        g1, r1 = self._guardian_counters()
        return HitReport(dmg, gold, broken, crit, g1 - g0, r1 - r0, self.check_achievements(),
                         bottom=self.at_bottom and not was_bottom)

    def tick(self, dt: float) -> TickReport:
        rep = TickReport()
        if dt <= 0:
            return rep
        was_fever = self.fever_active
        self.time += dt
        self.stats.play_time += dt

        fx = self.effects()
        dmg = self.dps() * dt
        if fx.autotap:
            self.autotap_acc += fx.autotap * dt
            rep.auto_taps = int(self.autotap_acc + 1e-9)  # 10 × 0.1 в float — это 0.999…
            self.autotap_acc = max(0.0, self.autotap_acc - rep.auto_taps)
            dmg += rep.auto_taps * self.tap_damage() * (1 + self.crit_chance() * (CRIT_MULT - 1))

        guardian_before = self.depth if self.at_guardian else None
        g0, r0, was_bottom = *self._guardian_counters(), self.at_bottom
        rep.gold, rep.broken = self._apply_damage(dmg)
        g1, r1 = self._guardian_counters()
        rep.guardians, rep.trophies = g1 - g0, r1 - r0
        rep.bottom = self.at_bottom and not was_bottom

        # таймер стража идёт, только если мы стояли на нём и в начале тика
        if self.at_guardian and self.depth == guardian_before:
            self.guardian_left -= dt
            if self.guardian_left <= 0:
                self.block_hp = block_max_hp(self.depth)
                self.guardian_left = fx.guardian_time
                self.stats.guardians_failed += 1
                rep.guardian_failed = True

        rep.fever_ended = was_fever and not self.fever_active
        if self.nugget_until > 0 and self.time >= self.nugget_until:
            self.nugget_until = 0.0
            self._schedule_nugget()
            rep.nugget_expired = True
        elif self.nugget_until <= 0 and self.time >= self.next_nugget_at:
            self.nugget_until = self.time + fx.nugget_life
            rep.nugget_spawned = True

        self.battle.idle(dt)  # пройденные волны похода понемногу приносят осколки
        rep.new_achievements = self.check_achievements()
        return rep

    # ── самородки ──

    def _schedule_nugget(self) -> None:
        lo, hi = NUGGET_INTERVAL
        self.next_nugget_at = self.time + self.rng.uniform(lo, hi) / self.effects().nugget_freq

    def spawn_nugget_now(self) -> None:
        self.nugget_until = self.time + self.effects().nugget_life

    def claim_nugget(self) -> NuggetReport | None:
        if not self.nugget_active:
            return None
        self.nugget_until = 0.0
        self._schedule_nugget()
        self.stats.nuggets += 1
        if self.rng.random() < 0.5:
            self.fever_until = self.time + self.effects().fever_time
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

    # ── покупки за золото ──

    def _digger_base_cost(self, did: str) -> float:
        return C.DIGGERS_BY_ID[did].base_cost * self.effects().digger_cost_mult

    def digger_cost(self, did: str, n: int = 1) -> float:
        owned = self.diggers.get(did, 0)
        if owned + n > DIGGER_CAP:
            return math.inf  # больше потолка не продаётся
        return geometric_cost(self._digger_base_cost(did), DIGGER_COST_GROWTH, owned, n)

    def digger_max(self, did: str) -> int:
        owned = self.diggers.get(did, 0)
        can = max_affordable(self._digger_base_cost(did), DIGGER_COST_GROWTH, owned, self.gold)
        return min(can, DIGGER_CAP - owned)

    def digger_maxed(self, did: str) -> bool:
        return self.diggers.get(did, 0) >= DIGGER_CAP

    def buy_digger(self, did: str, n: int = 1) -> bool:
        cost = self.digger_cost(did, n)
        if n <= 0 or cost > self.gold:
            return False
        self.gold -= cost
        self.diggers[did] = self.diggers.get(did, 0) + n
        return True

    def pickaxe_cost(self, n: int = 1) -> float:
        if self.pickaxe_level + n > PICKAXE_CAP:
            return math.inf
        return geometric_cost(PICKAXE_BASE_COST, PICKAXE_COST_GROWTH, self.pickaxe_level, n)

    def pickaxe_max(self) -> int:
        can = max_affordable(PICKAXE_BASE_COST, PICKAXE_COST_GROWTH, self.pickaxe_level, self.gold)
        return min(can, PICKAXE_CAP - self.pickaxe_level)

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

    # ── реликварий (покупки за реликвии) ──

    def perk_maxed(self, pid: str) -> bool:
        p = C.PERKS_BY_ID[pid]
        return bool(p.max_level) and self.perk(pid) >= p.max_level

    def perk_price(self, pid: str) -> int:
        return perk_cost(pid, self.perk(pid))

    def buy_perk(self, pid: str) -> bool:
        if self.perk_maxed(pid) or self.perk_price(pid) > self.relics:
            return False
        self.relics -= self.perk_price(pid)
        self.perks[pid] = self.perk(pid) + 1
        if pid == "slayer" and self.at_guardian:
            self.guardian_left += PERK_SLAYER_TIME  # бонус работает и на текущего стража
        self.check_achievements()
        return True

    def perks_refund(self) -> int:
        return sum(perk_cost(pid, lvl) for pid, n in self.perks.items() for lvl in range(n))

    def respec(self) -> int:
        """Вернуть все реликвии из артефактов (бесплатно)."""
        refund = self.perks_refund()
        self.relics += refund
        self.perks = {}
        return refund

    def start_gold(self) -> float:
        lvl = self.perk("inherit")
        return 10.0 ** (lvl + 2) if lvl else 0.0

    # ── достижения ──

    def check_achievements(self) -> list[C.Achievement]:
        new = []
        for a in C.ACHIEVEMENTS:
            if a.id not in self.achievements and a.check(self):
                self.achievements.add(a.id)
                new.append(a)
        if new:
            self._sync_battle()
        return new

    # ── поход ──

    def _sync_battle(self) -> None:
        """Достижения усиливают и героя похода — тем же +1% за каждое."""
        self.battle.hero_bonus = self.achievement_mult()

    def battle_hit(self):
        rep = self.battle.hit()
        if rep.killed:
            rep.new_achievements = self.check_achievements()
        return rep

    def battle_upgrade(self, slot: str, n: int = 1) -> bool:
        return self.battle.upgrade(slot, n)

    # ── перерождение ──

    def relics_on_prestige(self) -> int:
        return int(relics_for_depth(self.max_depth_run) * self.effects().relic_mult)

    def can_prestige(self) -> bool:
        return self.relics_on_prestige() > 0

    def prestige(self) -> int:
        gain = self.relics_on_prestige()
        if gain <= 0:
            return 0
        self.relics += gain
        self.stats.relics_total += gain
        self.stats.prestiges += 1
        self.depth = 0
        self.block_hp = block_max_hp(0)
        self.guardian_left = 0.0
        self.max_depth_run = 0
        self.pickaxe_level = 0
        self.diggers = {}
        self.upgrades = set()
        self.gold = self.start_gold()
        self.fever_until = 0.0
        self.nugget_until = 0.0
        self.next_nugget_at = self.time + FIRST_NUGGET_AT
        self.autotap_acc = 0.0
        self.check_achievements()
        return gain

    # ── для режима тестировщика ──

    def skip_meters(self, n: int) -> None:
        """Мгновенно пробить n блоков (без золота, стражи не считаются побеждёнными)."""
        for _ in range(max(0, n)):
            if self.at_bottom:
                break
            self._break_block(reward=False)

    # ── офлайн ──

    def apply_offline(self, seconds: float) -> OfflineReport:
        fx = self.effects()
        seconds = max(0.0, min(float(seconds), fx.offline_cap))
        # бонусы не тикают, пока игрока нет
        self.fever_until = min(self.fever_until, self.time)
        if self.nugget_until > 0:
            self.nugget_until = 0.0
            self._schedule_nugget()
        depth_before = self.depth
        offline_dps = self.total_dps() * fx.offline_eff
        gold, _ = self._apply_damage(offline_dps * seconds, offline_dps=offline_dps)
        if self.at_guardian:
            self.guardian_left = fx.guardian_time
        shards = self.battle.idle(seconds)
        return OfflineReport(seconds, gold, self.depth - depth_before, self.at_guardian, shards)

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
            "perks": {k: v for k, v in self.perks.items() if v > 0},
            "achievements": sorted(self.achievements),
            "stats": asdict(self.stats),
            "time": self.time,
            "fever_until": self.fever_until,
            "nugget_until": self.nugget_until,
            "next_nugget_at": self.next_nugget_at,
            "guardian_left": self.guardian_left,
            "autotap_acc": self.autotap_acc,
            "battle": self.battle.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict, rng: random.Random | None = None) -> "Game":
        """Терпимо к мусору: неизвестные ключи игнорируются, битые значения → по умолчанию."""

        def num(key, default=0.0, src=data):
            v = src.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                return default
            return v

        def counts(key: str, known: dict) -> dict:
            raw = data.get(key) if isinstance(data.get(key), dict) else {}
            parsed = {k: int(num(k, 0, raw)) for k in raw if k in known}
            return {k: v for k, v in parsed.items() if v > 0}

        g = cls(rng=rng or random.Random())
        g.battle = Battle.from_dict(data.get("battle"), g.rng)  # до v4 похода не было — будет пустой
        g.gold = float(num("gold"))
        g.depth = min(int(num("depth", 0)), MINE_DEPTH)  # v2: шахта была бесконечной
        g.max_depth_run = max(int(num("max_depth_run", 0)), g.depth)
        g.pickaxe_level = min(int(num("pickaxe_level", 0)), PICKAXE_CAP)
        g.relics = int(num("relics", 0))
        g.time = float(num("time"))
        g.fever_until = float(num("fever_until"))
        g.nugget_until = float(num("nugget_until"))
        g.next_nugget_at = float(num("next_nugget_at", g.time + FIRST_NUGGET_AT))
        g.autotap_acc = min(float(num("autotap_acc")), 1.0)

        g.diggers = {k: min(v, DIGGER_CAP) for k, v in counts("diggers", C.DIGGERS_BY_ID).items()}
        g.perks = counts("perks", C.PERKS_BY_ID)
        for pid, lvl in list(g.perks.items()):
            cap = C.PERKS_BY_ID[pid].max_level
            if cap:
                g.perks[pid] = min(lvl, cap)
        g.upgrades = {u for u in data.get("upgrades", []) if u in C.UPGRADES_BY_ID}
        g.achievements = {a for a in data.get("achievements", []) if a in C.ACHIEVEMENTS_BY_ID}

        raw_stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        for f in fields(Stats):
            default = getattr(g.stats, f.name)
            if f.name == "max_guardian":
                v = raw_stats.get(f.name, default)
                ok = isinstance(v, int) and not isinstance(v, bool) and v >= -1
                g.stats.max_guardian = v if ok else default
                continue
            setattr(g.stats, f.name, type(default)(num(f.name, default, raw_stats)))
        g.max_depth_run = min(g.max_depth_run, MINE_DEPTH)
        g.stats.max_depth = min(max(g.stats.max_depth, g.max_depth_run), MINE_DEPTH)
        g.stats.max_guardian = min(g.stats.max_guardian, MINE_DEPTH - 1)

        # v1: реликвии давали +10% урона сами по себе → переносим их в «Силу предков»
        if int(num("v", 1)) < 2 and g.relics:
            g.perks["power"] = g.perk("power") + g.relics
            g.stats.relics_total = max(g.stats.relics_total, g.relics)
            g.relics = 0

        full = block_max_hp(g.depth)
        hp = num("block_hp", full)
        g.block_hp = hp if 0 < hp <= full else full
        guardian_time = g.effects().guardian_time
        left = float(num("guardian_left", guardian_time))
        g.guardian_left = min(left, guardian_time) if g.at_guardian and left > 0 else (
            guardian_time if g.at_guardian else 0.0
        )
        g._sync_battle()
        return g
