# AI Providers

Provider-specific chat runtimes live in this directory. Each implementation
owns its provider lifecycle, conversation state, and streaming response mapping
while reusing shared tools from `ai/scripts/`.

- `codex_service.py`: OpenAI Codex app-server provider

Future providers should remain separate modules rather than adding
provider-specific branches to the Codex implementation.

When the data service starts without a saved Codex login in an interactive
terminal, it asks whether to enable the Codex AI service with an Up/Down and
Enter selector. Selecting `Yes` starts device-code authentication and waits for
it to complete. Selecting `No` disables AI for that data-service process
without affecting the remaining services. Unsupported terminals fall back to a
text prompt. An unauthenticated non-interactive process also leaves AI
disabled; run `codex login` before startup to enable it there.
