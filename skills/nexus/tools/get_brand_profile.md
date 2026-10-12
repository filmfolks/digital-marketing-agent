# Tool: `get_brand_profile`

Get the brand profile (voice, audience, pillars, hashtags, guardrails) of the active or named brand.

## When to use
You need voice, audience, pillars or hashtags before writing.

## Steps
1. Call with no arguments for the active brand, or brand=<id>.

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
{"name": "get_brand_profile", "arguments": {}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
