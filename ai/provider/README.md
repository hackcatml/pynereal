# AI Providers

Provider-specific chat runtimes live in this directory. Each implementation
owns its provider lifecycle, conversation state, and streaming response mapping
while reusing shared tools from `ai/scripts/`.

- `codex_service.py`: OpenAI Codex app-server provider

Future providers should remain separate modules rather than adding
provider-specific branches to the Codex implementation.

When the data service starts without a saved Codex login in an interactive
terminal, the Codex provider starts device-code authentication and waits for it
to complete. Non-interactive services must authenticate with `codex login`
before startup.
