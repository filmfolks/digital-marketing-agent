# Tool: `create_task`

Create a task on the calendar (a post to prepare or a reminder). It is saved as pending_approval; a human approves it before anything is posted.

## When to use
A to-do or reminder that is not a finished post ("remind me to shoot reels Tuesday").

## Steps
1. Write a short title and the instruction.
2. Add scheduled_at if a time was given.
3. Call create_task.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "title": {
      "type": "string",
      "minLength": 1,
      "maxLength": 120
    },
    "instruction": {
      "type": "string",
      "maxLength": 2000,
      "description": "What should be done"
    },
    "platforms": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "x",
          "instagram",
          "facebook",
          "linkedin",
          "youtube",
          "blog"
        ]
      }
    },
    "content": {
      "type": "string",
      "maxLength": 5000,
      "description": "Post text if already written"
    },
    "scheduled_at": {
      "type": "string",
      "description": "ISO 8601 local date-time, e.g. 2026-10-20T18:30 (user timezone if no offset)"
    },
    "brand": {
      "type": "string"
    }
  },
  "required": [
    "title"
  ],
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "create_task", "arguments": {"title": "Shoot 3 reels in the kitchen", "instruction": "Film burger assembly from top view", "scheduled_at": "2026-10-20T11:00"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
