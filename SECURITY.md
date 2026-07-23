# Security

## Reporting a vulnerability

Email hhamaker22@gmail.com with details, or use GitHub's private
vulnerability reporting on this repository. Please do not open a public
issue for security problems. You will get a response within a week.

## Threat model — what ByteBarn does with your machine

ByteBarn is a local desktop app that runs LLM-driven coding agents against
folders you choose. Understand what that means before using it:

**Agents can execute shell commands.** The `bash` tool runs whatever the
model asks, subject to the permission mode:

- **Safe** — bash, file edits/writes, web fetch/search, and MCP tools are
  denied outright. Read-only tools still run.
- **Ask** (default) — risky tools prompt you per call. "Allow always" saves
  a glob-scoped rule to the project config.
- **Full-auto** — no prompts. Only use on projects where you would accept
  arbitrary command execution (containers, throwaway checkouts).

Prompt injection is real: text an agent reads (files, web pages, MCP tool
output) can try to steer it into running commands. Ask mode is the guard —
review what you approve, especially after web fetches.

**Where secrets live.** Provider API keys and OAuth tokens are stored in
`~/.bytebarn/auth.json`, created with mode 0600. They are never written to
project config files and never leave your machine except in requests to the
provider you configured them for.

**Network traffic.** ByteBarn has no backend and no telemetry. Outbound
traffic is exactly: your configured LLM providers, web fetch/search when an
agent uses those tools, MCP servers you add, and a daily GitHub release
check for updates.

**MCP servers.** Anything you add under the `mcp` config key runs with your
user's privileges (stdio) or receives your bearer token (HTTP). Treat MCP
server installation like installing software — because it is.
