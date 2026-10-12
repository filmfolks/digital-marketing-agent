# Tool: `schedule_task`

Set or change the date/time of an existing task.

## When to use
Move a task to another date/time.

## Steps
1. Find the task_id (list_tasks or get_calendar if you do not have it).
2. Call schedule_task with the new ISO time.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "minLength": 3
    },
    "scheduled_at": {
      "type": "string",
      "description": "ISO 8601 local date-time, e.g. 2026-10-20T18:30 (user timezone if no offset)"
    }
  },
  "required": [
    "task_id",
    "scheduled_at"
  ],
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "schedule_task", "arguments": {"task_id": "t-20261013-ab12cd34", "scheduled_at": "2026-10-21T18:30"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
