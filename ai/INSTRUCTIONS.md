# Shared AI Instructions

## Purpose

Use deterministic local scripts to collect session, account, position, and
market data, then evaluate that evidence. Do not rediscover APIs or application
internals when an existing script already provides the required data.

## Required Process

1. Select the workflow under `ai/workflows/` that matches the request.
2. Run only the scripts required by that workflow.
3. Treat script JSON output as the factual input to the evaluation.
4. Clearly distinguish observed facts from interpretation and uncertainty.
5. Return the final evaluation without exposing internal credentials or secret
   configuration values.

## Account Selection

- When an asset or position request does not name an account or exchange, query
  every account configured in `workdir/config/providers.toml` by running the
  relevant script without `--account` or `--exchange`.
- When only an exchange is named, use `--exchange <exchange>` to query every
  configured account for that exchange.
- When a configured account name is given, use `--account <account>` to query
  only that account.
- Do not ask the user to select an account when neither an account nor an
  exchange was specified; the default is all configured accounts.

## Safety

- Data collection is read-only by default.
- Never place API keys, secrets, passphrases, tokens, or private keys in prompts,
  command arguments, logs, or output.
- Scripts that can trade, transfer, withdraw, or mutate server state must be
  separate from read-only scripts and require explicit execution confirmation.
- Do not execute a state-changing script unless the user explicitly requests
  that exact action.
- Redact sensitive fields if an upstream API unexpectedly returns them.

## Script Contract

- Put reusable Python tools in `ai/scripts/`.
- Write machine-readable results as JSON to standard output.
- Write progress messages and diagnostics to standard error.
- Exit with a non-zero status when required data cannot be collected reliably.
- Include the schema version, collection time, source, and relevant session or
  account identifiers in the result.
- Keep exchange-specific normalization inside the script so workflows receive a
  stable data shape.

## Evaluation Rules

- Do not infer a current position, balance, order, or price from stale context.
- Include the observation time when evaluating live account or market state.
- State missing or stale evidence explicitly.
- Do not present a trading interpretation as guaranteed future performance.

## User-visible Progress

- Keep intermediate commentary useful and concrete. Report the account or
  exchange currently being queried, meaningful collection progress, partial
  failures, retries, or missing evidence.
- Do not expose internal preparation such as reading instruction files,
  exploring the repository, selecting a workflow, checking script options, or
  planning how the result will be organized.
- Do not announce that instructions or execution options will be checked before
  the lookup. Start the required lookup and report only material progress.
- For asset and position lookups, begin the final response with the observation
  time and collected result. Omit workflow narration and generic preambles.
