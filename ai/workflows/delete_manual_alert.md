# Delete Manual Alert Triggers

Use this workflow only when the user explicitly asks to delete active Manual
Alert price triggers. Deleting triggers never deletes configured templates.

1. Call `get_manual_alert_context`.
2. Resolve symbols, asset or company names, exchanges, timeframes, strategy
   names, alert titles, and prices to active sessions and triggers. Use IDs only
   internally; never ask the user to provide them.
3. For one or more selected triggers in one session, pass that `session_id` and
   the exact `trigger_ids` to `delete_manual_alert_triggers`.
4. If the user explicitly asks to delete every trigger in one session, pass its
   `session_id` with `delete_all=true`.
5. If the user explicitly asks to delete every Manual Alert without limiting the
   request to a session, call the tool with only `delete_all=true`.
6. If the target or scope is ambiguous and the user did not say all, ask for a
   human-readable distinction before deleting anything.
7. Report the deleted count and affected symbols only from the tool result.

If one instruction asks to clear a session and immediately set a new trigger in
that same session, use the setting workflow with
`replace_existing_triggers=true` instead of this workflow.
