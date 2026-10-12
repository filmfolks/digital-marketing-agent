# Tool: `list_tasks`

List tasks, optionally filtered by status, brand or date range.

## When to use
The user asks what is planned / pending / done.

## Steps
1. Map the request to a status filter (pending_approval, approved, done…).
2. Call list_tasks.
3. Summarise briefly.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": [
        "draft",
        "pending_approval",
        "approved",
        "running",
        "done",
        "failed",
        "cancelled"
      ]
    },
    "brand": {
      "type": "string"
    },
    "start": {
      "type": "string"
    },
    "end": {
      "type": "string"
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    }
  },
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "list_tasks", "arguments": {"status": "pending_approval", "limit": 20}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
