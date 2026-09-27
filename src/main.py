"""Бездонная шахта — idle-кликер на Flet.

    flet run src/main.py          окно на компьютере
    flet run --web src/main.py    в браузере
    flet run --ios src/main.py    на iPhone через приложение Flet (сканируешь QR-код)

MINE_TESTER=1 — стартовать сразу с включённым режимом тестировщика.
"""

import asyncio
import json
import logging
import os
import random
import time

import flet as ft

from game import content as C
from game import engine as E
from game.engine import Game
from game.numfmt import fmt, fmt_duration

log = logging.getLogger("mine")

SAVE_KEY = "bottomless_mine.save"
TICK = 0.25                # шаг игрового цикла, с: 4 кадра/с хватает счётчикам, тапы рисуются сразу
TICK_IDLE = 1.0            # никто не трогает экран — перерисовываем раз в секунду, бережём батарею
IDLE_AFTER = 5.0           # через сколько секунд без касаний считаем, что игрок просто смотрит
PANEL_REFRESH = 1.0        # как часто обновлять магазин (только открытую вкладку), с
AUTOSAVE_EVERY = 10.0
OFFLINE_GAP = 5.0          # цикл «проспал» дольше → iOS усыплял приложение, считаем это офлайном
MIN_OFFLINE_REPORT = 60.0
MAX_FLOATERS = 14
SPEEDS = [1, 10, 100]

BG = "#0b0d10"
PANEL = "#12151a"
CARD = "#1a1e25"
CARD_HI = "#252b35"
TEXT = "#f1f3f5"
MUTED = "#8b93a1"
GOLD = "#fbbf24"
ACCENT = "#f59e0b"
VIOLET = "#a78bfa"
TESTER = "#38bdf8"

CENTER = ft.Alignment.CENTER
BOLD = ft.FontWeight.W_700
HEAVY = ft.FontWeight.W_800


def alpha(color: str, opacity: float) -> str:
    return ft.Colors.with_opacity(opacity, color)


def mult_str(x: float) -> str:
    return f"×{x:.1f}".replace(".0", "") if x < 10 else f"×{fmt(x)}"


def card(content: ft.Control, bgcolor: str = CARD, padding: int = 12) -> ft.Container:
    return ft.Container(content=content, bgcolor=bgcolor, padding=padding, border_radius=16)


def avatar(emoji: str, bgcolor: str = CARD_HI, tooltip: str | None = None) -> ft.Container:
    return ft.Container(
        content=ft.Text(emoji, size=26), width=48, height=48, border_radius=14,
        bgcolor=bgcolor, alignment=CENTER, tooltip=tooltip,
    )


class BuyButton:
    def __init__(self, on_click, width: int = 108, color: str = ACCENT):
        self.color = color
        self.top = ft.Text("", size=10, weight=ft.FontWeight.W_600, color=MUTED)
        self.label = ft.Text("", size=15, weight=HEAVY, color=MUTED)
        self.view = ft.Container(
            width=width, height=50, border_radius=12, bgcolor=CARD_HI, alignment=CENTER,
            on_click=on_click, ink=True,
            content=ft.Column(
                [self.top, self.label], spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

    def set(self, top: str, label: str, enabled: bool):
        self.top.value = top
        self.label.value = label
        self.view.bgcolor = self.color if enabled else CARD_HI
        self.label.color = "#1a1200" if enabled else MUTED
        self.top.color = alpha("#1a1200", 0.7) if enabled else MUTED


class ShopRow:
    """Строка магазина: аватар, название, описание, полоска до следующего порога и кнопка покупки."""

    def __init__(self, emoji: str, title: str, on_buy, tooltip: str | None = None, milestone: bool = True):
        self.title = ft.Text(title, size=15, weight=BOLD, color=TEXT)
        self.info = ft.Text("", size=12, color=MUTED)
        self.ms_bar = ft.ProgressBar(value=0, bar_height=4, color=VIOLET, bgcolor=CARD_HI, border_radius=2)
        self.ms_text = ft.Text("", size=10, color=MUTED)
        self.ms_row = ft.Row(
            [ft.Container(self.ms_bar, expand=True), self.ms_text],
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=milestone,
        )
        self.btn = BuyButton(on_buy)
        self.view = card(ft.Row(
            [
                avatar(emoji, tooltip=tooltip),
                ft.Column([self.title, self.info, self.ms_row], spacing=3, expand=True),
                self.btn.view,
            ],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))

    def set_milestone(self, n: int, every: int, factor: float, unit: str = "шт."):
        k = n % every
        self.ms_bar.value = k / every
        self.ms_text.value = f"ещё ×{fmt(factor)} через {every - k} {unit}"


class MineApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.prefs = ft.SharedPreferences()
        self.haptics = ft.HapticFeedback()
        self.clipboard = ft.Clipboard()
        self.game = Game()
        self.running = True
        self.paused = False            # игра свёрнута — цикл спит
        self.last_input = time.monotonic()
        self.tab = 0
        self.buy_mode = 1              # 1, 10 или 0 (= максимум)
        self.speed_idx = 0
        self.haptics_on = True
        self.tester = os.getenv("MINE_TESTER") == "1"
        self.mine_w, self.mine_h = 360.0, 300.0
        self.floaters = 0
        self.shown_biome = None
        self.shown_ach = -1
        self.last_save_at = 0.0
        self.tick_ms = 0.0
        self.fail_toast_depth = -1
        self.is_mobile = page.platform in (ft.PagePlatform.IOS, ft.PagePlatform.ANDROID) and not page.web
        self._build()

    # ═════════════════════════ разметка ═════════════════════════

    def _build(self):
        # ── шапка ──
        self.gold_text = ft.Text("0", size=32, weight=HEAVY, color=GOLD)
        self.income_text = ft.Text("+0/с", size=13, color=MUTED)
        self.depth_text = ft.Text("0 м", size=24, weight=HEAVY, color=TEXT)
        self.biome_text = ft.Text("", size=12, color=MUTED)
        self.header = ft.Container(
            bgcolor=PANEL,
            padding=ft.Padding.only(left=16, right=16, top=6, bottom=10),
            content=ft.Row(
                [
                    ft.Column([ft.Row([ft.Text("🪙", size=24), self.gold_text], spacing=6), self.income_text], spacing=0),
                    ft.Column([self.depth_text, self.biome_text], spacing=0,
                              horizontal_alignment=ft.CrossAxisAlignment.END),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        # ── шахта ──
        self.block_emoji = ft.Text("🌱", size=60)
        self.block_label = ft.Text("", size=11, weight=HEAVY, color=GOLD)
        self.block = ft.Container(
            width=150, height=150, border_radius=30, alignment=CENTER,
            border=ft.Border.all(3, alpha("#000000", 0.35)),
            shadow=ft.BoxShadow(blur_radius=28, color=alpha("#000000", 0.55), offset=ft.Offset(0, 10)),
            content=ft.Column([self.block_emoji, self.block_label], spacing=0, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              alignment=ft.MainAxisAlignment.CENTER),
            scale=1, animate_scale=ft.Animation(duration=70, curve=ft.AnimationCurve.EASE_OUT),
        )
        self.hp_bar = ft.ProgressBar(value=1, width=180, bar_height=10, color="#ef4444",
                                     bgcolor=alpha("#000000", 0.45), border_radius=5)
        self.hp_text = ft.Text("", size=11, color=alpha(TEXT, 0.75))
        self.next_text = ft.Text("", size=11, color=alpha(TEXT, 0.55))
        # таймер стража
        self.timer_bar = ft.ProgressBar(value=1, width=180, bar_height=6, color="#f59e0b",
                                        bgcolor=alpha("#000000", 0.45), border_radius=3)
        self.timer_text = ft.Text("", size=12, weight=HEAVY, color="#fbbf24")
        self.timer_box = ft.Column([self.timer_bar, self.timer_text], spacing=2, tight=True, visible=False,
                                   horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        self.tap_layer = ft.Container(
            left=0, top=0, right=0, bottom=0, alignment=CENTER,
            bgcolor=alpha("#000000", 0.01),  # чтобы тап ловился по всей площади шахты
            on_tap_down=self.on_mine_tap,
            content=ft.Column(
                [self.block, ft.Container(height=6), self.hp_bar, self.hp_text, self.timer_box, self.next_text],
                spacing=3, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )
        self.fx_layer = ft.Stack(controls=[])
        self.fever_chip = ft.Container(
            left=12, top=10, visible=False, border_radius=20,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            gradient=ft.LinearGradient(colors=["#f97316", "#facc15"]),
            content=ft.Text("", size=13, weight=HEAVY, color="#2a1400"),
        )
        self.speed_chip = ft.Container(
            right=12, top=10, visible=False, border_radius=20,
            padding=ft.Padding.symmetric(horizontal=10, vertical=5), bgcolor=alpha(TESTER, 0.25),
            content=ft.Text("", size=12, weight=BOLD, color=TESTER),
        )
        self.hint = ft.Container(
            left=0, right=0, bottom=10, alignment=CENTER,
            content=ft.Text("Тапай по шахте, чтобы копать ⛏", size=13, color=alpha(TEXT, 0.6)),
        )
        self.nugget = ft.Container(
            width=76, height=76, left=40, top=40, visible=False, alignment=CENTER,
            border_radius=38, bgcolor=alpha(GOLD, 0.18),
            border=ft.Border.all(2, alpha(GOLD, 0.8)),
            shadow=ft.BoxShadow(blur_radius=24, color=alpha(GOLD, 0.6)),
            content=ft.Text("🪙", size=40),
            scale=1, animate_scale=ft.Animation(duration=450, curve=ft.AnimationCurve.EASE_IN_OUT),
            on_click=self.on_nugget,
        )
        # Свой тост вместо SnackBar: SnackBar'ы во Flutter встают в очередь,
        # а в кликере событий много — новое сообщение должно сразу заменять старое.
        self.toast_text = ft.Text("", size=13, weight=BOLD, color=TEXT, text_align=ft.TextAlign.CENTER)
        self.toast_inner = ft.Container(
            content=self.toast_text, bgcolor=CARD_HI, border_radius=14,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            shadow=ft.BoxShadow(blur_radius=18, color=alpha("#000000", 0.5), offset=ft.Offset(0, 6)),
        )
        self.toast_box = ft.Container(
            content=self.toast_inner, alignment=CENTER, visible=False, opacity=0,
            animate_opacity=ft.Animation(duration=220, curve=ft.AnimationCurve.EASE_OUT),
        )
        self.toast_seq = 0
        self.stack = ft.Stack(
            expand=True,
            on_size_change=self.on_mine_size,
            controls=[
                self.tap_layer,
                ft.TransparentPointer(left=0, top=0, right=0, bottom=0, content=self.fx_layer),
                self.hint, self.fever_chip, self.speed_chip,
                ft.TransparentPointer(left=16, right=16, top=46, content=self.toast_box),
                self.nugget,
            ],
        )
        self.mine = ft.Container(expand=5, content=self.stack)

        # ── вкладки ──
        # вкладки в стиле iOS: иконка над подписью, точка — «есть что купить»
        self.tab_buttons, self.tab_dots = [], []
        for i, (icon, label) in enumerate([("⛏", "Кирка"), ("👷", "Бригада"), ("🏺", "Реликвии"), ("🏆", "Прочее")]):
            dot = ft.Container(width=7, height=7, border_radius=4, bgcolor=ACCENT, visible=False,
                               right=14, top=6)
            btn = ft.Container(
                expand=True, height=52, border_radius=12, ink=True,
                on_click=lambda e, i=i: self.select_tab(i),
                content=ft.Stack([
                    ft.Container(
                        left=0, right=0, top=0, bottom=0, alignment=CENTER,
                        content=ft.Column(
                            [ft.Text(icon, size=19), ft.Text(label, size=11, weight=BOLD, color=TEXT)],
                            spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                    dot,
                ]),
            )
            self.tab_buttons.append(btn)
            self.tab_dots.append(dot)
        tabs_bar = ft.Container(
            padding=ft.Padding.only(left=10, right=10, top=10),
            content=ft.Row(self.tab_buttons, spacing=6),
        )

        self.mode_buttons = {}
        for mode, label in [(1, "×1"), (10, "×10"), (0, "MAX")]:
            self.mode_buttons[mode] = ft.Container(
                content=ft.Text(label, size=12, weight=BOLD, color=TEXT), width=52, height=28,
                border_radius=8, alignment=CENTER, on_click=lambda e, m=mode: self.set_buy_mode(m),
            )
        self.mode_bar = ft.Container(
            padding=ft.Padding.only(left=14, right=14, top=8),
            content=ft.Row(
                [ft.Text("Покупать по:", size=12, color=MUTED), *self.mode_buttons.values()],
                spacing=6, alignment=ft.MainAxisAlignment.END,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        self.lists = [self._build_pick_tab(), self._build_crew_tab(), self._build_relic_tab(), self._build_more_tab()]
        self.panel_body = ft.Container(expand=True, content=self.lists[0])
        self.panel = ft.Container(
            expand=6, bgcolor=PANEL,
            border_radius=ft.BorderRadius.only(top_left=22, top_right=22),
            content=ft.Column([tabs_bar, self.mode_bar, self.panel_body], spacing=0),
        )

        self.root = ft.SafeArea(
            expand=True,
            content=ft.Column([self.header, self.mine, self.panel], spacing=0, expand=True),
        )

    def _list(self, controls: list[ft.Control]) -> ft.ListView:
        return ft.ListView(controls=controls, spacing=8, padding=ft.Padding.all(12), expand=True)

    def _build_pick_tab(self) -> ft.ListView:
        self.pick_row = ShopRow("⛏️", "Кирка", self.on_buy_pickaxe)
        self.upgrade_rows = {
            u.id: ShopRow(u.emoji, u.name, lambda e, uid=u.id: self.on_buy_upgrade(uid), milestone=False)
            for u in C.UPGRADES
        }
        for u in C.UPGRADES:
            self.upgrade_rows[u.id].info.value = u.desc
        self.upg_teaser = card(ft.Text("", size=13, color=MUTED), bgcolor=alpha(CARD, 0.5))
        self.upg_owned = ft.Text("", size=13, color=MUTED)
        return self._list([
            self.pick_row.view,
            ft.Container(ft.Text("Улучшения", size=13, weight=BOLD, color=MUTED), padding=ft.Padding.only(left=4, top=6)),
            *[r.view for r in self.upgrade_rows.values()],
            self.upg_teaser,
            ft.Container(self.upg_owned, padding=ft.Padding.only(left=4, top=4)),
        ])

    def _build_crew_tab(self) -> ft.ListView:
        self.digger_rows = {
            d.id: ShopRow(d.emoji, d.name, lambda e, did=d.id: self.on_buy_digger(did), tooltip=d.desc)
            for d in C.DIGGERS
        }
        self.crew_teaser = card(ft.Text("", size=13, color=MUTED), bgcolor=alpha(CARD, 0.5))
        return self._list([*[r.view for r in self.digger_rows.values()], self.crew_teaser])

    def _build_relic_tab(self) -> ft.ListView:
        # перерождение
        self.pr_info = ft.Text("", size=13, color=MUTED)
        self.pr_gain = ft.Text("", size=14, weight=BOLD, color=VIOLET)
        self.pr_btn = BuyButton(self.ask_prestige, width=150, color=VIOLET)
        prestige_card = card(ft.Column([
            ft.Row([
                avatar("🌀", "#2e1065"),
                ft.Column([ft.Text("Новая шахта", size=17, weight=HEAVY, color=TEXT),
                           ft.Text("Начать с нуля, но с реликвиями 🏺", size=12, color=MUTED)], spacing=0),
            ], spacing=12),
            self.pr_info, self.pr_gain,
            ft.Row([self.pr_btn.view], alignment=ft.MainAxisAlignment.END),
        ], spacing=8))

        # реликварий
        self.relic_balance = ft.Text("", size=22, weight=HEAVY, color=VIOLET)
        self.relic_hint = ft.Text("", size=12, color=MUTED)
        self.respec_btn = ft.Container(
            content=ft.Text("🔄 Вернуть реликвии", size=12, weight=BOLD, color=MUTED),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6), border_radius=8,
            bgcolor=CARD_HI, on_click=self.ask_respec, ink=True,
        )
        balance_card = card(ft.Row([
            ft.Column([ft.Text("Реликварий", size=15, weight=BOLD, color=TEXT), self.relic_balance, self.relic_hint],
                      spacing=2, expand=True),
            self.respec_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.START), bgcolor=alpha(VIOLET, 0.08))

        self.perk_rows = {}
        for p in C.PERKS:
            row = ShopRow(p.emoji, p.name, lambda e, pid=p.id: self.on_buy_perk(pid))
            row.btn.color = VIOLET
            self.perk_rows[p.id] = row
        return self._list([prestige_card, balance_card, *[r.view for r in self.perk_rows.values()]])

    def _build_more_tab(self) -> ft.ListView:
        # статистика
        self.stats_text = ft.Text("", size=13, color=MUTED)
        stats_card = card(ft.Column([ft.Text("📊 Статистика", size=15, weight=BOLD, color=TEXT), self.stats_text], spacing=6))

        # достижения
        self.ach_title = ft.Text("", size=15, weight=BOLD, color=TEXT)
        self.ach_rows = {}
        for a in C.ACHIEVEMENTS:
            self.ach_rows[a.id] = ft.Row([
                ft.Text(a.emoji, size=20),
                ft.Column([ft.Text(a.name, size=13, weight=BOLD, color=TEXT),
                           ft.Text(a.desc, size=11, color=MUTED)], spacing=0, expand=True),
            ], spacing=10, opacity=0.35)
        ach_card = card(ft.Column([self.ach_title, *self.ach_rows.values()], spacing=8))

        # настройки
        self.haptics_switch = ft.Switch(label="Вибрация при тапе", value=True, on_change=self.on_haptics_switch)
        self.tester_switch = ft.Switch(label="🧪 Режим тестировщика", value=self.tester, on_change=self.on_tester_switch)
        settings_card = card(ft.Column([
            ft.Text("🔧 Настройки", size=15, weight=BOLD, color=TEXT),
            self.haptics_switch, self.tester_switch,
        ], spacing=4))

        self.tester_card = self._build_tester_card()
        return self._list([self.tester_card, stats_card, ach_card, settings_card])

    def _build_tester_card(self) -> ft.Container:
        def label_text(label: str) -> ft.Text:
            return ft.Text(label, size=13, weight=ft.FontWeight.W_600, color=TEXT)

        def btn(label: str | ft.Text, handler):
            return ft.Container(
                content=label if isinstance(label, ft.Text) else label_text(label),
                padding=ft.Padding.symmetric(horizontal=12, vertical=9), border_radius=10,
                bgcolor=alpha(TESTER, 0.14), border=ft.Border.all(1, alpha(TESTER, 0.45)),
                on_click=handler, ink=True,
            )

        self.speed_btn_text = label_text("⏩ Время ×1")
        speed_btn = btn(self.speed_btn_text, self.t_speed)
        self.debug_text = ft.Text("", size=11, color=MUTED)
        run = self.page.run_task
        buttons = [
            btn("+1K 🪙", lambda e: self.t_gold(1e3)),
            btn("+1M 🪙", lambda e: self.t_gold(1e6)),
            btn("+1B 🪙", lambda e: self.t_gold(1e9)),
            btn("+1T 🪙", lambda e: self.t_gold(1e12)),
            btn("🪙 ×10", lambda e: self.t_gold(self.game.gold * 9)),
            btn("+10 м", lambda e: self.t_meters(10)),
            btn("+100 м", lambda e: self.t_meters(100)),
            btn("👹 К стражу", self.t_to_guardian),
            btn("🌟 Самородок", self.t_nugget),
            btn("🔥 Лихорадка", self.t_fever),
            btn("+10 🏺", self.t_relics),
            speed_btn,
            btn("💤 Офлайн 1 ч", lambda e: self.t_offline(3600)),
            btn("💤 Офлайн 8 ч", lambda e: self.t_offline(8 * 3600)),
            btn("💾 Сохранить", lambda e: run(self.save, True)),
            btn("📂 Загрузить", lambda e: run(self.load, True)),
            btn("📋 Копировать сейв", lambda e: run(self.t_copy_save)),
            btn("📥 Вставить сейв", lambda e: run(self.t_paste_save)),
            btn("❌ Сброс", self.t_ask_reset),
        ]
        return card(
            ft.Column([
                ft.Text("🧪 Режим тестировщика", size=15, weight=BOLD, color=TESTER),
                ft.Text("Читы и отладка. Прогресс сохраняется как обычно.", size=12, color=MUTED),
                ft.Row(buttons, wrap=True, spacing=8, run_spacing=8),
                self.debug_text,
            ], spacing=8),
            bgcolor=alpha(TESTER, 0.06),
        )

    # ═════════════════════════ запуск ═════════════════════════

    async def start(self):
        p = self.page
        p.title = "Бездонная шахта"
        p.theme_mode = ft.ThemeMode.DARK
        p.theme = ft.Theme(color_scheme_seed=ACCENT)
        p.dark_theme = ft.Theme(color_scheme_seed=ACCENT)
        p.bgcolor = BG
        p.padding = 0
        p.spacing = 0
        if not p.web and p.platform in (ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX):
            p.window.width, p.window.height = 420, 880
        p.on_app_lifecycle_state_change = self.on_lifecycle
        p.on_close = self.on_close
        p.add(self.root)

        await self.load()
        self.select_tab(0)
        self.set_buy_mode(1)
        self.refresh_all()
        p.update()
        p.run_task(self.game_loop)

    async def game_loop(self):
        last = time.monotonic()
        panel_t = save_t = pulse_t = 0.0
        while self.running:
            await asyncio.sleep(TICK if self.active else TICK_IDLE)
            if self.paused:
                # игра свёрнута: не считаем и не рисуем; `last` не трогаем,
                # чтобы при возврате пропущенное время засчиталось как офлайн
                continue
            now = time.monotonic()
            dt, last = now - last, now
            try:
                t0 = time.perf_counter()
                if dt > OFFLINE_GAP:
                    self.show_offline(self.game.apply_offline(dt))
                    self.refresh_all()
                else:
                    self.step(dt)
                self.refresh_hud()
                self.page.update(self.header, self.mine)  # одним сообщением

                panel_t += dt
                if panel_t >= PANEL_REFRESH:
                    panel_t = 0.0
                    self.refresh_panel()
                    self.panel.update()
                pulse_t += dt
                if pulse_t >= 0.45 and self.nugget.visible:
                    pulse_t = 0.0
                    self.nugget.scale = 1.18 if self.nugget.scale == 1 else 1
                    self.nugget.update()
                self.tick_ms = (time.perf_counter() - t0) * 1000
                save_t += dt
                if save_t >= AUTOSAVE_EVERY:
                    save_t = 0.0
                    await self.save()
            except Exception:
                if not self.running:
                    break
                log.exception("game loop error")

    def touch(self):
        """Игрок что-то сделал — снова 4 кадра в секунду."""
        self.last_input = time.monotonic()

    @property
    def active(self) -> bool:
        return time.monotonic() - self.last_input < IDLE_AFTER

    def step(self, dt: float):
        g = self.game
        rep = g.tick(dt * SPEEDS[self.speed_idx])
        if rep.broken:
            self.on_blocks_broken(rep.broken)
        if rep.guardians:
            self.on_guardians_defeated(rep.guardians, rep.trophies, rep.new_achievements)
            rep.new_achievements = []
        if rep.guardian_failed and self.fail_toast_depth != g.depth:
            self.fail_toast_depth = g.depth  # напоминаем один раз на стража, а не каждые 30 с
            self.toast(f"😈 {E.guardian_at(g.depth).name} устоял — усиль бригаду или тапай быстрее", "#ef4444")
        # Автокирку на экране не рисуем: постоянная анимация держит 60 кадров/с и сажает батарею.
        # Её видно в шапке («⛏2/с») и по тому, как тает HP блока.
        if rep.nugget_spawned:
            self.show_nugget()
        if rep.nugget_expired:
            self.nugget.visible = False
        if rep.fever_ended:
            self.toast("Золотая лихорадка закончилась")
        if rep.new_achievements:
            self.announce(rep.new_achievements)

    # ═════════════════════════ сохранение ═════════════════════════

    def _save_blob(self) -> str:
        return json.dumps({
            "game": self.game.to_dict(),
            "saved_at": time.time(),
            "haptics": self.haptics_on,
            "tester": self.tester,
        })

    async def save(self, notify: bool = False):
        try:
            await self.prefs.set(SAVE_KEY, self._save_blob())
            self.last_save_at = time.time()
            if notify:
                self.toast("💾 Сохранено")
        except Exception:
            log.exception("save failed")
            if notify:
                self.toast("❗ Не удалось сохранить")

    async def load(self, notify: bool = False):
        try:
            raw = await self.prefs.get(SAVE_KEY)
        except Exception:
            log.exception("load failed")
            raw = None
        if not raw:
            if notify:
                self.toast("Сохранения нет")
            return
        self.apply_save_blob(raw, offline=True)
        if notify:
            self.toast("📂 Загружено")
            self.refresh_all()
            self.page.update()

    def apply_save_blob(self, raw: str, offline: bool) -> bool:
        try:
            data = json.loads(raw)
            game = Game.from_dict(data.get("game", {}))
        except Exception:
            log.exception("bad save")
            return False
        self.game = game
        self.haptics_on = bool(data.get("haptics", True))
        self.tester = self.tester or bool(data.get("tester", False))
        self.nugget.visible = False
        self.hint.visible = game.stats.taps < 15
        self.shown_biome = None
        if offline:
            away = time.time() - float(data.get("saved_at", time.time()))
            if away >= MIN_OFFLINE_REPORT:
                self.show_offline(self.game.apply_offline(away))
        return True

    async def on_lifecycle(self, e: ft.AppLifecycleStateChangeEvent):
        s = ft.AppLifecycleState
        if e.state in (s.HIDE, s.PAUSE, s.INACTIVE):
            await self.save()
        # INACTIVE — это ещё не «свернули» (например, открыт пункт управления), игра идёт дальше
        if e.state in (s.HIDE, s.PAUSE):
            self.paused = True
        elif e.state in (s.SHOW, s.RESUME):
            self.paused = False

    def on_close(self, e):
        self.running = False

    # ═════════════════════════ ввод ═════════════════════════

    async def on_mine_tap(self, e: ft.TapEvent):
        self.touch()
        g = self.game
        rep = g.tap()
        pos = e.local_position
        x, y = (pos.x, pos.y) if pos else (self.mine_w / 2, self.mine_h / 2)
        if rep.crit:
            self.float_text(x, y, f"💥 {fmt(rep.gold)}", "#ff7a59", 26)
        else:
            self.float_text(x, y, f"+{fmt(rep.gold)}", GOLD, 20)
        if rep.broken:
            self.on_blocks_broken(rep.broken)
        if rep.guardians:
            self.on_guardians_defeated(rep.guardians, rep.trophies, rep.new_achievements)
            rep.new_achievements = []
        self.buzz("medium_impact" if (rep.crit or rep.broken) else "light_impact")
        if rep.new_achievements:
            self.announce(rep.new_achievements)
        if self.hint.visible and g.stats.taps >= 15:
            self.hint.visible = False
        self.refresh_hud()
        self.header.update()
        self.mine.update()
        self.page.run_task(self.bump_block)

    async def bump_block(self):
        self.block.scale = 0.92
        self.block.update()
        await asyncio.sleep(0.07)
        self.block.scale = 1
        self.block.update()

    def on_mine_size(self, e: ft.LayoutSizeChangeEvent):
        self.mine_w, self.mine_h = e.width, e.height

    def on_nugget(self, e):
        self.touch()
        rep = self.game.claim_nugget()
        self.nugget.visible = False
        if not rep:
            return
        self.buzz("heavy_impact")
        x, y = self.nugget.left + 38, self.nugget.top + 20
        if rep.kind == "gold":
            self.float_text(x, y, f"+{fmt(rep.gold)} 🪙", GOLD, 28)
            self.toast(f"🌟 Самородок! +{fmt(rep.gold)} золота", GOLD)
        else:
            self.float_text(x, y, "🔥 ×7", "#fb923c", 30)
            secs = int(self.game.effects().fever_time)
            self.toast(f"🔥 Золотая лихорадка! Золото ×{int(E.FEVER_MULT)} на {secs} с", "#fb923c")
        if rep.new_achievements:
            self.announce(rep.new_achievements)
        self.refresh_hud()
        self.page.update()

    def on_buy_digger(self, did: str):
        g = self.game
        n = self.buy_mode or g.digger_max(did)
        if n and g.buy_digger(did, n):
            self.buzz("selection_click")
            self.after_purchase()

    def on_buy_pickaxe(self, e):
        g = self.game
        n = self.buy_mode or g.pickaxe_max()
        if n and g.buy_pickaxe(n):
            self.buzz("selection_click")
            self.after_purchase()

    def on_buy_upgrade(self, uid: str):
        if self.game.buy_upgrade(uid):
            self.buzz("medium_impact")
            self.toast(f"{C.UPGRADES_BY_ID[uid].emoji} {C.UPGRADES_BY_ID[uid].name} — куплено!")
            self.after_purchase()

    def on_buy_perk(self, pid: str):
        if self.game.buy_perk(pid):
            p = C.PERKS_BY_ID[pid]
            self.buzz("medium_impact")
            self.toast(f"{p.emoji} {p.name}: ур. {self.game.perk(pid)}", VIOLET)
            self.after_purchase()

    def ask_respec(self, e):
        refund = self.game.perks_refund()
        if not refund:
            self.toast("Артефактов пока нет — возвращать нечего")
            self.page.update()
            return

        def do_respec(e):
            self.page.pop_dialog()
            self.game.respec()
            self.toast(f"🔄 Возвращено {refund} 🏺 — распредели заново", VIOLET)
            self.after_purchase()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Вернуть реликвии?"),
            content=ft.Text(f"Все артефакты обнулятся, а {refund} 🏺 вернутся — можно распределить их по-другому. Бесплатно."),
            actions=[
                ft.TextButton(content="Отмена", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton(content="Вернуть", bgcolor=VIOLET, on_click=do_respec),
            ],
        ))

    def on_guardians_defeated(self, killed: int, trophies: int, achievements: list = ()):
        self.buzz("heavy_impact")
        name = "Страж повержен!" if killed == 1 else f"Стражей повержено: {killed}"
        extra = f" +{trophies} 🏺 за нового стража" if trophies else ""
        ach = "".join(f"\n🏆 {a.emoji} {a.name} (+1% урона)" for a in achievements)
        self.toast(f"💥 {name}{extra}{ach}", "#f87171")
        self.float_text(self.mine_w / 2, self.mine_h / 2 - 40, "💥", TEXT, 34)

    def after_purchase(self):
        self.touch()
        self.refresh_hud()
        self.refresh_panel()
        self.page.update()

    def select_tab(self, i: int):
        self.touch()
        self.tab = i
        for k, b in enumerate(self.tab_buttons):
            b.bgcolor = CARD_HI if k == i else None
        self.panel_body.content = self.lists[i]
        self.mode_bar.visible = i in (0, 1)
        self.refresh_panel()
        self.page.update()

    def set_buy_mode(self, mode: int):
        self.touch()
        self.buy_mode = mode
        for m, b in self.mode_buttons.items():
            b.bgcolor = ACCENT if m == mode else CARD_HI
            b.content.color = "#1a1200" if m == mode else TEXT
        self.refresh_panel()
        self.page.update()

    def on_haptics_switch(self, e):
        self.haptics_on = bool(e.control.value)
        self.buzz("selection_click")

    def on_tester_switch(self, e):
        self.tester = bool(e.control.value)
        if not self.tester:
            self.speed_idx = 0
        self.refresh_all()
        self.page.update()

    # ═════════════════════════ перерождение ═════════════════════════

    def ask_prestige(self, e):
        g = self.game
        gain = g.relics_on_prestige()
        if gain <= 0:
            return
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("🌀 Новая шахта?"),
            content=ft.Text(
                f"Ты получишь +{gain} 🏺 реликвий — их можно потратить в реликварии на артефакты.\n\n"
                "Золото, глубина, кирка, бригада и улучшения обнулятся. "
                "Реликвии, артефакты, достижения и статистика останутся."
                + (f"\n\nНаследство: начнёшь с {fmt(g.start_gold())} 🪙" if g.start_gold() else "")
            ),
            actions=[
                ft.TextButton(content="Не сейчас", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton(content="Переродиться", bgcolor=VIOLET, on_click=self.do_prestige),
            ],
        )
        self.page.show_dialog(dlg)

    async def do_prestige(self, e):
        self.page.pop_dialog()
        gain = self.game.prestige()
        self.nugget.visible = False
        self.shown_biome = None
        self.buzz("heavy_impact")
        self.toast(f"🌀 Новая шахта! +{gain} 🏺", VIOLET)
        self.refresh_all()
        self.page.update()
        await self.save()

    # ═════════════════════════ режим тестировщика ═════════════════════════

    def _tester_done(self, msg: str | None = None):
        self.touch()
        if msg:
            self.toast(f"🧪 {msg}", TESTER)
        self.refresh_all()
        self.page.update()

    def t_gold(self, amount: float):
        self.game.gold += amount
        self._tester_done(f"+{fmt(amount)} золота")

    def t_meters(self, n: int):
        self.game.skip_meters(n)
        self._tester_done(f"+{n} м")

    def t_nugget(self, e):
        self.game.spawn_nugget_now()
        self.show_nugget()
        self._tester_done()

    def t_to_guardian(self, e):
        g = self.game
        g.skip_meters(E.GUARDIAN_EVERY - 1 - g.depth % E.GUARDIAN_EVERY or E.GUARDIAN_EVERY)
        self._tester_done(f"Страж: {E.guardian_at(g.depth).name}")

    def t_fever(self, e):
        self.game.fever_until = self.game.time + self.game.effects().fever_time
        self._tester_done("Лихорадка")

    def t_relics(self, e):
        self.game.relics += 10
        self._tester_done("+10 реликвий")

    def t_speed(self, e):
        self.speed_idx = (self.speed_idx + 1) % len(SPEEDS)
        self._tester_done(f"Скорость времени ×{SPEEDS[self.speed_idx]}")

    def t_offline(self, seconds: float):
        rep = self.game.apply_offline(seconds)
        self.show_offline(rep, force=True)
        self._tester_done()

    async def t_copy_save(self):
        await self.clipboard.set(self._save_blob())
        self.toast("📋 Сейв скопирован в буфер обмена", TESTER)
        self.page.update()

    async def t_paste_save(self):
        raw = await self.clipboard.get()
        if raw and self.apply_save_blob(raw, offline=False):
            await self.save()
            self._tester_done("Сейв загружен из буфера")
        else:
            self._tester_done("В буфере не сейв")

    def t_ask_reset(self, e):
        async def reset(e):
            self.page.pop_dialog()
            self.game = Game()
            self.nugget.visible = False
            self.hint.visible = True
            self.shown_biome = None
            await self.prefs.remove(SAVE_KEY)
            self._tester_done("Прогресс сброшен")

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Сбросить весь прогресс?"),
            content=ft.Text("Удалится всё, включая реликвии и достижения."),
            actions=[
                ft.TextButton(content="Отмена", on_click=lambda e: self.page.pop_dialog()),
                ft.FilledButton(content="Сбросить", bgcolor="#dc2626", on_click=reset),
            ],
        ))

    # ═════════════════════════ эффекты ═════════════════════════

    def buzz(self, kind: str):
        if not (self.haptics_on and self.is_mobile):
            return

        async def go():
            try:
                await getattr(self.haptics, kind)()
            except Exception:
                pass

        self.page.run_task(go)

    def float_text(self, x: float, y: float, text: str, color: str, size: int):
        if self.floaters >= MAX_FLOATERS:
            return
        self.page.run_task(self._floater, x + random.uniform(-14, 14), y, text, color, size)

    async def _floater(self, x, y, text, color, size):
        self.floaters += 1
        f = ft.Container(
            left=x - 80, top=y - size, width=160, alignment=CENTER,
            content=ft.Text(text, size=size, weight=HEAVY, color=color, no_wrap=True),
            animate_position=ft.Animation(duration=700, curve=ft.AnimationCurve.EASE_OUT),
            animate_opacity=ft.Animation(duration=700, curve=ft.AnimationCurve.EASE_IN),
        )
        try:
            self.fx_layer.controls.append(f)
            self.fx_layer.update()
            await asyncio.sleep(0.03)
            f.top = y - size - 90
            f.opacity = 0
            f.update()
            await asyncio.sleep(0.75)
            self.fx_layer.controls.remove(f)
            self.fx_layer.update()
        except Exception:
            pass
        finally:
            self.floaters -= 1

    def on_blocks_broken(self, n: int):
        if self.active:  # в простое без анимаций — бережём батарею
            self.float_text(self.mine_w / 2, self.mine_h / 2 - 90, f"+{n} м", TEXT, 16)
        b = E.biome_at(self.game.depth)
        if self.shown_biome is not None and b.name != self.shown_biome:
            self.toast(f"{b.emoji} Новый слой: {b.name}! Золото {mult_str(E.biome_gold_mult(self.game.depth))}", ACCENT)
            self.buzz("heavy_impact")

    def show_nugget(self):
        w, h = max(self.mine_w, 120), max(self.mine_h, 120)
        self.nugget.left = random.uniform(12, w - 88)
        self.nugget.top = random.uniform(40, h - 88)
        self.nugget.scale = 1
        self.nugget.visible = True
        self.buzz("selection_click")

    def announce(self, achievements: list):
        names = ", ".join(f"{a.emoji} {a.name}" for a in achievements)
        self.toast(f"🏆 Достижение: {names} (+{len(achievements)}% урона)", VIOLET)

    def toast(self, text: str, color: str | None = None):
        self.toast_seq += 1
        self.toast_text.value = text
        self.toast_text.color = "#0b0d10" if color else TEXT
        self.toast_inner.bgcolor = color or CARD_HI
        self.toast_box.visible = True
        self.toast_box.opacity = 1
        try:
            self.toast_box.update()
        except Exception:
            pass  # ещё не на странице — покажется со следующим обновлением
        self.page.run_task(self._hide_toast, self.toast_seq)

    async def _hide_toast(self, seq: int):
        await asyncio.sleep(2.2)
        if seq != self.toast_seq:
            return  # уже показан более свежий тост
        self.toast_box.opacity = 0
        self.toast_box.update()
        await asyncio.sleep(0.25)
        if seq == self.toast_seq:
            self.toast_box.visible = False
            self.toast_box.update()

    def show_offline(self, rep: E.OfflineReport, force: bool = False):
        if not force and (rep.seconds < MIN_OFFLINE_REPORT or rep.gold <= 0):
            return
        fx = self.game.effects()
        stuck = (
            f"\n\n😈 Бригада упёрлась в стража «{E.guardian_at(self.game.depth).name}» — "
            "дальше без тебя не пройти, но золото с него капало."
            if rep.stuck_at_guardian else ""
        )
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("С возвращением! ⛏"),
            content=ft.Text(
                f"Пока тебя не было {fmt_duration(rep.seconds)}, бригада накопала "
                f"{fmt(rep.gold)} 🪙 и прошла {rep.meters} м.{stuck}\n\n"
                f"Офлайн-доход: {int(fx.offline_eff * 100)}%, максимум {fx.offline_cap / 3600:g} ч."
            ),
            actions=[ft.FilledButton(content="Забрать", on_click=lambda e: self.page.pop_dialog())],
        ))

    # ═════════════════════════ обновление экрана ═════════════════════════

    def refresh_all(self):
        self.refresh_hud()
        self.refresh_panel()

    def refresh_hud(self):
        g = self.game
        self.gold_text.value = fmt(g.gold)
        auto = g.effects().autotap
        self.income_text.value = (
            f"+{fmt(g.income_per_sec())}/с · тап {fmt(g.tap_damage())}" + (f" · ⛏{auto}/с" if auto else "")
        )
        self.depth_text.value = f"{g.depth} м"
        b = E.biome_at(g.depth)
        self.biome_text.value = f"{b.emoji} {b.name} · золото {mult_str(E.biome_gold_mult(g.depth))}"

        if b.name != self.shown_biome:
            self.shown_biome = b.name
            self.mine.gradient = ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER, colors=[b.bg_top, b.bg_bottom]
            )
            self.block.bgcolor = b.block

        guard = E.is_guardian(g.depth)
        vein = E.is_vein(g.depth)
        if guard:
            mob = E.guardian_at(g.depth)
            self.block_emoji.value = mob.emoji
            self.block.border = ft.Border.all(4, "#ef4444")
            self.block_label.value = "👹 СТРАЖ"
            self.block_label.color = "#fca5a5"
            limit = g.effects().guardian_time
            self.timer_bar.value = max(0.0, min(1.0, g.guardian_left / limit))
            self.timer_bar.color = "#ef4444" if g.guardian_left < 10 else "#f59e0b"
            self.timer_text.value = f"⏳ {max(0.0, g.guardian_left):.0f} с · {mob.name}"
        else:
            self.block_emoji.value = b.emoji
            self.block.border = ft.Border.all(4 if vein else 3, GOLD if vein else alpha("#000000", 0.35))
            self.block_label.value = f"✨ ЖИЛА ×{int(E.VEIN_GOLD_MULT)}" if vein else ""
            self.block_label.color = GOLD
        self.timer_box.visible = guard
        full = E.block_max_hp(g.depth)
        self.hp_bar.value = max(0.0, min(1.0, g.block_hp / full))
        self.hp_text.value = f"{fmt(g.block_hp)} / {fmt(full)} HP"
        nxt = E.next_biome_start(g.depth)
        self.next_text.value = f"до слоя «{E.biome_at(nxt).name}»: {nxt - g.depth} м"

        self.fever_chip.visible = g.fever_active
        if g.fever_active:
            self.fever_chip.content.value = f"🔥 Лихорадка ×{int(E.FEVER_MULT)} · {int(g.fever_until - g.time) + 1} с"
        if not g.nugget_active:
            self.nugget.visible = False
        speed = SPEEDS[self.speed_idx]
        self.speed_chip.visible = self.tester and speed != 1
        self.speed_chip.content.value = f"⏩ ×{speed}"

    def digger_visible(self, i: int) -> bool:
        g = self.game
        prev = g.diggers.get(C.DIGGERS[i - 1].id, 0) if i else 1
        return i == 0 or prev > 0 or g.diggers.get(C.DIGGERS[i].id, 0) > 0

    def refresh_panel(self):
        """Точки «есть что купить» на вкладках + строки только открытой вкладки."""
        g = self.game
        pick_dot = g.pickaxe_cost() <= g.gold or any(
            g.upgrade_unlocked(u.id) and u.id not in g.upgrades and u.cost <= g.gold for u in C.UPGRADES
        )
        crew_dot = any(self.digger_visible(i) and g.digger_cost(d.id) <= g.gold for i, d in enumerate(C.DIGGERS))
        relic_dot = g.can_prestige() or any(
            not g.perk_maxed(p.id) and g.perk_price(p.id) <= g.relics for p in C.PERKS
        )
        for i, dot in enumerate((pick_dot, crew_dot, relic_dot)):
            self.tab_dots[i].visible = dot and self.tab != i

        [self.refresh_pick, self.refresh_crew, self.refresh_relics, self.refresh_more][self.tab]()

    def refresh_pick(self):
        g = self.game
        n = self.buy_mode or max(1, g.pickaxe_max())
        cost = g.pickaxe_cost(n)
        self.pick_row.title.value = f"Кирка · ур. {g.pickaxe_level}"
        self.pick_row.info.value = f"Тап: {fmt(g.tap_damage())} · крит {g.crit_chance():.0%} ×{int(E.CRIT_MULT)}"
        self.pick_row.set_milestone(g.pickaxe_level, E.PICKAXE_MILESTONE_EVERY, 2, "ур.")
        self.pick_row.btn.set(f"+{n} ур.", fmt(cost), cost <= g.gold)

        owned = []
        for u in C.UPGRADES:
            row = self.upgrade_rows[u.id]
            row.view.visible = g.upgrade_unlocked(u.id) and u.id not in g.upgrades
            if row.view.visible:
                row.btn.set("улучшить", fmt(u.cost), u.cost <= g.gold)
            if u.id in g.upgrades:
                owned.append(u.emoji)
        locked = [u for u in C.UPGRADES if not g.upgrade_unlocked(u.id)]
        self.upg_teaser.visible = bool(locked)
        if locked:
            self.upg_teaser.content.value = f"🔒 Следующее улучшение откроется на глубине {locked[0].unlock_depth} м"
        self.upg_owned.value = ("Куплено: " + " ".join(owned)) if owned else ""

    def refresh_crew(self):
        g = self.game
        mode = self.buy_mode
        teaser = None
        for i, d in enumerate(C.DIGGERS):
            row = self.digger_rows[d.id]
            count = g.diggers.get(d.id, 0)
            row.view.visible = self.digger_visible(i)
            if not row.view.visible:
                teaser = teaser or C.DIGGERS[i - 1]
                continue
            k = mode or max(1, g.digger_max(d.id))
            cost = g.digger_cost(d.id, k)
            each = g.digger_dps_each(d.id)
            bonus = E.milestone_mult(count)
            row.title.value = f"{d.name} · {count}" if count else d.name
            row.info.value = (
                f"{fmt(each)}/с за шт."
                + (f" · всего {fmt(each * count)}/с" if count else "")
                + (f" · бонус ×{fmt(bonus)}" if bonus > 1 else "")
            )
            _, factor = E.next_milestone(count)
            row.set_milestone(count, E.MILESTONE_EVERY, factor)
            row.btn.set(f"+{k}", fmt(cost), cost <= g.gold)
        self.crew_teaser.visible = teaser is not None
        if teaser:
            self.crew_teaser.content.value = f"❓ Новый копатель появится, когда наймёшь «{teaser.name}»"

    def perk_now(self, pid: str) -> str:
        """Текущий суммарный эффект артефакта — одной строкой."""
        g, lvl = self.game, self.game.perk(pid)
        return {
            "power": f"+{lvl * 10}% урона",
            "autotap": f"{lvl} удар/с",
            "veterans": f"бригада ×{fmt(E.PERK_VETERANS ** lvl)}",
            "inherit": f"старт с {fmt(g.start_gold())} 🪙",
            "eye": f"+{lvl * 3}% крита",
            "slayer": f"+{lvl * 5} с, урон +{lvl * 25}%",
            "luck": f"чаще на {lvl * 25}%, +{lvl * 2} с",
            "blaze": f"+{lvl * 10} с лихорадки",
            "night": f"+{lvl * 10}% и +{lvl} ч офлайн",
            "union": f"копатели −{100 - round(E.PERK_UNION ** lvl * 100)}%",
            "archeo": f"+{lvl * 15}% реликвий",
        }[pid]

    def refresh_relics(self):
        g = self.game
        self.relic_balance.value = f"{g.relics} 🏺"
        self.relic_hint.value = (
            f"Всего добыто: {g.stats.relics_total} · вложено: {g.perks_refund()}\n"
            "Реликвии дают стражи (новые) и перерождение"
        )
        for p in C.PERKS:
            row, lvl = self.perk_rows[p.id], g.perk(p.id)
            row.title.value = f"{p.name} · {lvl}" if lvl else p.name
            row.info.value = f"{p.desc} за уровень" + (f"\nСейчас: {self.perk_now(p.id)}" if lvl else "")
            row.ms_row.visible = bool(p.max_level)
            if p.max_level:
                row.ms_bar.value = lvl / p.max_level
                row.ms_text.value = f"ур. {lvl}/{p.max_level}"
            if g.perk_maxed(p.id):
                row.btn.set("куплено", "МАКС", False)
            else:
                price = g.perk_price(p.id)
                row.btn.set("улучшить", f"{price} 🏺", price <= g.relics)

        gain = g.relics_on_prestige()
        self.pr_info.value = (
            f"Рекорд этого забега: {g.max_depth_run} м\n"
            f"Реликвии за перерождение растут с глубиной рекорда"
        )
        if gain:
            next_depth = E.PRESTIGE_DIVISOR * (gain + 1) ** 0.5
            self.pr_gain.value = (f"Переродиться сейчас: +{gain} 🏺\n"
                                  f"Копнёшь до {int(next_depth) + 1} м — будет +{gain + 1}")
            self.pr_btn.set("переродиться", f"+{gain} 🏺", True)
        else:
            left = E.PRESTIGE_MIN_DEPTH - g.max_depth_run
            self.pr_gain.value = f"Откроется на {E.PRESTIGE_MIN_DEPTH} м (осталось {left} м)"
            self.pr_btn.set("недоступно", f"{E.PRESTIGE_MIN_DEPTH} м", False)

    def refresh_more(self):
        g = self.game
        s = g.stats
        self.stats_text.value = "\n".join([
            f"Рекорд глубины: {s.max_depth} м",
            f"Добыто золота всего: {fmt(s.gold_total)}",
            f"Тапов: {fmt(s.taps)} · критов: {fmt(s.crits)}",
            f"Блоков: {fmt(s.blocks)} · жил: {fmt(s.veins)}",
            f"Самородков: {s.nuggets} · перерождений: {s.prestiges}",
            f"Стражей повержено: {s.guardians} · устояли: {s.guardians_failed}",
            f"Реликвий добыто: {s.relics_total}",
            f"Время в игре: {fmt_duration(s.play_time)}",
            f"Урон бригады: {fmt(g.dps())}/с · множитель урона ×{g.damage_mult():.2f}",
        ])

        if len(g.achievements) != self.shown_ach:
            self.shown_ach = len(g.achievements)
            self.ach_title.value = f"🏆 Достижения {len(g.achievements)}/{len(C.ACHIEVEMENTS)} (+{len(g.achievements)}% урона)"
            for aid, row in self.ach_rows.items():
                row.opacity = 1 if aid in g.achievements else 0.35

        self.haptics_switch.value = self.haptics_on
        self.tester_switch.value = self.tester
        self.tester_card.visible = self.tester
        if self.tester:
            self.speed_btn_text.value = f"⏩ Время ×{SPEEDS[self.speed_idx]}"
            saved = f"{int(time.time() - self.last_save_at)} с назад" if self.last_save_at else "ещё нет"
            self.debug_text.value = (
                f"platform={self.page.platform.value if self.page.platform else '?'} web={self.page.web}\n"
                f"tick={self.tick_ms:.1f} мс · floaters={self.floaters}\n"
                f"сейв: {len(self._save_blob()) / 1024:.1f} КБ, {saved}\n"
                f"game.time={g.time:.0f} с · след. самородок через {max(0, g.next_nugget_at - g.time):.0f} с\n"
                f"block_hp={g.block_hp:.4g} · dps={g.dps():.4g} · tap={g.tap_damage():.4g}"
            )


async def main(page: ft.Page):
    logging.basicConfig(level=logging.INFO)
    app = MineApp(page)
    await app.start()


if __name__ == "__main__":
    ft.run(main)
