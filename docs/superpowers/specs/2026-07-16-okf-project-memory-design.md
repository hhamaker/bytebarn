# Per-project OKF memory (okf.md)

Date: 2026-07-16
Status: shipped (autonomous /goal run)

## Goal

While the user prompts, agents accumulate a persistent memory profile per
project so context survives when sessions die. Format: the Open Knowledge
Format (https://okf.md) — plain markdown concepts with YAML frontmatter
(`type` required), a `log.md` change history, git-friendly, zero tooling.

## How it works

- **Bundle location**: `<crew home>/memory/<project_id>/` (per project,
  including the implicit working-directory project). `Engine.memory_dir()`.
- **Writing** — new `memory` tool (`tools/memory.py`), available to every
  agent including read-only ones (it only writes inside the bundle, so it is
  in `_ALWAYS_ALLOWED`; also in `WRITE_TOOLS` for serialized execution).
  - `save`: writes an OKF concept file (frontmatter: type/title/description/
    tags/timestamp + markdown body) at a bundle-relative path; rejects
    `../` escapes and the reserved `log.md`/`index.md`.
  - `delete`: removes a concept.
  - Every change is prepended to `log.md` under today's date, OKF-style.
- **Reading** — `runner.load_memory()` collects the bundle (concepts first,
  log.md last, >32 KB files listed by name only) and `build_system_prompt`
  injects it into **every run in the project** as `<project-memory
  file="…">` sections, preceded by a `<project-memory-guide>` directive
  telling agents when to save (decisions, architecture facts, preferences,
  gotchas) and to update rather than duplicate.
- Tool description (`assets/prompts/tool_memory.txt`) gives type conventions
  (Decision / Architecture / Preference / Gotcha / Reference) and forbids
  storing secrets.

## Verified

`tests/engine/test_memory.py::test_memory_survives_session_death`: session 1
saves a preference via the tool → file + log land in the bundle → a fresh
session's captured `ModelRequest.system` contains the memory. Unit tests
cover OKF rendering, log prepending, traversal/reserved-name guards, and
loader ordering/caps.
