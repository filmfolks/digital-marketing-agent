# Tool: `research_brand`

Return what Nexus already knows about the brand: saved website/Google-profile research and the profile summary. Use before planning.

## When to use
Before planning a month or when the user asks what we know about the brand.

## Steps
1. Call it once; use the result for pillars, offers, local keywords.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
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
{"name": "research_brand", "arguments": {}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
