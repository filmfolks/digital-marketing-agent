# Tool: `get_calendar`

Get scheduled tasks grouped by day for a date range (default: next 30 days).

## When to use
The user asks what is on the calendar, or you need free slots before planning.

## Steps
1. Pass start/end as YYYY-MM-DD (default: next 30 days).

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "start": {
      "type": "string",
      "description": "YYYY-MM-DD"
    },
    "end": {
      "type": "string",
      "description": "YYYY-MM-DD"
    },
    "brand": {
      "type": "string"
    }
  },
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "get_calendar", "arguments": {"start": "2026-11-01", "end": "2026-11-30"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
