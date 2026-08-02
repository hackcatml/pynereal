# Set Manual Alert Trigger

Use this workflow only when the user explicitly asks to set a Manual Alert
price trigger.

1. Call `get_manual_alert_context`.
2. Match the user's symbol, company or asset name, exchange, timeframe, or
   strategy description to one exact active session. Use its `session_id`
   internally; never ask the user to provide that ID.
3. Require one exact positive trigger price.
4. If the requested template exists, use its returned `index`.
5. If the requested template does not exist, ask the user for both its title
   and message format. Preserve the supplied format exactly and pass both custom
   fields so the tool adds the template and trigger in one operation. Do not
   tell the user to add it through the dashboard. Pass `custom_template_ai`
   only when the user explicitly supplies an AI instruction for the template.
6. If more than one session matches, ask a concise question using a meaningful
   distinction such as exchange, timeframe, or strategy name. If another value
   is missing or ambiguous, ask for it and stop without calling the setting
   tool.
7. Call `set_manual_alert_trigger` with the resolved values.
8. Report the session, symbol, price, and template title returned by the tool.

If the same instruction explicitly asks to remove every existing trigger in the
selected session before setting the new one, pass
`replace_existing_triggers=true`. This replaces the trigger list in one save and
preserves all configured templates. Do not call the deletion tool first.

Do not send the alert immediately. Do not edit `sessions.json` directly. The
data service persists the trigger and handles price-touch firing and removal.
An optional template AI instruction runs only after the webhook succeeds.
