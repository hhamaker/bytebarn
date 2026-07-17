# Feature research: what Crew should build next

Date: 2026-07-17
Scope: local desktop AI coding-agent apps, July 2026 landscape.

## Where the market is

The field has split into three shapes: IDE-embedded agents (Cursor,
Windsurf, Copilot), terminal-first harnesses (Claude Code, Codex CLI,
Aider), and autonomous cloud agents (Devin, Codex cloud). A fourth shape is
emerging that Crew sits squarely in: **desktop "mission control" apps**
that orchestrate multiple agents against local repos (Intent, dorothy,
Sculptor, ivy-tendril). Common pattern for serious users: an IDE agent for
flow plus a delegated agent for hard/long tasks — Crew competes for that
second slot.

What the leaders ship that defines table stakes in 2026:

- **MCP client support** — every mature product (Claude Code/Desktop,
  Cursor, Cline) lets users plug in Model Context Protocol servers; it is
  now the standard way to give agents access to Jira, DBs, browsers, docs.
- **Checkpoints & revert** — Cursor and Claude Code snapshot the workspace
  around agent edits; one click rolls back a bad run.
- **Parallel agents in isolated worktrees** — orchestrators run subagents
  in per-task git worktrees with a review/merge gate, instead of letting
  them fight over one working tree.
- **Review pipelines** — a diff-first "what did the agent change" surface
  with approve/revert before anything lands.
- **Hooks & automations** — user-defined commands that fire on lifecycle
  events (pre-tool, post-run), plus scheduled/recurring runs and
  kanban-style queues of delegated tasks.
- **Memory & spec-driven planning** — persistent agent memory and a shared
  living plan/spec that agents coordinate around (Intent's whole pitch;
  multi-agent-with-planner setups report large win rates over single-agent).

## Where Crew already stands

Ahead of or level with the market: multi-provider routing (14 providers,
two wire protocols), orchestrator + parallel subagents with a visual crew
stage, per-project OKF memory that agents write themselves, project
knowledge/instructions, skills, slash commands, permission modes,
compaction, automatic model fallback, cost tracking, opt-in themed UI.

## Recommended additions (priority order)

1. **MCP client** — highest leverage, clearest gap. Support stdio +
   streamable-HTTP servers configured per project (`.crew/config.json
   mcp` block); each server's tools surface to agents like built-ins,
   gated by the existing permission policy. Without this, Crew agents are
   limited to the 11 built-in tools while every competitor is extensible.
2. **Run checkpoints + diff review** — before a run's first write, create
   a shadow git stash/commit; after the run, show a per-run diff panel
   with "keep / revert" (whole run first, per-file later). Turns Full-auto
   mode from scary to routine. Builds on the existing `WRITE_TOOLS`
   serialization point.
3. **Worktree isolation for goals** — `/goal` optionally runs in a fresh
   `git worktree` (default for Full-auto), with a merge-back review step.
   Enables safely running several goals in parallel — the crew stage
   already visualizes the agents; this makes it real.
4. **Hooks & scheduled runs** — user-defined shell hooks on lifecycle
   events (`run.started`, `tool.pre`, `run.finished`) in config, plus
   simple recurring prompts ("every morning, triage new issues") saved on
   a project. The EventBus already carries all the trigger points.
5. **Goal queue + desktop notifications** — a lightweight queue/kanban of
   delegated runs per project (extends the Goals tab), with native
   notifications on finish/permission-ask so users can walk away.
6. **Context meter** — the status bar shows cost; add a context-window
   usage bar per session (tokens vs `ModelInfo.context_window`) with a
   one-click compact button. Cheap, differentiating transparency.
7. **Transcript export/share** — export a session (or goal run) to
   markdown with tool calls collapsed; teams increasingly paste agent runs
   into PRs/tickets.

Deliberately skipped: cloud execution (against Crew's local-first
identity), IDE plugin (different product), voice input (no demand signal
in this segment).

## Sources

- lushbinary.com/blog/ai-coding-agents-comparison-cursor-windsurf-claude-copilot-kiro-2026
- blink.new/blog/best-ai-coding-agents-2026
- ssojet.com/blog/ai-coding-agents-compared
- firecrawl.dev/blog/best-ai-coding-agents
- augmentcode.com/tools/best-ai-coding-agent-desktop-apps
- github.com/andyrewlee/awesome-agent-orchestrators
- workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026
- truthifi.com/education/state-of-mcp-2026-ai-agents-custom-connectors
