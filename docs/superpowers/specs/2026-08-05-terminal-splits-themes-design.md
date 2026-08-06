# Terminal splits, renames, and per-terminal themes

**Date:** 2026-08-05
**Branch:** `ui-redesign`
**Goal:** tmux-style pane splitting inside the Terminal Manager, renameable
terminals, and a set of color themes selectable per terminal.

## Current state

`TerminalPanel` shows one terminal at a time: a list on the left, a single
visible view on the right (all views live in one layout, only the selected one
is shown). `TerminalView` paints through module-level palette constants
(VS Code Dark+ only). Titles come from the PTY/hub and cannot be changed.

## Design

### Components

**`bytebarn/app/term_themes.py`** (new) — pure data, no widgets:

- `TermTheme` dataclass: `name`, `fg`, `bg`, `cursor`, `selection`, and
  `ansi` — exactly 16 hex colors (normal + bright).
- `THEMES` ordered dict with ten built-ins: Dark+ (current default,
  unchanged colors), Night Barn (brand: warm charcoal + lantern amber),
  Solarized Dark, Solarized Light, Dracula, Nord, Gruvbox Dark, Monokai,
  One Dark, Tokyo Night.
- `get_theme(name)` falls back to Dark+ for unknown names.

**Themable views** — `TerminalView` and `LogTerminalView` gain
`set_theme(theme)`. The module-level `_PALETTE`/`_DEFAULT_FG`… constants
become per-instance state seeded from Dark+, so existing behaviour is
unchanged until a theme is applied.

**`bytebarn/app/pane_area.py`** (new) — the tiling container:

- `TermPane(QFrame)`: slim header (title label, 🎨 theme button, ✕ close)
  above a body slot holding one terminal view. Signals: `activated`,
  `close_requested`, `rename_requested`, `theme_menu_requested`.
  Double-clicking the header title requests a rename.
- `PaneArea(QWidget)`: manages panes in nested `QSplitter`s.
  API: `panes()`, `active_pane()`, `split(pane, orientation) -> TermPane`,
  `close_pane(pane)` (reparents the splitter tree back together),
  `set_active(pane)` (accent border on the active pane header).
  Always keeps ≥ 1 pane; closing the last pane just empties it.

**`TerminalPanel` rewiring** — keeps its public API (`_views`, `list`,
`handle_event`, `refresh_from_hub`, `_select_id`, `shutdown`):

- The single-view host is replaced by a `PaneArea` plus a hidden "parking"
  widget for views not currently mounted in any pane.
- Selecting a terminal in the list mounts its view into the active pane
  (the pane's previous view is parked — its terminal keeps running).
- Toolbar gains **Split →** and **Split ↓**: split the active pane and spawn
  a new local shell into the new pane.
- Closing a pane parks its view; the terminal stays alive and stays in the
  list. Kill semantics are unchanged.

### Rename

- List context menu ("Rename…"), list double-click, and pane-header
  double-click all open a `QInputDialog` prefilled with the current title.
- Custom titles live in `TerminalPanel._titles[tid]` and win over hub/PTY
  titles everywhere labels are formatted. For hub-backed terminals the new
  `ProcessHub.rename(terminal_id, title)` also updates `TerminalInfo.title`
  so `refresh_from_hub` cannot resurrect the old name.

### Themes

- Pane-header 🎨 button opens a menu of theme names (checkmark on the
  current one) plus "Set as default". The same menu appears in the list
  context menu.
- Per-terminal choice lives in `TerminalPanel._theme_names[tid]`
  (session-scoped — terminals themselves are session-scoped).
- The default for new terminals comes from global config key
  `terminal.theme`; "Set as default" writes it via `patch_config_file`.

### Error handling

- Unknown theme name in config → Dark+.
- Splitting when the active pane's shell can't spawn shows the failure text
  in the new pane (same path as + Shell today).
- Rename to empty/whitespace is ignored.

## Testing

- `tests/app/test_term_themes.py` — every theme has 16 valid hex ANSI
  colors + valid fg/bg/cursor; `get_theme` fallback.
- `tests/app/test_terminal_splits.py` — offscreen: split → two panes; close
  → collapses back; select mounts view into active pane and parks the old
  one; rename updates list label and pane header (and hub title for backend
  terminals); `set_theme` changes the view's background; config default
  applied to new terminals.
- Existing `test_terminal_panel.py` must pass unmodified.
