# Tool: `regenerate_task_image`

Redo a task's picture with changes the user asked for (new headline/subline/CTA text, colours, style, stock keywords or source). Regenerating the image of an approved post sends it back for approval.

## When to use
The user wants the picture changed: other text, colours, style, a photo instead of a card, or "try again".

## Steps
1. Get the task_id.
2. Put ONLY what changes in edits: headline / subline / cta (text), bg / fg / accent (#RRGGBB colours), style, keywords, provider.
3. "Make it blue" → edits.bg = "#1E3A8A". "Use a photo" → edits.provider = "stock". "Plain text card" → edits.provider = "card".
4. Call regenerate_task_image once; if it was approved, say it needs approval again.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "minLength": 3
    },
    "edits": {
      "type": "object",
      "description": "What to change on the image. Only send what changes.",
      "properties": {
        "headline": {
          "type": "string",
          "maxLength": 80
        },
        "subline": {
          "type": "string",
          "maxLength": 160
        },
        "cta": {
          "type": "string",
          "maxLength": 30
        },
        "style": {
          "type": "string",
          "maxLength": 300
        },
        "keywords": {
          "type": "array",
          "items": {
            "type": "string"
          },
          "maxItems": 6
        },
        "bg": {
          "type": "string",
          "pattern": "^#[0-9a-fA-F]{6}$",
          "description": "Background colour #RRGGBB"
        },
        "bg2": {
          "type": "string",
          "pattern": "^#[0-9a-fA-F]{6}$",
          "description": "Second gradient colour"
        },
        "fg": {
          "type": "string",
          "pattern": "^#[0-9a-fA-F]{6}$",
          "description": "Text colour"
        },
        "accent": {
          "type": "string",
          "pattern": "^#[0-9a-fA-F]{6}$",
          "description": "Button/accent colour"
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
          ],
          "description": "Force one source; 'card' = offline text card (always works)"
        }
      },
      "additionalProperties": false
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
{"name": "regenerate_task_image", "arguments": {"task_id": "t-20261013-ab12cd34", "edits": {"headline": "Bookmarks that find themselves", "accent": "#FFB703"}}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
