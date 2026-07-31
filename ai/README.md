# AI Integration

This directory contains the shared instructions, workflows, scripts, and data
contracts used when an LLM CLI evaluates a running PyneReal session.

## Layout

- `INSTRUCTIONS.md`: common rules for every supported LLM CLI
- `provider/`: provider-specific chat runtimes and streaming adapters
- `workflows/`: task-specific procedures and required evidence
- `scripts/`: deterministic data collectors and shared dynamic tools
- `schemas/`: JSON contracts produced by the scripts

CLI-specific entry files such as the repository `AGENTS.md` should stay small
and point to `INSTRUCTIONS.md`. Do not duplicate the common rules in multiple
CLI-specific files.

## File Editing

Dashboard Codex threads have read-only filesystem access. Explicit file-change
requests use the dynamic tools implemented in `ai/scripts/file_tools.py`:

- `edit_existing_file` performs validated exact-text replacements in existing
  files under `workdir/`, `modules/`, or `data_service/templates/`.
- `write_tmp_file` creates files and directories only under `tmp/`.

The tool handler validates paths and symbolic links in the data-service process;
the Codex command sandbox itself never receives filesystem write access.

## Telegram Delivery

`send_telegram_message` sends an explicitly requested AI result to the fixed
Telegram destination configured by `BOT_TOKEN` and `CHAT_ID` in `.env`. The AI
supplies only plain-text message content; credentials and the destination are
loaded and kept inside the data-service process. Long results are split into
messages of at most 3,900 characters.

## Manual Alert Triggers

Dashboard Codex threads use three server-controlled dynamic tools:

- `get_manual_alert_context` lists active sessions, configured templates,
  current prices, and existing triggers without exposing webhook credentials.
- `set_manual_alert_trigger` appends a validated trigger through the existing
  session registry, which persists it and pushes the updated state to charts.
- `delete_manual_alert_triggers` removes selected, session-wide, or globally
  scoped active triggers while preserving configured templates.

The AI must resolve the exact session, price, and template before setting a
trigger. When the requested template is missing, a user-supplied title and
message format are persisted as a new session template together with the price
trigger. The template may also contain an explicit optional AI instruction,
which runs only after successful direct or price-trigger webhook delivery. A
combined "delete all in this session, then set this trigger" request uses the
setting tool's atomic replacement option.

## Session Calendar

Dashboard Codex threads use `get_calendar_context` to resolve natural-language
symbols and company names to active sessions. `replace_calendar_events` then
replaces only the researched date range for those sessions and broadcasts the
new state to every open dashboard. A schedule shared by multiple sessions is
stored once with all affected sessions linked; its calendar card expands to show
the linked sessions.

SaveTicker calendar titles are discovery leads rather than sufficient evidence.
The AI verifies dates and details through public search or authoritative company,
filing, exchange, or economic-calendar sources and records the supporting URL
with every event. A general schedule request covers all active sessions and uses
today through 90 days ahead unless the user specifies another range.

Calendar event cards can start isolated read-only forecast turns. These turns
use the shared dashboard model and effort selection but do not append messages
to the main chat. Completed Markdown answers are persisted by event ID and
removed automatically when the associated event no longer exists.

## Strategy Instructions

Realtime `strategy.entry` and `strategy.close` orders can provide an `ai`
instruction. PyneReal sends it to the enabled AI service only when that order
fills on the latest confirmed bar. The runner queues the event without waiting
for the AI response; data-service executes it for the exact originating session
and records the instruction and result in shared dashboard chat history.
Strategy instructions run independently from dashboard chat and from other
sessions, while instructions from the same session run in event order.
