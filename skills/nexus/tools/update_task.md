# Tool: `update_task`

Edit a task's text, platforms, title, hashtags or poster idea. Cannot approve; edits to an approved post send it back for approval.

## When to use
Change text, hashtags, platforms or the poster idea of a task.

## Steps
1. Get the task_id.
2. Send only the fields that change.
3. Mention that an approved post goes back to approval after edits.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "minLength": 3
    },
    "title": {
      "type": "string",
      "maxLength": 120
    },
    "content": {
      "type": "string",
      "maxLength": 5000
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
    "hashtags": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "poster_idea": {
      "type": "string",
      "maxLength": 600
    },
    "instruction": {
      "type": "string",
      "maxLength": 2000
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
{"name": "update_task", "arguments": {"task_id": "t-20261013-ab12cd34", "content": "New caption text", "hashtags": ["#burger"]}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
