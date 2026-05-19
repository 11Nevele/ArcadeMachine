#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys

try:
    import pygame
except ImportError as exc:  # pragma: no cover - exercised only when pygame is absent
    pygame = None
    PYGAME_IMPORT_ERROR = exc
else:
    PYGAME_IMPORT_ERROR = None

from config import ConfigError, LauncherConfig, load_config, prepare_runtime_paths
from controller_bridge import ControllerBridgeError, ControllerBridgeManager
from game_library import GameEntry, LibraryScanResult, discover_games


HEADER_HEIGHT = 96
FOOTER_HEIGHT = 64
PANEL_RADIUS = 26

BG_TOP = (12, 19, 34)
BG_BOTTOM = (7, 10, 19)
PANEL_COLOR = (19, 30, 49)
PANEL_ALT = (24, 36, 59)
TEXT_PRIMARY = (241, 244, 249)
TEXT_MUTED = (150, 168, 194)
ACCENT = (255, 142, 82)
ACCENT_SOFT = (255, 187, 145)
SUCCESS = (106, 214, 176)
WARNING = (255, 204, 110)
OUTLINE = (63, 95, 136)


@dataclass(slots=True)
class LayoutMetrics:
    header_rect: "pygame.Rect"
    grid_rect: "pygame.Rect"
    detail_rect: "pygame.Rect"
    footer_rect: "pygame.Rect"
    columns: int
    card_width: int
    card_height: int
    gap: int
    visible_rows: int


class ArcadeLauncherApp:
    def __init__(self, config: LauncherConfig, bridge_manager: ControllerBridgeManager):
        if pygame is None:
            raise RuntimeError("pygame is required to run the launcher.")

        self.config = config
        self.bridge_manager = bridge_manager
        self.selected_index = 0
        self.scroll_row = 0
        self.status_message = "Use WASD or the arrow keys to browse the library."
        self.running = True
        self.thumbnail_cache: dict[tuple[str, tuple[int, int]], "pygame.Surface"] = {}
        self.scan_result = LibraryScanResult(games=[], warnings=[])

        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption(self.config.window_title)
        pygame.mouse.set_visible(self.config.show_mouse_cursor)

        self.clock = pygame.time.Clock()
        self.title_font = self._build_font(44, bold=True)
        self.section_font = self._build_font(24, bold=True)
        self.card_font = self._build_font(22, bold=True)
        self.body_font = self._build_font(20)
        self.small_font = self._build_font(16)
        self.badge_font = self._build_font(15, bold=True)

        self.screen: "pygame.Surface"
        self.layout: LayoutMetrics
        self.background_surface: "pygame.Surface"
        self._create_display()
        self.refresh_library(keep_message=False)

    @property
    def selected_game(self) -> GameEntry | None:
        if not self.scan_result.games:
            return None
        return self.scan_result.games[self.selected_index]

    def refresh_library(self, keep_message: bool = True) -> None:
        self.scan_result = discover_games(self.config.library_root)
        self.thumbnail_cache.clear()
        if self.scan_result.games:
            self.selected_index = min(self.selected_index, len(self.scan_result.games) - 1)
        else:
            self.selected_index = 0
        self._ensure_selection_visible()

        if not keep_message:
            if self.scan_result.warnings:
                self.status_message = self.scan_result.warnings[0]
            elif self.scan_result.games:
                self.status_message = f"Loaded {len(self.scan_result.games)} game(s) from {self.config.library_root.name}."
            else:
                self.status_message = "Add game folders under the library root to populate the menu."

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                self._handle_event(event)

            self._draw()
            pygame.display.flip()
            self.clock.tick(60)

    def _handle_event(self, event: "pygame.event.Event") -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return

        if event.type == pygame.VIDEORESIZE and not self.config.fullscreen:
            self._create_display((max(960, event.w), max(540, event.h)))
            return

        if event.type != pygame.KEYDOWN:
            return

        if event.key in (pygame.K_LEFT, pygame.K_a):
            self._move_selection(-1)
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            self._move_selection(1)
        elif event.key in (pygame.K_UP, pygame.K_w):
            self._move_selection(-self.layout.columns)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self._move_selection(self.layout.columns)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self._launch_selected_game()
        elif event.key == pygame.K_F5:
            self.refresh_library(keep_message=False)
        elif event.key == pygame.K_ESCAPE:
            self.running = False

    def _move_selection(self, delta: int) -> None:
        if not self.scan_result.games:
            return

        new_index = max(0, min(len(self.scan_result.games) - 1, self.selected_index + delta))
        if new_index != self.selected_index:
            self.selected_index = new_index
            self._ensure_selection_visible()
            current = self.selected_game
            if current is not None:
                self.status_message = f"Selected {current.title}."

    def _ensure_selection_visible(self) -> None:
        if not self.scan_result.games:
            self.scroll_row = 0
            return

        selected_row = self.selected_index // self.layout.columns
        if selected_row < self.scroll_row:
            self.scroll_row = selected_row
        elif selected_row >= self.scroll_row + self.layout.visible_rows:
            self.scroll_row = selected_row - self.layout.visible_rows + 1

    def _launch_selected_game(self) -> None:
        game = self.selected_game
        if game is None:
            self.status_message = "No games are available yet. Add a game folder to the library root."
            return

        try:
            command = _build_java_command(self.config.java_command, game.jar_path, self.config.is_windows)
        except ValueError as exc:
            self.status_message = str(exc)
            return

        self.status_message = f"Launching {game.title}..."
        self._draw()
        pygame.display.flip()

        mixer_state = self._suspend_audio()
        debug_log_path = self.config.project_root / "logs" / "game_launch.log"

        with debug_log_path.open("a", encoding="utf-8") as debug_log:
            print("\n===== GAME LAUNCH DEBUG =====", file=debug_log)
            print(f"game={game.title}", file=debug_log)
            print(f"config.java_command={self.config.java_command!r}", file=debug_log)
            print(f"command={command!r}", file=debug_log)
            print(f"cwd={str(game.folder)!r}", file=debug_log)
            print(f"jar_path={str(game.jar_path)!r}", file=debug_log)
            print(f"PATH={os.environ.get('PATH')}", file=debug_log)
            print(f"USER={os.environ.get('USER')}", file=debug_log)
            print(f"HOME={os.environ.get('HOME')}", file=debug_log)
            print(f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR')}", file=debug_log)
            print(f"PULSE_SERVER={os.environ.get('PULSE_SERVER')}", file=debug_log)

            java_version = subprocess.run(
                [*command[:-2], "-version"],
                capture_output=True,
                text=True,
                cwd=str(game.folder),
            )
            print(f"java -version exit={java_version.returncode}", file=debug_log)
            print("java -version stderr:", file=debug_log)
            print(java_version.stderr, file=debug_log)
            print("java -version stdout:", file=debug_log)
            print(java_version.stdout, file=debug_log)
            debug_log.flush()

        try:
            process = subprocess.Popen(command, cwd=str(game.folder))
        except FileNotFoundError:
            self._restore_audio(mixer_state)
            self.status_message = (
                "Java was not found. Update java_command in config.json or install Java on this machine."
            )
            return
        except OSError as exc:
            self._restore_audio(mixer_state)
            self.status_message = f"Failed to launch {game.title}: {exc}"
            return

        pygame.display.iconify()

        try:
            while process.poll() is None and self.running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        process.terminate()
                        self.running = False
                self.clock.tick(10)

            exit_code = process.poll()
            if exit_code is None:
                exit_code = process.wait()
        finally:
            self._restore_audio(mixer_state)

        self._create_display()
        self.refresh_library(keep_message=True)

        if exit_code == 0:
            self.status_message = f"{game.title} closed. Returning to the selector."
        else:
            self.status_message = f"{game.title} exited with code {exit_code}."

    def _create_display(self, size: tuple[int, int] | None = None) -> None:
        flags = pygame.DOUBLEBUF
        if self.config.fullscreen:
            flags |= pygame.FULLSCREEN
            screen_size = self.config.window_size
        else:
            flags |= pygame.RESIZABLE
            screen_size = size or self.config.window_size

        self.screen = pygame.display.set_mode(screen_size, flags)
        self.layout = self._build_layout(self.screen.get_size())
        self.background_surface = self._build_background(self.screen.get_size())

    def _build_layout(self, screen_size: tuple[int, int]) -> LayoutMetrics:
        width, height = screen_size
        padding = 24
        gap = 20

        header_rect = pygame.Rect(padding, padding, width - (padding * 2), HEADER_HEIGHT)
        footer_rect = pygame.Rect(
            padding,
            height - FOOTER_HEIGHT - padding,
            width - (padding * 2),
            FOOTER_HEIGHT,
        )

        content_top = header_rect.bottom + 16
        content_height = footer_rect.top - content_top - 16
        grid_width = int(width * 0.58)
        grid_rect = pygame.Rect(padding, content_top, grid_width - (padding // 2), content_height)
        detail_rect = pygame.Rect(grid_rect.right + padding, content_top, width - grid_rect.right - (padding * 2), content_height)

        minimum_card_width = 172
        max_columns_that_fit = max(1, (grid_rect.width + gap) // (minimum_card_width + gap))
        columns = max(1, min(self.config.grid_columns, max_columns_that_fit))
        card_width = max(
            minimum_card_width,
            min(250, (grid_rect.width - (gap * (columns - 1))) // columns),
        )
        card_height = int(card_width * 1.24)
        visible_rows = max(1, (grid_rect.height + gap) // (card_height + gap))

        return LayoutMetrics(
            header_rect=header_rect,
            grid_rect=grid_rect,
            detail_rect=detail_rect,
            footer_rect=footer_rect,
            columns=columns,
            card_width=card_width,
            card_height=card_height,
            gap=gap,
            visible_rows=visible_rows,
        )

    def _build_background(self, screen_size: tuple[int, int]) -> "pygame.Surface":
        width, height = screen_size
        surface = pygame.Surface(screen_size)

        for row in range(height):
            blend = row / max(1, height - 1)
            color = _blend_color(BG_TOP, BG_BOTTOM, blend)
            pygame.draw.line(surface, color, (0, row), (width, row))

        overlay = pygame.Surface(screen_size, pygame.SRCALPHA)
        pygame.draw.circle(overlay, (46, 170, 164, 44), (int(width * 0.18), int(height * 0.2)), int(height * 0.18))
        pygame.draw.circle(overlay, (255, 122, 72, 38), (int(width * 0.78), int(height * 0.72)), int(height * 0.22))
        pygame.draw.rect(overlay, (255, 255, 255, 8), (int(width * 0.52), 0, int(width * 0.48), height))
        surface.blit(overlay, (0, 0))
        return surface

    def _draw(self) -> None:
        self.screen.blit(self.background_surface, (0, 0))
        self._draw_header()
        self._draw_grid_panel()
        self._draw_detail_panel()
        self._draw_footer()

    def _draw_header(self) -> None:
        _draw_panel(self.screen, self.layout.header_rect, PANEL_COLOR, 180)

        title_surface = self.title_font.render(self.config.window_title, True, TEXT_PRIMARY)
        self.screen.blit(title_surface, (self.layout.header_rect.x + 28, self.layout.header_rect.y + 18))

        library_text = f"{len(self.scan_result.games)} games"
        if self.scan_result.warnings:
            library_text += f"  |  {len(self.scan_result.warnings)} warning(s)"

        info_surface = self.body_font.render(library_text, True, TEXT_MUTED)
        self.screen.blit(info_surface, (self.layout.header_rect.x + 30, self.layout.header_rect.y + 60))

        mode_label = "PRODUCTION" if self.config.production_mode else "DEVELOPMENT"
        mode_color = SUCCESS if self.config.production_mode and self.bridge_manager.should_start() else ACCENT_SOFT
        self._draw_badge(mode_label, self.layout.header_rect.right - 206, self.layout.header_rect.y + 24, mode_color)

    def _draw_grid_panel(self) -> None:
        _draw_panel(self.screen, self.layout.grid_rect, PANEL_COLOR, 168)

        section_title = self.section_font.render("Game Library", True, TEXT_PRIMARY)
        self.screen.blit(section_title, (self.layout.grid_rect.x + 24, self.layout.grid_rect.y + 20))

        hint_surface = self.small_font.render("F5 refreshes the library after adding new folders.", True, TEXT_MUTED)
        self.screen.blit(hint_surface, (self.layout.grid_rect.x + 24, self.layout.grid_rect.y + 54))

        if not self.scan_result.games:
            self._draw_empty_library_state()
            return

        top_offset = self.layout.grid_rect.y + 92
        total_rows = (len(self.scan_result.games) + self.layout.columns - 1) // self.layout.columns
        max_scroll_row = max(0, total_rows - self.layout.visible_rows)
        self.scroll_row = max(0, min(self.scroll_row, max_scroll_row))

        cards_area_width = (self.layout.card_width * self.layout.columns) + (self.layout.gap * (self.layout.columns - 1))
        start_x = self.layout.grid_rect.x + max(24, (self.layout.grid_rect.width - cards_area_width) // 2)

        first_visible_row = self.scroll_row
        last_visible_row = min(total_rows, self.scroll_row + self.layout.visible_rows)

        for row in range(first_visible_row, last_visible_row):
            for col in range(self.layout.columns):
                index = (row * self.layout.columns) + col
                if index >= len(self.scan_result.games):
                    continue

                x = start_x + (col * (self.layout.card_width + self.layout.gap))
                y = top_offset + ((row - self.scroll_row) * (self.layout.card_height + self.layout.gap))
                card_rect = pygame.Rect(x, y, self.layout.card_width, self.layout.card_height)
                self._draw_game_card(card_rect, self.scan_result.games[index], index == self.selected_index)

    def _draw_empty_library_state(self) -> None:
        message_rect = self.layout.grid_rect.inflate(-48, -148)
        _draw_panel(self.screen, message_rect, PANEL_ALT, 156)

        title = self.section_font.render("Your library is empty", True, TEXT_PRIMARY)
        detail = self.body_font.render(
            "Create a folder under games/ with one .jar, cover.png, and des.txt to add a title.",
            True,
            TEXT_MUTED,
        )

        self.screen.blit(title, (message_rect.x + 24, message_rect.y + 30))
        self.screen.blit(detail, (message_rect.x + 24, message_rect.y + 72))

    def _draw_game_card(self, card_rect: "pygame.Rect", game: GameEntry, is_selected: bool) -> None:
        fill = PANEL_ALT if is_selected else (15, 24, 40)
        alpha = 216 if is_selected else 180
        _draw_panel(self.screen, card_rect, fill, alpha)

        outline_color = ACCENT if is_selected else OUTLINE
        outline_width = 4 if is_selected else 2
        pygame.draw.rect(self.screen, outline_color, card_rect, outline_width, border_radius=22)

        inner_rect = card_rect.inflate(-16, -16)
        cover_height = int(inner_rect.height * 0.72)
        cover_rect = pygame.Rect(inner_rect.x, inner_rect.y, inner_rect.width, cover_height)
        cover_surface = self._get_cover_surface(game, cover_rect.size)
        self.screen.blit(cover_surface, cover_rect)

        if game.warnings:
            badge_rect = pygame.Rect(card_rect.right - 38, card_rect.y + 12, 24, 24)
            pygame.draw.circle(self.screen, WARNING, badge_rect.center, 12)
            badge_surface = self.badge_font.render("!", True, (23, 29, 42))
            self.screen.blit(badge_surface, badge_surface.get_rect(center=badge_rect.center))

        title_lines = _wrap_text(game.title, self.card_font, inner_rect.width, max_lines=2)
        title_y = cover_rect.bottom + 12
        for line in title_lines:
            line_surface = self.card_font.render(line, True, TEXT_PRIMARY)
            self.screen.blit(line_surface, (inner_rect.x, title_y))
            title_y += line_surface.get_height() + 4

        path_surface = self.small_font.render(game.folder.name, True, TEXT_MUTED)
        self.screen.blit(path_surface, (inner_rect.x, card_rect.bottom - 28))

    def _draw_detail_panel(self) -> None:
        _draw_panel(self.screen, self.layout.detail_rect, PANEL_COLOR, 178)

        panel_x = self.layout.detail_rect.x + 28
        panel_y = self.layout.detail_rect.y + 22

        section_title = self.section_font.render("Selected Game", True, TEXT_PRIMARY)
        self.screen.blit(section_title, (panel_x, panel_y))

        game = self.selected_game
        if game is None:
            empty_title = self.body_font.render("Waiting for games", True, TEXT_MUTED)
            empty_desc = self.body_font.render("The detail panel will update when the library has entries.", True, TEXT_MUTED)
            self.screen.blit(empty_title, (panel_x, panel_y + 48))
            self.screen.blit(empty_desc, (panel_x, panel_y + 82))
            return

        title_surface = self.title_font.render(game.title, True, TEXT_PRIMARY)
        self.screen.blit(title_surface, (panel_x, panel_y + 44))

        subline = self.small_font.render(f"{game.jar_path.name}  |  {game.folder.name}", True, TEXT_MUTED)
        self.screen.blit(subline, (panel_x, panel_y + 92))

        cover_width = self.layout.detail_rect.width - 56
        cover_height = max(240, min(420, int(self.layout.detail_rect.height * 0.44)))
        cover_rect = pygame.Rect(panel_x, panel_y + 124, cover_width, cover_height)
        cover_surface = self._get_cover_surface(game, cover_rect.size)
        self.screen.blit(cover_surface, cover_rect)
        pygame.draw.rect(self.screen, OUTLINE, cover_rect, 2, border_radius=20)

        description_top = cover_rect.bottom + 26
        body_title = self.section_font.render("Description", True, ACCENT_SOFT)
        self.screen.blit(body_title, (panel_x, description_top))

        available_width = self.layout.detail_rect.width - 56
        description_lines = _wrap_text(game.description, self.body_font, available_width, max_lines=10)
        line_y = description_top + 42
        for line in description_lines:
            line_surface = self.body_font.render(line, True, TEXT_MUTED)
            self.screen.blit(line_surface, (panel_x, line_y))
            line_y += line_surface.get_height() + 6

        if game.warnings:
            warning_y = self.layout.detail_rect.bottom - 56
            warning_text = self.small_font.render(game.warnings[0], True, WARNING)
            self.screen.blit(warning_text, (panel_x, warning_y))

    def _draw_footer(self) -> None:
        _draw_panel(self.screen, self.layout.footer_rect, PANEL_COLOR, 188)

        status_surface = self.small_font.render(self.status_message, True, ACCENT_SOFT)
        self.screen.blit(status_surface, (self.layout.footer_rect.x + 24, self.layout.footer_rect.y + 14))

        bridge_surface = self.small_font.render(self.bridge_manager.last_status, True, TEXT_MUTED)
        self.screen.blit(bridge_surface, (self.layout.footer_rect.x + 24, self.layout.footer_rect.y + 36))

        controls_text = "Move: WASD / Arrows   Launch: Enter / Space   Refresh: F5   Exit: Esc"
        controls_surface = self.small_font.render(controls_text, True, TEXT_MUTED)
        self.screen.blit(
            controls_surface,
            (
                self.layout.footer_rect.right - controls_surface.get_width() - 24,
                self.layout.footer_rect.y + 25,
            ),
        )

    def _draw_badge(self, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
        padding_x = 16
        padding_y = 10
        text_surface = self.badge_font.render(text, True, (14, 21, 34))
        badge_rect = pygame.Rect(
            x,
            y,
            text_surface.get_width() + (padding_x * 2),
            text_surface.get_height() + (padding_y * 2),
        )

        pygame.draw.rect(self.screen, color, badge_rect, border_radius=16)
        self.screen.blit(text_surface, (badge_rect.x + padding_x, badge_rect.y + padding_y))

    def _build_font(self, size: int, bold: bool = False) -> "pygame.font.Font":
        candidates = ["bahnschrift", "trebuchetms", "verdana", "dejavusans"]
        font_path = None
        for candidate in candidates:
            font_path = pygame.font.match_font(candidate, bold=bold)
            if font_path:
                break
        return pygame.font.Font(font_path, size)

    def _suspend_audio(self) -> tuple[int, int, int] | None:
        mixer_state = pygame.mixer.get_init()
        if mixer_state is None:
            return None

        pygame.mixer.stop()
        pygame.mixer.quit()
        return mixer_state

    def _restore_audio(self, mixer_state: tuple[int, int, int] | None) -> None:
        if mixer_state is None or pygame.mixer.get_init() is not None:
            return

        frequency, sample_size, channels = mixer_state
        try:
            pygame.mixer.init(frequency=frequency, size=sample_size, channels=channels)
        except pygame.error:
            pass

    def _get_cover_surface(self, game: GameEntry, size: tuple[int, int]) -> "pygame.Surface":
        cache_key = (str(game.cover_path) if game.cover_path else f"placeholder:{game.slug}", size)
        if cache_key in self.thumbnail_cache:
            return self.thumbnail_cache[cache_key]

        if game.cover_path is not None:
            try:
                image_surface = pygame.image.load(str(game.cover_path)).convert_alpha()
                scaled_surface = pygame.transform.smoothscale(image_surface, size)
            except pygame.error:
                scaled_surface = self._build_placeholder_surface(size, game.title)
        else:
            scaled_surface = self._build_placeholder_surface(size, game.title)

        self.thumbnail_cache[cache_key] = scaled_surface
        return scaled_surface

    def _build_placeholder_surface(self, size: tuple[int, int], title: str) -> "pygame.Surface":
        surface = pygame.Surface(size)
        for row in range(size[1]):
            blend = row / max(1, size[1] - 1)
            color = _blend_color((31, 52, 76), (15, 25, 40), blend)
            pygame.draw.line(surface, color, (0, row), (size[0], row))

        overlay = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.circle(overlay, (255, 142, 82, 44), (int(size[0] * 0.78), int(size[1] * 0.28)), int(min(size) * 0.18))
        pygame.draw.circle(overlay, (89, 185, 177, 36), (int(size[0] * 0.2), int(size[1] * 0.78)), int(min(size) * 0.24))
        surface.blit(overlay, (0, 0))

        initials = "".join(word[0] for word in title.split()[:2]).upper() or "AR"
        initials_font = self._build_font(max(34, min(92, size[0] // 3)), bold=True)
        initials_surface = initials_font.render(initials, True, TEXT_PRIMARY)
        initials_rect = initials_surface.get_rect(center=(size[0] // 2, int(size[1] * 0.46)))
        surface.blit(initials_surface, initials_rect)

        label_lines = _wrap_text(title, self.small_font, size[0] - 28, max_lines=2)
        line_y = int(size[1] * 0.72)
        for line in label_lines:
            line_surface = self.small_font.render(line, True, TEXT_MUTED)
            line_rect = line_surface.get_rect(center=(size[0] // 2, line_y))
            surface.blit(line_surface, line_rect)
            line_y += line_surface.get_height() + 4

        return surface


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arcade Machine game selector")
    parser.add_argument("--config", type=Path, help="Path to a JSON config file.")
    parser.add_argument("--library-root", help="Override the game library root directory.")
    parser.add_argument("--windowed", action="store_true", help="Force windowed mode even if the config enables fullscreen.")
    parser.add_argument("--production", action="store_true", help="Enable Linux production behavior, including controller bridge startup.")
    parser.add_argument("--validate", action="store_true", help="Validate config and game library contents without opening the UI.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(
            args.config,
            library_root_override=args.library_root,
            windowed=args.windowed,
            production_override=True if args.production else None,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    prepare_runtime_paths(config)

    if args.validate:
        return _validate_configuration(config)

    if pygame is None:
        print(
            f"pygame is required to run the launcher. Install dependencies from requirement.txt. {PYGAME_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 2

    bridge_manager = ControllerBridgeManager(config)
    try:
        bridge_manager.start()
    except ControllerBridgeError as exc:
        print(f"Unable to start the controller bridge: {exc}", file=sys.stderr)
        return 1

    app = ArcadeLauncherApp(config, bridge_manager)
    try:
        app.run()
    finally:
        bridge_manager.stop()
        pygame.quit()

    return 0


def _validate_configuration(config: LauncherConfig) -> int:
    scan_result = discover_games(config.library_root)

    print(f"Config path: {config.config_path}")
    print(f"Library root: {config.library_root}")
    print(f"Production mode: {config.production_mode}")
    print(f"Controller bridge enabled: {config.enable_controller_bridge}")
    print(f"Java command: {config.java_command}")
    print(f"Games found: {len(scan_result.games)}")

    if scan_result.games:
        for game in scan_result.games:
            print(f" - {game.title}: {game.jar_path.name}")

    if scan_result.warnings:
        print("Warnings:")
        for warning in scan_result.warnings:
            print(f" - {warning}")

    return 0


def _build_java_command(java_command: str, jar_path: Path, is_windows: bool) -> list[str]:
    base_command = shlex.split(java_command, posix=not is_windows)
    if not base_command:
        raise ValueError("java_command cannot be empty.")
    return [*base_command, "-jar", str(jar_path)]


def _blend_color(start: tuple[int, int, int], end: tuple[int, int, int], blend: float) -> tuple[int, int, int]:
    return (
        int(start[0] + ((end[0] - start[0]) * blend)),
        int(start[1] + ((end[1] - start[1]) * blend)),
        int(start[2] + ((end[2] - start[2]) * blend)),
    )


def _draw_panel(
    surface: "pygame.Surface",
    rect: "pygame.Rect",
    color: tuple[int, int, int],
    alpha: int,
) -> None:
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (*color, alpha), panel.get_rect(), border_radius=PANEL_RADIUS)
    pygame.draw.rect(panel, (*OUTLINE, min(255, alpha + 30)), panel.get_rect(), 1, border_radius=PANEL_RADIUS)
    surface.blit(panel, rect.topleft)


def _wrap_text(text: str, font: "pygame.font.Font", max_width: int, max_lines: int | None = None) -> list[str]:
    lines: list[str] = []

    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        current_line = words[0]
        for word in words[1:]:
            candidate = f"{current_line} {word}"
            if font.size(candidate)[0] <= max_width:
                current_line = candidate
            else:
                lines.append(current_line)
                current_line = word
        lines.append(current_line)

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        trimmed = lines[-1]
        while trimmed and font.size(f"{trimmed}...")[0] > max_width:
            trimmed = trimmed[:-1]
        lines[-1] = f"{trimmed.rstrip()}..."

    return lines


if __name__ == "__main__":
    raise SystemExit(main())