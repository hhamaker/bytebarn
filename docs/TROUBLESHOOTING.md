# Engine Troubleshooting (top 5)

1. `KeyError: unknown provider/agent`  
   Add entry to `config.provider` or create `<name>.md` agent file.

2. `ValueError: model must be 'provider/id'`  
   Use the canonical string form; check `providers/known.py`.

3. `RetryableProviderError` / `APIConnectionError`  
   Transient network hiccup—engine auto-retries; ensure API key valid.

4. OAuth `RuntimeError` (token exchange failed)  
   Re-run device/login flow; cached token in `~/.crew/auth.json` may be stale.

5. Tool `is_error` (exit code / timeout / permission)  
   Inspect `output` in the `ToolResult`; grant execute perms or increase timeout in task call.