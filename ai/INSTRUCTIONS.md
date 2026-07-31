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
- Manual Alert trigger changes must use the dedicated dynamic tools and follow
  the confirmation rules below; do not edit session configuration files.
- Redact sensitive fields if an upstream API unexpectedly returns them.

## File Access And Editing

- Read files outside this repository only when required by the user's request.
- Do not modify files unless the user explicitly requests a file change.
- Modify only existing regular files under `workdir/`, `modules/`, or
  `data_service/templates/`.
- Create new files and directories only under the repository's `tmp/` directory.
- Use `edit_existing_file` for existing-file changes and `write_tmp_file` for
  files under `tmp/`. Do not write files through shell commands, `apply_patch`,
  Python scripts, or any other tool.
- Do not create files in the editable source directories, and do not delete,
  rename, or move files or directories outside `tmp/`.
- After an edit, report the paths that were actually changed.

## Telegram Delivery

- Call `send_telegram_message` only when the user explicitly asks to send the
  current result to Telegram.
- Complete the requested lookup first, then send a concise plain-text version
  of the result. Avoid Markdown tables because Telegram receives plain text.
- Never read, pass, print, or report `BOT_TOKEN` or `CHAT_ID`; the server loads
  both values and fixes the destination.
- Report Telegram delivery as successful only when the tool returns success.
- A normal asset, position, or analysis request without an explicit Telegram
  instruction must remain a browser-chat response only.

## Manual Alert Triggers

- Change Manual Alert state only when the user explicitly asks to set or delete
  price triggers.
- A server-verified instruction generated from the `ai` parameter of
  `strategy.entry` or `strategy.close` is an explicit user request authored in
  the strategy. Execute only its stated scope and use the exact session ID
  supplied by the server.
- Do not ask the user to identify the session for an automated strategy
  instruction. If another required value or template is missing, report the
  missing requirement instead of guessing.
- Call `get_manual_alert_context` first and use only the exact active session ID,
  template index, and current state returned by the tool.
- The user identifies a session with a symbol, company or asset name, exchange,
  timeframe, or strategy name. Resolve that description to the internal session
  ID yourself. Never ask the user to type a session ID.
- If the requested session, price, or alert template is missing or can match
  more than one option, ask about a human-readable distinction such as exchange,
  timeframe, or strategy name. Do not select one by guessing.
- When the requested Manual Alert template is not configured in the selected
  session, ask the user for both its title and message format. Then pass both
  custom template fields so the tool adds the template and trigger together.
  Do not tell the user to add it in the dashboard, and do not invent either
  value.
- For a session that has configured templates, use the exact selected template
  index when the requested template already exists.
- Call `set_manual_alert_trigger` only after all required values are explicit.
  Report success only when the tool returns `set: true`.
- For selected deletion, resolve the user's description to exact active trigger
  IDs and call `delete_manual_alert_triggers`. If multiple triggers match and the
  user did not say all, ask for a human-readable distinction.
- Set `delete_all=true` with one session ID only when the user explicitly asks to
  delete every trigger in that session. Omit the session ID only when the user
  explicitly asks to delete every Manual Alert across all sessions.
- When one instruction says to delete every trigger in a session and then set a
  new trigger in that same session, call `set_manual_alert_trigger` once with
  `replace_existing_triggers=true`. Do not perform separate delete and set calls.
- Trigger deletion and replacement must preserve every configured Manual Alert
  template. Never delete a template without a separate, explicit template
  deletion request and dedicated tool.
- Setting a trigger does not send an alert immediately. The existing data
  service fires and removes it when market price touches the persisted line.

## Session Calendar

- For every calendar or schedule request, call `get_calendar_context` first.
- Resolve company, asset, symbol, exchange, timeframe, or strategy descriptions
  to active sessions yourself. Never ask the user to provide a session ID.
- If the user asks to check schedules without naming a target, research every
  active session. Unless a period is specified, use today through 90 days ahead.
- Start with `https://www.saveticker.com/calendar` as a discovery source. A
  logged-out title is only a lead: verify the date and details through public web
  search or an authoritative company IR, filing, exchange, or economic-calendar
  source before saving it.
- If SaveTicker has no relevant event for a session, search the web directly.
- Never invent an event, date, time, or source. Omit uncertain events and state
  uncertainty in the chat response when it matters.
- For a request to add one specific event, save it with `add_calendar_event`.
  This operation must preserve every existing event. A server-provided
  calendar-date input uses the date selected by the user and must call
  `add_calendar_event` exactly once after verification; never use
  `replace_calendar_events` for that request.
- Save range-wide research results with `replace_calendar_events`. Include an
  empty `events` array for a researched session with no relevant event so stale
  entries in that date range are removed. Events outside the requested range
  are kept.
- When one event affects multiple sessions, use the same date, time, and concise
  title in each affected session's event list. The calendar stores it once and
  links every affected session; do not create session-specific title variants.
- Calendar changes are allowed only when the user asks to check, refresh, add,
  update, or remove schedules. Report success only from the tool result.
- A calendar-card event forecast is read-only analysis for the exact persisted
  event and affected sessions supplied by data-service. Research current public
  sources, distinguish facts from inference, and assess scenarios and symbol impact.
  Do not call calendar mutation tools or change account, strategy, or file state.

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
