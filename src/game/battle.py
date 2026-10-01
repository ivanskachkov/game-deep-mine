"""Поход — боевой режим, который открывается на дне шахты. Чистый Python, без Flet.

Как это устроено:
  * 4 мира по 10 волн, в волне 10 мобов: девять обычных и один элитный (в десятой волне — босс);
  * внутри волны мобы почти равны по силе, каждая следующая волна сильнее в WAVE_GROWTH раз;
  * удар сильнее с каждым тапом подряд (комбо); пропущенный удар моба сбивает комбо;
  * перед ударом моб замахивается — нажал «Блок» вовремя, и урон режется, а комбо остаётся;
  * мобы бьют в общем ритме волны: убил одного — следующий не начинает отсчёт заново;
  * здоровье героя восстанавливается только между волнами; погиб — волна начинается заново;
  * с мобов падают осколки (на улучшение снаряжения), с элитных — изредка вещи следующего ранга;
  * пройденные волны понемногу приносят осколки сами, даже когда игра закрыта.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, fields

from . import content as C
from .costs import geometric_cost, max_affordable

WAVES = C.WAVES_PER_WORLD     # волн в мире
MOBS = 10                     # мобов в волне
TOTAL_WAVES = WAVES * len(C.WORLDS)
SLOTS = tuple(s.id for s in C.GEAR)
ARMOR = tuple(s.id for s in C.GEAR if s.stat == "hp")
MAX_TIER = len(C.WORLDS)      # ранг 0 — стартовые вещи, 1…4 выпадают в мирах

HERO_ATK = 10.0
HERO_HP = 100.0

COMBO_STEP = 0.1              # +10% урона за каждый удар подряд
COMBO_CAP = C.COMBO_CAP       # 20 ударов → ×3
COMBO_HOLD = 1.5              # секунд без ударов — и комбо сгорает
BLOCK_ABSORB = 0.6            # блок съедает 60% урона
BLOCK_COOLDOWN = 1.5          # нажал «Блок» не в замах — столько секунд он недоступен

WAVE_GROWTH = 1.3             # во сколько раз мобы сильнее с каждой волной
MOB_HP = 480.0
MOB_ATK = 1.4
MOB_STEP = 0.02               # внутри волны каждый следующий моб на 2% крепче

# Снаряжение. Вещь нового ранга вдвое сильнее, уровень улучшения даёт +12% за ×1.15 цены.
# Цена растёт чуть быстрее добычи, поэтому к концу мира фармить приходится дольше,
# а вещи следующего ранга снимают это отставание — миры занимают примерно поровну времени.
TIER_MULT = (1.0, 2.0, 4.0, 8.0, 16.0)
LEVEL_GROWTH = 1.12
LEVEL_CAP = 500
UPGRADE_COST = 75.0
UPGRADE_GROWTH = 1.15

SHARD_CHANCE = 0.3            # шанс осколков с обычного моба
SHARD_GROWTH = 1.27           # осколки дорожают почти так же быстро, как крепнут мобы
ELITE_SHARDS = 5              # элитный моб даёт их всегда и впятеро больше
FIRST_CLEAR_SHARDS = 5        # премия за волну, пройденную впервые: столько «порций»…
FIRST_CLEAR_FLAT = 40         # …и ещё столько осколков сверху — чтобы первые улучшения были сразу
GEAR_CHANCE = 0.10            # шанс вещи с элитного моба…
GEAR_PITY = 10                # …но не реже раза в 10 волн
DUPLICATE_SHARDS = 10         # комплект уже собран — вещь рассыпается на осколки

PASSIVE_SHARE = 0.10          # пройденные волны сами приносят 10% от ручного фарма
WAVE_TIME_REF = 120.0         # «ручной фарм» считаем как одну волну за две минуты
GEAR_MINE_BONUS = 0.05        # +5% урона в шахте за каждый ранг каждой вещи


# ───────────────────────── чистые формулы ─────────────────────────

def world_of(wave: int) -> int:
    return min(wave // WAVES, len(C.WORLDS) - 1)


def is_boss_wave(wave: int) -> bool:
    return wave % WAVES == WAVES - 1


def mob_at(wave: int, i: int) -> C.Mob:
    """Кто стоит i-м в волне (сквозной номер волны, с нуля)."""
    world = C.WORLDS[world_of(wave)]
    if i >= MOBS - 1:
        return world.boss if is_boss_wave(wave) else world.elite
    return world.mobs[(wave + i) % len(world.mobs)]


def wave_power(wave: int) -> float:
    return WAVE_GROWTH**wave


def mob_max_hp(wave: int, i: int) -> float:
    return MOB_HP * wave_power(wave) * mob_at(wave, i).hp * (1 + MOB_STEP * i)


def mob_attack(wave: int, i: int) -> float:
    return MOB_ATK * wave_power(wave) * mob_at(wave, i).atk


def shard_value(wave: int) -> int:
    """Сколько осколков в одной «порции» на этой волне."""
    return max(1, round(SHARD_GROWTH**wave))


def wave_shards(wave: int) -> float:
    """Сколько осколков в среднем приносит одно прохождение волны."""
    return shard_value(wave) * (ELITE_SHARDS + SHARD_CHANCE * (MOBS - 1))


def upgrade_cost(level: int, n: int = 1) -> int:
    """Цена n уровней улучшения подряд, если сейчас уровень level."""
    cost = geometric_cost(UPGRADE_COST, UPGRADE_GROWTH, level, n)
    return math.ceil(cost) if math.isfinite(cost) else cost


def combo_mult(combo: int) -> float:
    return 1 + COMBO_STEP * combo


# ───────────────────────── состояние ─────────────────────────

@dataclass
class BattleStats:
    kills: int = 0
    elites: int = 0          # элитных и боссов
    deaths: int = 0
    blocks: int = 0
    hits_taken: int = 0      # пропущенных ударов (без блока)
    waves: int = 0           # пройдено волн, считая повторы
    best_combo: int = 0
    shards_total: int = 0
    gear_drops: int = 0


@dataclass
class StrikeReport:
    """Что случилось после удара героя."""
    damage: float = 0.0
    combo: int = 0
    killed: C.Mob | None = None
    shards: int = 0
    gear: tuple | None = None        # (слот, ранг) — выпала вещь
    duplicate: bool = False          # вещь выпала, но комплект собран → осколки
    wave_cleared: bool = False
    first_clear: bool = False
    world_cleared: bool = False
    new_achievements: list = field(default_factory=list)


@dataclass
class EnemyReport:
    """Что успели сделать мобы за прошедшее время."""
    hits: int = 0                    # пропущено ударов
    blocked: int = 0
    damage: float = 0.0
    died: bool = False


@dataclass
class Battle:
    shards: int = 0
    cleared: int = 0                 # сколько волн пройдено подряд с самого начала
    wave: int = 0                    # выбранная волна (сквозной номер)
    tiers: dict = field(default_factory=lambda: dict.fromkeys(SLOTS, 0))
    levels: dict = field(default_factory=lambda: dict.fromkeys(SLOTS, 0))
    pity: int = 0                    # элитных мобов подряд без вещи
    passive_acc: float = 0.0
    stats: BattleStats = field(default_factory=BattleStats)
    hero_bonus: float = 1.0          # бонус от достижений шахты — выставляет Game
    rng: random.Random = field(default_factory=random.Random, repr=False, compare=False)

    # текущий бой — в сейв не идёт: после загрузки волна начинается заново
    t: float = 0.0
    mob: int = 0
    mob_hp: float = 0.0
    hero_hp: float = 0.0
    combo: int = 0
    combo_until: float = 0.0
    next_attack_at: float = 0.0
    blocking: bool = False
    block_ready_at: float = 0.0
    waiting: bool = True             # мобы не нападают, пока герой не ударит первым

    def __post_init__(self):
        self._start_wave()

    # ── герой ──

    def power(self, slot: str) -> float:
        return TIER_MULT[self.tiers[slot]] * LEVEL_GROWTH ** self.levels[slot]

    def attack(self) -> float:
        return HERO_ATK * self.power("weapon") * self.hero_bonus

    def max_hp(self) -> float:
        return HERO_HP * sum(self.power(s) for s in ARMOR) / len(ARMOR)

    def mine_bonus(self) -> float:
        """Прибавка к урону в шахте от собранных вещей."""
        return GEAR_MINE_BONUS * sum(self.tiers.values())

    # ── состояние боя ──

    @property
    def world(self) -> int:
        return world_of(self.wave)

    @property
    def frontier(self) -> int:
        """Самая дальняя волна, на которую можно выйти."""
        return min(self.cleared, TOTAL_WAVES - 1)

    @property
    def finished(self) -> bool:
        return self.cleared >= TOTAL_WAVES

    @property
    def current_mob(self) -> C.Mob:
        return mob_at(self.wave, self.mob)

    @property
    def combo_now(self) -> int:
        return self.combo if self.t <= self.combo_until else 0

    @property
    def time_to_attack(self) -> float:
        return math.inf if self.waiting else max(0.0, self.next_attack_at - self.t)

    @property
    def windup(self) -> bool:
        """Моб замахнулся — самое время жать «Блок»."""
        return self.time_to_attack <= C.WORLDS[self.world].windup

    @property
    def block_ready(self) -> bool:
        return self.t >= self.block_ready_at

    def _schedule_attack(self) -> None:
        self.next_attack_at = self.t + self.current_mob.speed
        self.blocking = False

    def _start_wave(self) -> None:
        self.mob = 0
        self.mob_hp = mob_max_hp(self.wave, 0)
        self.hero_hp = self.max_hp()
        self.combo = 0
        self.blocking = False
        self.block_ready_at = 0.0
        self.waiting = True

    def select(self, wave: int) -> bool:
        """Перейти на другую волну — пройденную или следующую за ними."""
        if not 0 <= wave <= self.frontier:
            return False
        self.wave = wave
        self._start_wave()
        return True

    # ── ход героя ──

    def hit(self) -> StrikeReport:
        rep = StrikeReport()
        if self.waiting:
            self.waiting = False
            self._schedule_attack()
        if self.t > self.combo_until:
            self.combo = 0
        rep.damage = self.attack() * combo_mult(self.combo)
        self.combo = min(self.combo + 1, COMBO_CAP)
        self.combo_until = self.t + COMBO_HOLD
        self.stats.best_combo = max(self.stats.best_combo, self.combo)
        rep.combo = self.combo
        self.mob_hp -= rep.damage
        if self.mob_hp <= 0:
            self._kill(rep)
        return rep

    def block(self) -> str:
        """«ok» — следующий удар будет отбит; «early» — замаха не было, комбо сбито;
        «cooldown» — блок ещё не готов; «idle» — бой не идёт."""
        if self.waiting:
            return "idle"
        if not self.block_ready:
            return "cooldown"
        if self.blocking or self.windup:
            self.blocking = True
            return "ok"
        self.combo = 0
        self.block_ready_at = self.t + BLOCK_COOLDOWN
        return "early"

    def _kill(self, rep: StrikeReport) -> None:
        wave, last = self.wave, self.mob >= MOBS - 1
        rep.killed = self.current_mob
        self.stats.kills += 1
        value = shard_value(wave)
        if last:
            self.stats.elites += 1
            rep.shards += ELITE_SHARDS * value
            self._roll_gear(rep, value)
        elif self.rng.random() < SHARD_CHANCE:
            rep.shards += value

        if last:
            rep.wave_cleared = True
            self.stats.waves += 1
            if wave == self.cleared:
                rep.first_clear = True
                rep.shards += FIRST_CLEAR_SHARDS * value + FIRST_CLEAR_FLAT
                self.cleared += 1
                rep.world_cleared = self.cleared % WAVES == 0
                self.wave = self.frontier        # дальше — следующая волна
            self._start_wave()
        else:
            self.mob += 1
            self.mob_hp = mob_max_hp(wave, self.mob)
            # ритм ударов общий на всю волну: новый моб «донашивает» замах предыдущего,
            # иначе быстрые убийства делали бы героя неуязвимым
            self.next_attack_at = min(self.next_attack_at, self.t + self.current_mob.speed)
        self._gain(rep.shards)

    def _roll_gear(self, rep: StrikeReport, value: int) -> None:
        """Элитный моб: вещь ранга этого мира с шансом GEAR_CHANCE, но не реже раза в GEAR_PITY волн.
        Босс при первой победе отдаёт вещь всегда."""
        tier = world_of(self.wave) + 1
        self.pity += 1
        first_boss = is_boss_wave(self.wave) and self.wave == self.cleared
        if not (first_boss or self.pity >= GEAR_PITY or self.rng.random() < GEAR_CHANCE):
            return
        self.pity = 0
        missing = [s for s in SLOTS if self.tiers[s] < tier]
        if not missing:
            rep.duplicate = True
            rep.shards += DUPLICATE_SHARDS * value
            return
        slot = missing[min(int(self.rng.random() * len(missing)), len(missing) - 1)]
        self.tiers[slot] = tier
        self.stats.gear_drops += 1
        rep.gear = (slot, tier)

    def _gain(self, shards: int) -> None:
        self.shards += shards
        self.stats.shards_total += shards

    # ── ход мобов ──

    def advance(self, dt: float) -> EnemyReport:
        """Прокрутить бой на dt секунд: мобы бьют по расписанию, блок срабатывает на ближайший удар."""
        rep = EnemyReport()
        if dt <= 0 or self.waiting:
            return rep
        end = self.t + dt
        while self.next_attack_at <= end:
            self.t = self.next_attack_at
            dmg = mob_attack(self.wave, self.mob)
            if self.blocking:
                dmg *= 1 - BLOCK_ABSORB
                rep.blocked += 1
                self.stats.blocks += 1
            else:
                rep.hits += 1
                self.stats.hits_taken += 1
                self.combo = 0
            rep.damage += dmg
            self.hero_hp -= dmg
            if self.hero_hp <= 0:
                rep.died = True
                self.stats.deaths += 1
                self._start_wave()
                return rep
            self._schedule_attack()
        self.t = end
        return rep

    # ── снаряжение ──

    def upgrade_price(self, slot: str, n: int = 1) -> float:
        if self.levels[slot] + n > LEVEL_CAP:
            return math.inf
        return upgrade_cost(self.levels[slot], n)

    def upgrade_max(self, slot: str) -> int:
        level = self.levels[slot]
        n = min(max_affordable(UPGRADE_COST, UPGRADE_GROWTH, level, self.shards), LEVEL_CAP - level)
        while n > 0 and upgrade_cost(level, n) > self.shards:  # цена округляется вверх
            n -= 1
        return n

    def upgrade(self, slot: str, n: int = 1) -> bool:
        cost = self.upgrade_price(slot, n)
        if n <= 0 or cost > self.shards:
            return False
        hp_before = self.max_hp()
        self.shards -= cost
        self.levels[slot] += n
        self.hero_hp += self.max_hp() - hp_before   # прибавка здоровья — сразу, даже посреди волны
        return True

    # ── пассивный фарм ──

    def passive_rate(self) -> float:
        """Осколков в секунду с пройденных волн — капает всегда, и в офлайне тоже."""
        if self.cleared <= 0:
            return 0.0
        return PASSIVE_SHARE * wave_shards(self.cleared - 1) / WAVE_TIME_REF

    def idle(self, seconds: float) -> int:
        self.passive_acc += self.passive_rate() * max(0.0, seconds)
        gained = int(self.passive_acc)
        self.passive_acc -= gained
        self._gain(gained)
        return gained

    # ── сохранение ──

    def to_dict(self) -> dict:
        return {
            "shards": self.shards,
            "cleared": self.cleared,
            "wave": self.wave,
            "tiers": dict(self.tiers),
            "levels": dict(self.levels),
            "pity": self.pity,
            "passive_acc": self.passive_acc,
            "stats": asdict(self.stats),
        }

    @classmethod
    def from_dict(cls, data, rng: random.Random | None = None) -> "Battle":
        """Терпимо к мусору, как и сейв шахты."""
        data = data if isinstance(data, dict) else {}

        def num(key, default=0, src=data):
            v = src.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                return default
            return v

        def per_slot(key: str, cap: int) -> dict:
            raw = data.get(key) if isinstance(data.get(key), dict) else {}
            return {s: min(int(num(s, 0, raw)), cap) for s in SLOTS}

        b = cls(rng=rng or random.Random())
        b.shards = int(num("shards"))
        b.cleared = min(int(num("cleared")), TOTAL_WAVES)
        b.tiers = per_slot("tiers", MAX_TIER)
        b.levels = per_slot("levels", LEVEL_CAP)
        b.pity = min(int(num("pity")), GEAR_PITY)
        b.passive_acc = min(float(num("passive_acc", 0.0)), 1.0)
        raw_stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        for f in fields(BattleStats):
            setattr(b.stats, f.name, int(num(f.name, 0, raw_stats)))
        b.wave = min(int(num("wave")), b.frontier)
        b._start_wave()
        return b
