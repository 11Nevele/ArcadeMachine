#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


DEFAULT_DESCRIPTION = "No description available yet. Add text to des.txt to describe this game."


@dataclass(slots=True)
class GameEntry:
    title: str
    slug: str
    folder: Path
    jar_path: Path
    cover_path: Path | None
    description: str
    warnings: tuple[str, ...]


@dataclass(slots=True)
class LibraryScanResult:
    games: list[GameEntry]
    warnings: list[str]


def discover_games(library_root: Path) -> LibraryScanResult:
    library_root.mkdir(parents=True, exist_ok=True)

    games: list[GameEntry] = []
    warnings: list[str] = []

    for folder in sorted((item for item in library_root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
        game, game_warnings = _build_game_entry(folder)
        warnings.extend(game_warnings)
        if game is not None:
            games.append(game)

    games.sort(key=lambda game: game.title.lower())
    return LibraryScanResult(games=games, warnings=warnings)


def _build_game_entry(folder: Path) -> tuple[GameEntry | None, list[str]]:
    warnings: list[str] = []

    jar_files = sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".jar"
    )
    if not jar_files:
        return None, [f"Skipped '{folder.name}': missing .jar file."]
    if len(jar_files) > 1:
        return None, [f"Skipped '{folder.name}': expected 1 .jar file, found {len(jar_files)}."]

    cover_path = folder / "cover.png"
    if not cover_path.is_file():
        cover_path = None
        warnings.append(f"'{folder.name}' is missing cover.png. Placeholder art will be used.")

    description_path = folder / "des.txt"
    description = DEFAULT_DESCRIPTION
    if description_path.is_file():
        description = description_path.read_text(encoding="utf-8").strip() or DEFAULT_DESCRIPTION
    else:
        warnings.append(f"'{folder.name}' is missing des.txt. A default description will be shown.")

    return (
        GameEntry(
            title=_display_name(folder.name),
            slug=_slugify(folder.name),
            folder=folder,
            jar_path=jar_files[0],
            cover_path=cover_path,
            description=description,
            warnings=tuple(warnings),
        ),
        warnings,
    )


def _display_name(folder_name: str) -> str:
    name = folder_name.replace("_", " ").replace("-", " ").strip()
    if name.islower():
        return name.title()
    return name or folder_name


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized or "game"