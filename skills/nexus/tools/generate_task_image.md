# Tool: `generate_task_image`

Make the picture for a task if it has none (uses the task's image_brief; falls back to an offline brand card, so it always works). Returns the image path.

## When to use
A task has no picture yet, or the user says "make the image" / "add a poster". Instagram posts MUST have one.

## Steps
1. Get the task_id (from the tool result that created it, or list_tasks).
2. If the task has no image_brief yet, first call update_task with one (headline <= 8 words, subline, cta, style, keywords).
3. Call generate_task_image. Leave provider empty: Nexus tries AI images, Canva, stock photos, then the offline brand card.
4. Tell the user the image is on the task card for review.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "minLength": 3
    },
    "provider": {
      "type": "string",
      "enum": [
        "openai",
        "gemini",
        "xai",
        "canva",
        "stock",
        "card"
      ]
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
{"name": "generate_task_image", "arguments": {"task_id": "t-20261013-ab12cd34"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
