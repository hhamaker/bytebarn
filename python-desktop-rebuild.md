# Spec: "Crew" — Python Desktop AI Coding Agent

A complete, self-contained specification for a **Python desktop application**.
This document is written to be handed to a fresh session with no other
context. Everything needed to build the product is here.

---

## 1. Product definition

A local desktop app that runs AI coding agents against the user's own
codebases. The user opens a project directory, types a prompt or a goal, and
the app drives one or more LLM-backed agents that read, edit, and run code via
a controlled tool suite. The signature feature is **goal orchestration**: a
`/goal` command hands the task to an orchestrator agent
that casts a crew of specialized subagents and delegates work in parallel,
while a live animated **crew stage** shows each agent as a pixel-art critter —
working, waiting, retrying, or done.

Everything runs locally. No cloud backend, no accounts, no telemetry, no
sharing. The only network traffic is to LLM provider APIs the user configures.

---

## 2. Scope decisions (what's in / what's out)

### In (the core)

| Feature | Why it earns its place |
|---|---|
| Session engine (streaming turns, tool calls, durable history) | The heart of the product |
| Multi-provider model support with per-agent model choice | Mixing models per specialty is the whole point of a crew |
| Tool suite: bash, read, write, edit, glob, grep, webfetch, todowrite, question, task | Minimal complete set for a coding agent |
| Agent system: primary/subagent modes, markdown agent definitions, drop-in discovery | Add a file → crew grows; no registration |
| Orchestrator agent + `/goal` command | Signature workflow |
| Crew stage visualization | Signature UI; live pixel-art view of who is working |
| In-app agent editor (edit model/prompt/temperature/color, persisted to config) | Natural fit for a GUI |
| Permission system (allow/ask/deny per tool + pattern) | Safety; required for bash/edit autonomy |
| Slash commands from markdown templates | Cheap, high leverage |
| Compaction (summarize-and-continue when context fills) | Required for long sessions |
| Config layering: global + per-project JSON | Two layers, simple and predictable |

### Out (deliberately excluded)

| Feature | Why it stays out |
|---|---|
| Cloud backend, accounts, billing, telemetry | Contradicts local-first desktop app |
| Session sharing / share links | Publishes user code to a web service; out of scope |
| Chat-platform / code-host integrations (Slack, GitHub apps) | Peripheral, high maintenance |
| Client/server HTTP split | That architecture serves many frontends; one frontend → run the engine in-process |
| Plugin system | Large API surface, few wins; revisit later as Python entry points |
| LSP tool | Heavy per-language servers for marginal agent benefit |
| PTY / embedded interactive terminal | Complexity sink; plain subprocess execution covers the bash tool |
| IDE embedding | Different product |
| Snapshot/worktree machinery | Simple git-stash-based revert covers it |
| Mid-conversation system-message replay machinery | Elegant but overkill; a fixed system prompt per turn + compaction suffices |
| Hosted model gateway, auto-fetched model catalog | Ship a small static catalog + user-editable provider config instead |
| Web search tool | Provider-dependent; webfetch covers most needs. Optional later |

---

## 3. Technology stack

- **Python 3.12+**
- **UI: PySide6 (Qt 6)** — native look, rich widgets, QPainter canvas for the
  crew stage, QTextBrowser/QWebEngine-free markdown rendering. (Alternatives
  considered: Tkinter — too weak for this UI; pywebview/Flet — web stack in
  disguise; Dear PyGui — poor text layout.)
- **Async: asyncio + qasync** (Qt event loop integration). All LLM streaming
  and tool execution are async tasks; UI updates via signals.
- **LLM access: direct provider SDKs behind one adapter interface** —
  `anthropic` and `openai` Python packages cover Anthropic, OpenAI, and every
  OpenAI-compatible endpoint (LM Studio, Ollama, OpenRouter, Groq, etc.).
  Do not take a hard dependency on a mega-wrapper; the adapter is ~2
  implementations.
- **Storage: SQLite** via `aiosqlite`, one DB per machine at
  `~/.crew/crew.db`. WAL mode.
- **Schemas/validation: pydantic v2** for config, agent frontmatter, tool
  params, message parts.
- **Markdown: `markdown-it-py` + `pygments`** rendered to HTML in the
  transcript view.
- **File watching: `watchfiles`** for live reload of agent/command/config files.
- **Search: ripgrep** (`rg`) subprocess if present, pure-Python fallback.
- Packaging: `uv` project; later PyInstaller/Briefcase for distributable.

Suggested layout:

```
crew/
  app/            # Qt: windows, panels, dialogs
    main_window.py, transcript.py, prompt_bar.py,
    crew_stage.py, agent_editor.py, permission_dialog.py,
    session_list.py, settings.py
  engine/         # no Qt imports allowed here
    session.py, runner.py, compaction.py, todo.py
    providers/ (base.py, anthropic.py, openai_compat.py, catalog.py)
    tools/ (base.py, bash.py, read.py, write.py, edit.py,
            glob.py, grep.py, webfetch.py, todowrite.py,
            question.py, task.py)
    agents.py, commands.py, permissions.py, config.py, store.py
  assets/         # sprites, icons, default prompts (*.txt)
  main.py
```

Hard rule: `engine/` is UI-free and fully unit-testable; `app/` talks to it
through an async facade + an event stream (see §5.6).

---

## 4. Configuration

### 4.1 Locations (two layers, project overrides global)

- Global: `~/.crew/config.json`, `~/.crew/agent/*.md`, `~/.crew/command/*.md`
- Project: `<project>/.crew/config.json`, `.crew/agent/*.md`, `.crew/command/*.md`

Merge: deep-merge objects, project wins per key. JSON with trailing commas
tolerated (use `json5`-style lenient parse or strip comments). **Programmatic
writes must preserve the file** (key-by-key patch, not wholesale rewrite) —
this is what the agent editor uses.

### 4.2 config.json schema

```jsonc
{
  "provider": {
    "anthropic": { "api_key_env": "ANTHROPIC_API_KEY" },
    "openai":    { "api_key_env": "OPENAI_API_KEY" },
    "lmstudio":  { "base_url": "http://localhost:1234/v1", "api": "openai" }
  },
  "model": "anthropic/claude-sonnet-latest",   // default model "provider/id"
  "small_model": "anthropic/claude-haiku-latest", // titles, summaries, compaction
  "agent": {                                    // per-agent overrides (editor writes here)
    "build": { "model": "openai/gpt-x", "temperature": 0.2, "color": "#e06c75" }
  },
  "permission": {                               // see §8
    "bash":  { "default": "ask", "allow": ["git status*", "ls*"], "deny": ["rm -rf*"] },
    "edit":  "allow",
    "webfetch": "ask"
  },
  "instructions": ["AGENTS.md", "CLAUDE.md"]    // project doc files appended to system prompt if present
}
```

Secrets: prefer env-var indirection (`api_key_env`); also allow `"api_key"`
literal for local endpoints. Never log keys.

### 4.3 Agent definition files (`agent/*.md`)

Markdown with YAML frontmatter; body = system prompt. Filename = agent name.

```markdown
---
description: Writes and runs tests for a described change; reports pass/fail
mode: subagent            # subagent | primary | all
model: lmstudio/qwen3-coder-30b
temperature: 0.1
top_p: 0.95               # optional
steps: 50                 # max tool-use turns, default 100
color: "#61afef"          # crew stage tint
hidden: false
tools: { read: true, bash: true, write: true, edit: true }   # omit = all
permission: { bash: "allow" }                                # per-agent override
---
You are the TESTER. ...
```

`description` is how the orchestrator picks it — write it like an instruction.
Discovery is automatic: the task tool's description enumerates every visible
`mode: subagent`/`all` agent with its description. Add a file → crew grows
(watchfiles hot-reload; no restart).

### 4.4 Command files (`command/*.md`)

Frontmatter: `description`, optional `agent` (route to that agent), optional
`model`. Body is a template; `$ARGUMENTS` is replaced with the text after the
command. User files override built-ins of the same name.

---

## 5. Engine

### 5.1 Data model (SQLite)

```
project(id, path, name, last_opened_at)
session(id, project_id, parent_session_id NULL, title, agent, model,
        created_at, updated_at, archived)
message(id, session_id, role 'user'|'assistant', created_at,
        model, provider, tokens_in, tokens_out, cost, error NULL)
part(id, message_id, idx, type, json)      -- ordered content parts
todo(session_id, idx, content, status 'pending'|'in_progress'|'completed')
```

Part types (`json` payload per type):

- `text` — `{text}`
- `reasoning` — `{text}` (render collapsed)
- `tool` — `{tool, call_id, input, output, status: pending|running|done|error,
  title, metadata}` (updated in place as the call progresses)
- `file` — `{path, mime}` (user attachments)
- `task` — `{subagent_session_id, agent, description, status}` (subagent run)

Subagent sessions are real sessions with `parent_session_id` set — this is
what the crew stage queries.

### 5.2 Provider adapter

```python
class Provider(Protocol):
    async def stream(self, req: ModelRequest) -> AsyncIterator[Event]: ...
# ModelRequest: model_id, system: str, messages: list[Msg],
#               tools: list[ToolDef], temperature, top_p, max_tokens
# Events: TextDelta(text) | ReasoningDelta(text)
#         | ToolCallStart(call_id, name) | ToolCallDelta(call_id, json_fragment)
#         | ToolCallEnd(call_id) | Usage(in, out) | Done(stop_reason) | ErrorEv(...)
```

Two implementations: `AnthropicProvider` (Messages API, native tool use,
prompt caching on system + tools) and `OpenAICompatProvider` (chat completions
+ tool calling; covers OpenAI/LM Studio/Ollama/OpenRouter via `base_url`).
Static catalog file `catalog.py`: model id → context window, max output,
supports_tools, cost per Mtok — user-extensible via config.

Retry: exponential backoff on 429/5xx/network, max 4 attempts, surfaced in UI
as a "retrying" state (crew critters show it — §7).

### 5.3 The agent loop (runner)

Per user prompt, in a session:

1. Build system prompt: agent's prompt body + environment block (cwd, platform,
   date, git branch/status) + project instruction files (config
   `instructions`).
2. Project history: all messages/parts, minus anything before the latest
   compaction summary (which is included as a synthetic assistant message).
3. Call provider with the agent's allowed tools. Stream events → persist parts
   incrementally → emit engine events (UI renders live).
4. On tool calls: check permission (§8) — may suspend awaiting user answer —
   then execute all calls concurrently (except bash/edit/write, serialized),
   append tool results, loop back to 3.
5. Stop when model ends without tool calls, `steps` cap reached, aborted, or
   error. Then: if session has no title, generate one with `small_model`.
6. Queueing: prompts submitted mid-run are queued and promoted between turns.
   Abort (Esc/stop button) cancels the provider stream and marks pending tool
   parts as error.

Context overflow: when `tokens_in` approaches the model's window (>85%), run
**compaction**: `small_model` summarizes the session into a structured
summary; history before the summary is no longer sent (but stays in the DB and
UI). Manual `/compact` command triggers the same.

### 5.4 Tool suite

All tools: pydantic param model, `execute(params, ctx) -> ToolResult(output,
title, metadata)`. Output truncated to 30k chars (head+tail) with a note; full
output saved to `~/.crew/tool-output/<id>.txt` and referenced.

| Tool | Params | Behavior |
|---|---|---|
| `bash` | `command`, `timeout?` (default 120s, max 600s), `description` | `asyncio.create_subprocess_shell` in project cwd, user's shell env; kills process tree on timeout/abort; returns merged stdout/stderr + exit code |
| `read` | `path`, `offset?`, `limit?` (default 2000 lines) | Numbered lines `   1→...`; error if missing; images returned as file parts |
| `write` | `path`, `content` | Overwrite requires prior read of that file this session; creates parent dirs |
| `edit` | `path`, `old_string`, `new_string`, `replace_all?` | Exact unique-match replace; requires prior read; error if not found or ambiguous |
| `glob` | `pattern`, `path?` | Match files, newest first, cap 100 |
| `grep` | `pattern`, `path?`, `glob?`, `output_mode?` | ripgrep subprocess (regex), fallback Python; modes: files_with_matches (default) / content / count |
| `todowrite` | `todos: [{content, status}]` | Replaces session todo list; drives crew stage "waiting" rows |
| `question` | `question`, `options[]` | Suspends run; UI shows chooser; answer returned as tool result |
| `webfetch` | `url`, `format: text|markdown` | httpx GET, html→markdown, 5MB cap |
| `task` | `description`, `prompt`, `agent`, `task_id?` | See §5.5 |

Tool description strings live in `assets/prompts/*.txt` — write them
carefully; they are prompt engineering, not docs. (E.g. edit's "old_string must be unique",
bash's "avoid cat/grep, use dedicated tools".)

### 5.5 Subagents (`task` tool)

- Description text is generated at request time: lists every available
  subagent name + description from the registry (this is the "no
  registration" mechanic).
- Creates a child session (`parent_session_id = current`), runs the target
  agent's loop inside it with the given prompt, returns the final assistant
  text as the tool result.
- `task_id` reuse: passing an existing task's id continues that child session
  with a follow-up prompt (context preserved) — the orchestrator uses this to
  send failures back to the same worker.
- Parallel: multiple task calls in one assistant message run concurrently.
- Subagents cannot spawn subagents (no `task` tool in subagent registries).

### 5.6 Engine → UI event stream

Single async queue of typed events; Qt subscribes via qasync:
`session.updated`, `message.part.updated`, `todo.updated`, `permission.asked`,
`question.asked`, `task.started/updated/finished`, `run.finished`,
`agent.registry.changed`. The UI is a pure projection of these events plus
the DB — no engine internals reach the widgets.

---

## 6. Agent system

### 6.1 Built-in agents (defined in code, prompts in `assets/prompts/`)

| Agent | Mode | Notes |
|---|---|---|
| `build` | primary | Default. Full tool access, standard coding-agent prompt |
| `plan` | primary | Read-only (edit/write/bash-writes denied); produces plans |
| `orchestrator` | primary | The `/goal` engine — see below. `edit`/`write` denied; may read/search/run read-only bash |
| `general` | subagent | Generalist worker, full tools |
| `explore` | subagent | Read-only search/answer agent |

A user/project agent file named the same as a built-in **merges over it**
(override prompt, model, permissions without code).

### 6.2 Orchestrator prompt (use verbatim, it is tuned)

```
You are the orchestrator. You do not do the work yourself — you assemble a crew of specialized subagents and coordinate them until the goal is fully accomplished.

Your job, in order:

1. Understand the goal. Restate it in one sentence. If the goal is genuinely ambiguous in a way that changes the plan, ask one focused question; otherwise proceed with sensible defaults.
2. Plan. Break the goal into concrete tasks. Write the plan with the todowrite tool so the user can watch progress. Each todo should map to work you will delegate.
3. Cast the crew. Look at the available agent types in the task tool description. Pick the best-fit agent for each task. Prefer specialists over general agents when one matches. If no agent fits a task, use the general agent with a precise prompt.
4. Delegate. Use the task tool to run tasks. Launch independent tasks in parallel (multiple task calls in a single message). Run dependent tasks sequentially, feeding forward the results from earlier tasks in the prompt of later ones. Reuse task_id when a follow-up belongs in the same subagent's context.
5. Track. Keep todos up to date as tasks start and finish: mark in_progress when you delegate, completed when the result comes back and is verified.
6. Verify. Do not trust "done" claims blindly. When a task produces code, delegate a verification task (tests, typecheck, review) or run cheap read-only checks yourself. If verification fails, send the failure back to the same subagent via task_id.
7. Report. When the goal is complete, give the user a short summary: what was done, by which agents, and anything left for the user to decide.

Rules for delegation prompts:
- Each subagent starts with zero context. Include everything it needs: relevant file paths, constraints, prior task results, and exactly what to return.
- State clearly whether the subagent should write code or only research.
- Tell it how to verify its own work when possible.
- Never delegate two tasks that write to the same files in parallel.

What you may do yourself:
- Read files, search, and run read-only commands to plan and verify.
- Small glue decisions between tasks.

What you must not do yourself:
- Edit or write files. All file modifications go through subagents.
- Duplicate work you already delegated.

Keep your own messages short. The user is watching the crew work; your commentary should be a status line, not an essay.
```

### 6.3 `/goal` command (built-in)

Routes to `orchestrator` with template:

```
The user has set the following goal:

<goal>
$ARGUMENTS
</goal>

Orchestrate this goal to completion. Restate the goal in one sentence, write a todo plan, pick the best-fit subagents from the available agent types, and delegate the work — parallel where independent, sequential where dependent. Track todos as tasks start and finish, verify results before marking anything done, and finish with a short summary of what each agent accomplished.
```

Other built-in commands: `/compact` (force compaction), `/new` (new session),
`/model` (open model picker), `/agents` (open agent editor).

---

## 7. UI specification (PySide6)

### 7.1 Main window

```
┌───────────┬──────────────────────────────────────────────┐
│ sessions  │  transcript (scrolling, markdown, tool cards)│
│ sidebar   │                                              │
│           ├──────────────────────────────────────────────┤
│ + New     │  CREW STAGE (appears only during crew runs)  │
│ [search]  ├──────────────────────────────────────────────┤
│           │  prompt bar [agent ▾] [model ▾]      [send ■]│
└───────────┴──────────────────────────────────────────────┘
status bar: project path · git branch · token/cost for session
```

- **Sessions sidebar**: per current project; title, relative time, running
  indicator. Child (subagent) sessions nested/expandable under parents.
- **Transcript**: user messages as blocks; assistant text as rendered
  markdown; each tool call a collapsible card (title line: icon + tool +
  summary + status spinner/check/red-x; body: input + truncated output).
  Reasoning collapsed by default. Auto-scroll unless user scrolled up.
- **Prompt bar**: multi-line grow-to-4-lines editor. `/` opens command
  palette (fuzzy over built-ins + user commands). `@` fuzzy file picker →
  inserts path + attaches. Enter sends, Shift+Enter newline, Esc aborts run.
  Agent and model dropdowns; per-session selection persists.
- **Permission dialog** (modal per request): tool, rendered input (diff view
  for edit/write, command for bash), buttons: Allow once / Allow always
  (writes config allowlist pattern) / Deny. Deny returns a tool error and the
  run continues.
- **Question dialog**: options + free-text "other".

### 7.2 Crew stage (the signature feature)

A `QWidget` with a QPainter canvas, mounted between transcript and prompt bar.
Visible only when the current session has child task sessions from the current
run; hides when the run finishes.

Rendering:

- **Critters**: one 12×11-logical-pixel chibi sprite per subagent, drawn at
  integer scale (crisp pixel-art, `Qt.FastTransformation`). Species — cat,
  dog, bunny, bear — chosen by stable hash of agent name; body tinted with the
  agent's configured `color`.
- **Hub**: crowned critter = orchestrator, connected to each crew member by a
  sagging rope (quadratic curve); a pulse dot travels the rope toward each
  working critter.
- **Animation** (QTimer ~12 fps): working critters bob ±1px and blink; retry
  state → worried brows + red tint; done → happy arc eyes + smile; waiting
  rows (pending orchestrator todos not yet delegated) render as faded sleeping
  critters with drifting "z" pixels.
- **Labels**: agent name under sprite + live current-tool detail (e.g.
  `grep "session"` ), elided.
- **Interaction**: click a critter → open its subagent session in the
  transcript (back button returns). Overflow beyond ~8 critters collapses to
  a `+n more` chip.
- State derives purely from engine events: task parts (status), todo list
  (waiting), retry events.

### 7.3 Agent editor

Reachable from `/agents`, dropdown, or Settings. Two panes: agent list
(primaries + subagents, hidden ones toggleable) → edit form:

- Fields: model (searchable combo of catalog), prompt (multiline), description,
  temperature, top_p, mode, color (swatch picker), steps, hidden.
- Save writes **only changed fields** to `agent.<name>` in the nearest
  config.json via the comment/format-preserving patcher (§4.1); registry
  hot-reloads; running sessions keep their old settings until next run.
- Built-in agents show a "native + overrides" badge; "reset field" clears the
  override key.

### 7.4 Settings

Providers (add/edit endpoint + key env), default models, permission defaults,
theme (light/dark, follow system), transcript font.

---

## 8. Permission system

Per tool: `"allow" | "ask" | "deny"` or `{default, allow: [patterns],
deny: [patterns]}`. Bash patterns match the command string (glob, e.g.
`git status*`); edit/write patterns match file paths. Resolution order:
deny-list → allow-list → default. Per-agent `permission` overrides global.
`ask` emits `permission.asked`, suspends that tool call until answered.
"Allow always" appends the pattern to project config. Subagents inherit the
resolved policy of their own agent definition (not the orchestrator's).

Session-level toggle in the UI: **Safe / Ask / Full-auto** (maps to preset
policies), shown in the status bar.

---

## 9. Build order (milestones)

1. **Engine core**: config load/merge, provider adapters, session store,
   runner loop with bash/read/write/edit/glob/grep — CLI harness for testing,
   no Qt. Unit tests: tool behaviors, edit uniqueness, truncation, permission
   resolution, config patcher round-trip (comments preserved).
2. **Minimal window**: session list, transcript with streaming text + tool
   cards, prompt bar, abort. qasync wiring.
3. **Permissions + question dialogs**; todowrite + todo strip above prompt.
4. **Agents & commands**: markdown discovery + hot reload, agent/model
   pickers, slash palette, built-in agents, task tool + child sessions.
5. **/goal + crew stage**: orchestrator, sprite renderer, animation states,
   click-through to child sessions.
6. **Agent editor + settings + compaction + polish** (titles via small model,
   cost display, theme).

Definition of done for the signature flow: in a real repo, `/goal add a
--verbose flag to the CLI and test it` produces a todo plan, ≥2 subagents
visibly working on the stage in parallel, verified edits on disk, and a final
per-agent summary.

---

## 10. Non-goals (explicit)

No cloud sync, no session sharing, no accounts, no plugin API (v1), no
embedded terminal, no LSP, no IDE integration, no web UI, no MCP (v1 —
tool suite is built-in; revisit once core is stable).
