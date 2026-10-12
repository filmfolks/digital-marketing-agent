# Tool: `cancel_task`

Cancel a task that has not run yet.

## When to use
Delete / cancel / drop a planned task.

## Steps
1. Get the task_id.
2. Call cancel_task with a short reason.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "minLength": 3
    },
    "reason": {
      "type": "string",
      "maxLength": 300
    }
  },
  "required": [
    "task_id"
  ],
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "cancel_task", "arguments": {"task_id": "t-20261013-ab12cd34", "reason": "Event moved"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
