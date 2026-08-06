"""Terminal color themes — pure data, no widgets.

Each theme is a full 16-color ANSI palette plus default fg/bg/cursor/selection.
Views apply one via ``TerminalView.set_theme`` /
``LogTerminalView.set_theme``; the panel keeps a per-terminal choice and a
global default under the ``terminal.theme`` config key. See
docs/superpowers/specs/2026-08-05-terminal-splits-themes-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TermTheme:
    name: str
    fg: str
    bg: str
    cursor: str
    selection: str          # rgba-capable hex (#RRGGBBAA)
    ansi: tuple[str, ...] = field(default=())  # 16: normal 0-7, bright 8-15


DEFAULT_THEME = "Dark+"

THEMES: dict[str, TermTheme] = {t.name: t for t in [
    TermTheme(  # the palette the panel shipped with — kept byte-identical
        name="Dark+", fg="#d4d4d4", bg="#1e1e1e",
        cursor="#aeafad", selection="#5555ff50",
        ansi=("#1e1e1e", "#f44747", "#6a9955", "#d7ba7d",
              "#569cd6", "#c586c0", "#4ec9b0", "#d4d4d4",
              "#808080", "#f44747", "#b5cea8", "#dcdcaa",
              "#9cdcfe", "#c586c0", "#4ec9b0", "#ffffff")),
    TermTheme(  # brand: the Night Workshop's warm charcoal + lantern amber
        name="Night Barn", fg="#e2ddd6", bg="#171519",
        cursor="#e5a458", selection="#e5a45840",
        ansi=("#171519", "#e05f55", "#9bb76d", "#e5a458",
              "#7d9fc4", "#b98aae", "#7fbfa8", "#e2ddd6",
              "#948d85", "#f0796e", "#b5d18a", "#f0b56b",
              "#9dbede", "#d3a5c8", "#9cd9c3", "#f5f1ea")),
    TermTheme(
        name="Solarized Dark", fg="#839496", bg="#002b36",
        cursor="#93a1a1", selection="#274d5450",
        ansi=("#073642", "#dc322f", "#859900", "#b58900",
              "#268bd2", "#d33682", "#2aa198", "#eee8d5",
              "#586e75", "#cb4b16", "#586e75", "#657b83",
              "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    TermTheme(
        name="Solarized Light", fg="#657b83", bg="#fdf6e3",
        cursor="#586e75", selection="#eee8d580",
        ansi=("#073642", "#dc322f", "#859900", "#b58900",
              "#268bd2", "#d33682", "#2aa198", "#eee8d5",
              "#586e75", "#cb4b16", "#586e75", "#657b83",
              "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    TermTheme(
        name="Dracula", fg="#f8f8f2", bg="#282a36",
        cursor="#f8f8f2", selection="#44475a80",
        ansi=("#21222c", "#ff5555", "#50fa7b", "#f1fa8c",
              "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
              "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5",
              "#d6acff", "#ff92df", "#a4ffff", "#ffffff")),
    TermTheme(
        name="Nord", fg="#d8dee9", bg="#2e3440",
        cursor="#d8dee9", selection="#434c5e80",
        ansi=("#3b4252", "#bf616a", "#a3be8c", "#ebcb8b",
              "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
              "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b",
              "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4")),
    TermTheme(
        name="Gruvbox Dark", fg="#ebdbb2", bg="#282828",
        cursor="#ebdbb2", selection="#50494580",
        ansi=("#282828", "#cc241d", "#98971a", "#d79921",
              "#458588", "#b16286", "#689d6a", "#a89984",
              "#928374", "#fb4934", "#b8bb26", "#fabd2f",
              "#83a598", "#d3869b", "#8ec07c", "#ebdbb2")),
    TermTheme(
        name="Monokai", fg="#f8f8f2", bg="#272822",
        cursor="#f8f8f0", selection="#49483e80",
        ansi=("#272822", "#f92672", "#a6e22e", "#f4bf75",
              "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
              "#75715e", "#f92672", "#a6e22e", "#f4bf75",
              "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5")),
    TermTheme(
        name="One Dark", fg="#abb2bf", bg="#282c34",
        cursor="#528bff", selection="#3e445180",
        ansi=("#282c34", "#e06c75", "#98c379", "#e5c07b",
              "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
              "#5c6370", "#e06c75", "#98c379", "#e5c07b",
              "#61afef", "#c678dd", "#56b6c2", "#ffffff")),
    TermTheme(
        name="Tokyo Night", fg="#a9b1d6", bg="#1a1b26",
        cursor="#c0caf5", selection="#33467c80",
        ansi=("#15161e", "#f7768e", "#9ece6a", "#e0af68",
              "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
              "#414868", "#f7768e", "#9ece6a", "#e0af68",
              "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5")),
]}


def get_theme(name: str) -> TermTheme:
    """Theme by name; unknown names fall back to the default."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])
