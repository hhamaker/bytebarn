# EventBus events

The engine talks to the UI exclusively through these events
(`crew/engine/events.py`) plus reads from the store. Every subscriber
receives every event (fan-out).

| name | dataclass | fields | emitted when |
|---|---|---|---|
| `session.updated` | `SessionUpdated` | `session_id` | session created, retitled, archived/deleted, message added, or run errored — "refresh the sidebar" |
| `message.part.updated` | `PartUpdated` | `session_id`, `message_id`, `part_id`, `part_type` (`text`/`reasoning`/`tool`/`task`/`compaction`/`file`), `data`, `delta` | a transcript part is created or streams new content |
| `todo.updated` | `TodoUpdated` | `session_id`, `todos` (list of `{content, status}`) | the agent rewrites its plan via the todowrite tool |
| `permission.asked` | `PermissionAsked` | `session_id`, `request_id`, `tool`, `arg`, `input` | a tool call needs user approval; answer with `engine.answer_permission(request_id, verdict)` |
| `question.asked` | `QuestionAsked` | `session_id`, `request_id`, `question`, `options` | the agent used the question tool; answer with `engine.answer_question(request_id, text)` |
| `task.started` | `TaskStarted` | `session_id` (parent), `subagent_session_id`, `agent`, `description` | orchestrator delegates work to a subagent — a critter appears |
| `task.updated` | `TaskUpdated` | `session_id`, `subagent_session_id`, `status` (`running`/`retrying`/`done`/`error`), `detail` | subagent status/tool activity changes — drives the crew stage |
| `task.finished` | `TaskFinished` | `session_id`, `subagent_session_id`, `status` | subagent run completed |
| `run.finished` | `RunFinished` | `session_id`, `status` (`done`/`aborted`/`error`) | a session's run loop ends — crew stage resets |
| `agent.registry.changed` | `AgentRegistryChanged` | — | agents/commands/config hot-reloaded — pickers refresh |

Subscribing:

```python
async for event in engine.bus.subscribe():
    ...
```
