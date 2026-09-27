"""Profil de la routine : thèmes cherchés, pile décrite à Jev, réglages."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources

from . import config


class ProfileError(Exception):
    """profile.toml illisible ou invalide."""


@dataclass(frozen=True)
class Profile:
    meta_themes: tuple[str, ...]
    stack_themes: tuple[str, ...]
    stack_description: str
    max_installs: int
    min_installs: int
    per_query: int
    leaderboard_top: int
    max_candidates: int

    def jev_text(self) -> str:
        return ("Thèmes méta recherchés : " + ", ".join(self.meta_themes) + ".\n"
                "Pile et projets de l'utilisateur : " + self.stack_description + "\n"
                "Mots-clés de la pile : " + ", ".join(self.stack_themes) + ".")


_BOUNDS = {"max_installs": (0, 10), "min_installs": (0, 10**9), "per_query": (1, 100),
           "leaderboard_top": (0, 600), "max_candidates": (1, 1000)}


def default_profile_text() -> str:
    return (resources.files("skillscout").joinpath("default_profile.toml")
            .read_text(encoding="utf-8"))


def _themes(section: dict, where: str) -> tuple[str, ...]:
    themes = section.get("themes")
    if (not isinstance(themes, list) or not themes
            or not all(isinstance(t, str) and t.strip() for t in themes)):
        raise ProfileError(f"[{where}] themes doit être une liste non vide de textes")
    return tuple(t.strip() for t in themes)


def parse_profile(text: str) -> Profile:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"profile.toml illisible : {e}") from e
    meta, stack, routine = (data.get(k) or {} for k in ("meta", "stack", "routine"))
    description = stack.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ProfileError("[stack] description doit être un texte non vide")
    values = {}
    for key, (lo, hi) in _BOUNDS.items():
        v = routine.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ProfileError(f"[routine] {key} doit être un entier entre {lo} et {hi}")
        values[key] = v
    return Profile(meta_themes=_themes(meta, "meta"), stack_themes=_themes(stack, "stack"),
                   stack_description=" ".join(description.split()), **values)


def load_profile(paths: config.Paths) -> Profile:
    """Lit le profil de l'utilisateur ; le crée depuis le profil par défaut
    s'il n'existe pas encore."""
    if not paths.profile.exists():
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        paths.profile.write_text(default_profile_text(), encoding="utf-8")
    try:
        text = paths.profile.read_text(encoding="utf-8")
    except OSError as e:
        raise ProfileError(f"profile.toml illisible : {e}") from e
    return parse_profile(text)
