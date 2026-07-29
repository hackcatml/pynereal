# Update Session Calendar

1. Call `get_calendar_context` and resolve the user's natural-language scope to
   active sessions. If no scope was given, select every active session.
2. Resolve the requested date range. Default to today through 90 days ahead.
3. Review `https://www.saveticker.com/calendar` for relevant event titles.
4. Verify each candidate date and detail with public web search or an
   authoritative company IR, filing, exchange, or economic-calendar source.
5. Search the web directly for sessions with no relevant SaveTicker item.
6. Call `replace_calendar_events` once with every researched session. Include
   empty event arrays for sessions with no verified events in the range. Use the
   same date, time, and title when one event affects multiple sessions so it is
   stored once with all affected sessions linked.
7. Report the covered date range, updated sessions, and saved event count from
   the tool response. Do not claim that an event was saved without tool success.
