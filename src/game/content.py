"""Игровой контент: копатели, улучшения, биомы, достижения.

Всё здесь — данные. Баланс правится в этом файле и в константах engine.py.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Digger:
    id: str
    name: str
    emoji: str
    base_cost: float
    base_dps: float
    desc: str


@dataclass(frozen=True)
class Upgrade:
    id: str
    name: str
    emoji: str
    cost: float
    unlock_depth: int
    desc: str
    tap_mult: float = 1.0          # множитель базового урона тапа
    tap_dps_share: float = 0.0     # тап дополнительно наносит долю от DPS
    crit_chance: float = 0.0       # прибавка к шансу крита
    gold_mult: float = 1.0         # множитель золота
    digger_mult: float = 1.0       # множитель DPS всей бригады
    nugget_freq: float = 1.0       # во сколько раз чаще самородки
    offline_eff: float = 0.0       # прибавка к эффективности офлайн-дохода


@dataclass(frozen=True)
class Biome:
    start: int
    name: str
    emoji: str
    bg_top: str
    bg_bottom: str
    block: str


@dataclass(frozen=True)
class Perk:
    """Артефакт реликвария — покупается за реликвии, живёт между перерождениями."""
    id: str
    name: str
    emoji: str
    desc: str              # что даёт один уровень
    base_cost: int
    cost_growth: float
    max_level: int = 0     # 0 — без предела


@dataclass(frozen=True)
class Guardian:
    name: str
    emoji: str


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    emoji: str
    desc: str
    check: Callable = field(compare=False, repr=False)


DIGGERS: list[Digger] = [
    Digger("hamster", "Хомяк-копатель", "🐹", 15, 0.1, "Роет лапками. Медленно, но с душой."),
    Digger("gnome", "Гном-шахтёр", "🧔", 100, 1, "Работает за еду и песни."),
    Digger("ants", "Муравьиная бригада", "🐜", 1_100, 8, "Их тысячи. Профсоюза нет."),
    Digger("beaver", "Бобр-инженер", "🦫", 12_000, 47, "Ставит крепи. Иногда плотины."),
    Digger("excavator", "Экскаватор", "🚜", 130_000, 260, "Ковш размером с кухню."),
    Digger("blaster", "Подрывник", "🧨", 1.4e6, 1_400, "Бабах — и минус метр."),
    Digger("drill", "Буровая вышка", "🗼", 2e7, 7_800, "Сверлит днём и ночью."),
    Digger("robomole", "Робо-крот", "🤖", 3.3e8, 44_000, "Обучен на хомяках."),
    Digger("plasma", "Плазменный бур", "🌠", 5.1e9, 260_000, "Камень? Какой камень?"),
    Digger("worm", "Древний червь", "🪱", 7.5e10, 1.6e6, "Жил тут задолго до вас."),
    Digger("portal", "Портал в ядро", "🌀", 1e12, 1e7, "Копает сразу с двух сторон."),
    Digger("blackhole", "Чёрная дыра на поводке", "🪐", 1.4e13, 6.5e7, "Не кормить после полуночи."),
]
DIGGERS_BY_ID = {d.id: d for d in DIGGERS}

UPGRADES: list[Upgrade] = [
    Upgrade("helmet", "Каска с фонарём", "💡", 300, 0,
            "Тап дополнительно наносит 1% от DPS бригады", tap_dps_share=0.01),
    Upgrade("gloves", "Перчатки с шипами", "🧤", 2_500, 12,
            "Шанс крита +5%", crit_chance=0.05),
    Upgrade("steel_pick", "Стальная кирка", "🔨", 12_000, 25,
            "Базовый урон тапа ×2", tap_mult=2),
    Upgrade("map", "Карта жил", "📜", 60_000, 40,
            "Золото ×1.5", gold_mult=1.5),
    Upgrade("coffee", "Кофе для бригады", "☕", 1e6, 55,
            "DPS бригады ×1.5", digger_mult=1.5),
    Upgrade("scanner", "Сейсмосканер", "📡", 5e6, 70,
            "Самородки появляются в 2 раза чаще", nugget_freq=2),
    Upgrade("diamond_pick", "Алмазная кирка", "💎", 3e7, 90,
            "Тап ×3 и ещё +2% от DPS", tap_mult=3, tap_dps_share=0.02),
    Upgrade("rails", "Вагонетки на рельсах", "🚃", 2.5e8, 110,
            "Золото ×2", gold_mult=2),
    Upgrade("nightshift", "Ночная смена", "🌙", 1e9, 130,
            "Офлайн-доход 100% вместо 50%", offline_eff=0.5),
    Upgrade("energy", "Энергетик «Ядро»", "⚡", 2e10, 160,
            "DPS бригады ×2", digger_mult=2),
    Upgrade("magnet", "Гравимагнит", "🧲", 3e11, 190,
            "Золото ×2", gold_mult=2),
    Upgrade("abyss_pick", "Кирка Бездны", "🌌", 1e13, 240,
            "Тап ×5, +5% от DPS, крит +10%", tap_mult=5, tap_dps_share=0.05, crit_chance=0.10),
]
UPGRADES_BY_ID = {u.id: u for u in UPGRADES}

BIOMES: list[Biome] = [
    Biome(0, "Дёрн", "🌱", "#1f160e", "#3d2a18", "#6b4a2e"),
    Biome(25, "Глина", "🧱", "#2a160e", "#5c2f1c", "#9a5a3a"),
    Biome(60, "Камень", "🪨", "#15181c", "#343b44", "#6b7580"),
    Biome(100, "Угольный пласт", "⚫", "#0a0a0c", "#1f2125", "#3a3d42"),
    Biome(150, "Железная руда", "🔩", "#1f1512", "#4a2f27", "#8c5a48"),
    Biome(210, "Золотая жила", "🪙", "#1f1807", "#4d3d0f", "#b8912a"),
    Biome(280, "Кристальный грот", "💎", "#120a24", "#2f1a5c", "#7b4fd0"),
    Biome(360, "Магма", "🔥", "#1f0703", "#5c1608", "#c2410c"),
    Biome(450, "Мантия", "🌋", "#170810", "#420f2a", "#9d174d"),
    Biome(550, "Ядро Земли", "🌞", "#211400", "#6b4100", "#f59e0b"),
]
# После ядра — бесконечная Бездна: новый слой каждые ABYSS_STEP метров.
ABYSS_START = 660
ABYSS_STEP = 120
ABYSS_PALETTES = [
    ("#050510", "#101035", "#3b3bb3"),
    ("#050f0d", "#0f3530", "#14b8a6"),
    ("#10050f", "#351035", "#c026d3"),
    ("#0f0f05", "#35350f", "#a3a31a"),
]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

# Страж каждого слоя (по порядку BIOMES), в Бездне — ABYSS_GUARDIAN.
GUARDIANS: list[Guardian] = [
    Guardian("Жирный червь", "🐛"),
    Guardian("Глиняная жаба", "🐸"),
    Guardian("Каменный скорпион", "🦂"),
    Guardian("Угольная летучая мышь", "🦇"),
    Guardian("Ржавый таракан", "🪳"),
    Guardian("Золотой змей", "🐍"),
    Guardian("Кристальный краб", "🦀"),
    Guardian("Магмовый ящер", "🦎"),
    Guardian("Мантийный спрут", "🐙"),
    Guardian("Ядерный дракон", "🐉"),
]
ABYSS_GUARDIAN = Guardian("Тварь Бездны", "👾")

PERKS: list[Perk] = [
    Perk("power", "Сила предков", "💪", "+10% ко всему урону", 1, 1.0),
    Perk("autotap", "Автокирка", "⛏️", "+1 удар киркой в секунду сам по себе", 3, 2.0, 10),
    Perk("veterans", "Ветераны", "🏅", "Урон бригады ×1.5", 5, 2.5),
    Perk("inherit", "Наследство", "💰", "Новая шахта стартует с золотом: 1K, 10K, 100K…", 2, 2.0, 10),
    Perk("eye", "Глаз геолога", "🎯", "Шанс крита +3%", 2, 1.6, 10),
    Perk("slayer", "Охотник на стражей", "🏹", "Стражам: +5 с на таймер и +25% урона", 3, 2.0, 5),
    Perk("luck", "Удача старателя", "🍀", "Самородки на 25% чаще и живут на 2 с дольше", 3, 2.0, 5),
    Perk("blaze", "Вечный жар", "🔥", "Золотая лихорадка длится на 10 с дольше", 3, 2.0, 5),
    Perk("night", "Ночной сторож", "🌙", "Офлайн: +10% дохода и +1 ч к лимиту", 4, 2.0, 5),
    Perk("union", "Профсоюз", "🤝", "Копатели дешевле на 5%", 4, 1.8, 10),
    Perk("archeo", "Археолог", "🏺", "+15% реликвий за перерождение", 10, 1.8),
]
PERKS_BY_ID = {p.id: p for p in PERKS}


def _total_diggers(g) -> int:
    return sum(g.diggers.values())


ACHIEVEMENTS: list[Achievement] = [
    Achievement("tap_1", "Первый удар", "👊", "Сделать первый тап", lambda g: g.stats.taps >= 1),
    Achievement("tap_1k", "Мозоль", "🩹", "1 000 тапов", lambda g: g.stats.taps >= 1_000),
    Achievement("tap_10k", "Стальной палец", "🦾", "10 000 тапов", lambda g: g.stats.taps >= 10_000),
    Achievement("depth_10", "Под землёй", "👇", "Глубина 10 м", lambda g: g.stats.max_depth >= 10),
    Achievement("depth_50", "Глубже!", "🔽", "Глубина 50 м", lambda g: g.stats.max_depth >= 50),
    Achievement("depth_100", "Сотня", "💯", "Глубина 100 м", lambda g: g.stats.max_depth >= 100),
    Achievement("depth_250", "Где-то тут было золото", "🪙", "Глубина 250 м", lambda g: g.stats.max_depth >= 250),
    Achievement("depth_500", "Жарковато", "🥵", "Глубина 500 м", lambda g: g.stats.max_depth >= 500),
    Achievement("depth_1000", "Километр вниз", "🌌", "Глубина 1 000 м", lambda g: g.stats.max_depth >= 1000),
    Achievement("gold_1m", "Миллионер", "💰", "Заработать 1M золота за всё время", lambda g: g.stats.gold_total >= 1e6),
    Achievement("gold_1b", "Миллиардер", "🏦", "Заработать 1B золота", lambda g: g.stats.gold_total >= 1e9),
    Achievement("gold_1t", "Триллионер", "👑", "Заработать 1T золота", lambda g: g.stats.gold_total >= 1e12),
    Achievement("hire_10", "Бригадир", "📋", "10 копателей одновременно", lambda g: _total_diggers(g) >= 10),
    Achievement("hire_100", "Прораб", "👷", "100 копателей", lambda g: _total_diggers(g) >= 100),
    Achievement("hire_500", "Олигарх недр", "🎩", "500 копателей", lambda g: _total_diggers(g) >= 500),
    Achievement("all_types", "Полный штат", "🧑‍🤝‍🧑", "Нанять каждого копателя хотя бы раз",
                lambda g: all(g.diggers.get(d.id, 0) > 0 for d in DIGGERS)),
    Achievement("crit_100", "Точно в трещину", "🎯", "100 критических ударов", lambda g: g.stats.crits >= 100),
    Achievement("vein_50", "Жилокоп", "✨", "Разбить 50 жил", lambda g: g.stats.veins >= 50),
    Achievement("nugget_1", "Блестяшка", "🌟", "Поймать самородок", lambda g: g.stats.nuggets >= 1),
    Achievement("nugget_25", "Золотоискатель", "🧭", "Поймать 25 самородков", lambda g: g.stats.nuggets >= 25),
    Achievement("prestige_1", "Новая шахта", "🌀", "Переродиться", lambda g: g.stats.prestiges >= 1),
    Achievement("prestige_5", "Династия", "🏰", "Переродиться 5 раз", lambda g: g.stats.prestiges >= 5),
    Achievement("guardian_1", "Первая кровь", "🩸", "Победить стража", lambda g: g.stats.guardians >= 1),
    Achievement("guardian_25", "Гроза подземелья", "💀", "Победить 25 стражей", lambda g: g.stats.guardians >= 25),
    Achievement("perk_1", "Коллекционер", "🔮", "Купить артефакт за реликвии", lambda g: sum(g.perks.values()) >= 1),
    Achievement("perk_all", "Полный реликварий", "🔑", "Хотя бы по уровню каждого артефакта",
                lambda g: all(g.perks.get(p.id, 0) > 0 for p in PERKS)),
]
ACHIEVEMENTS_BY_ID = {a.id: a for a in ACHIEVEMENTS}
