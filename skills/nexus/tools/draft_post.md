# Tool: `draft_post`

Write one social post and save it as a pending_approval task (optionally scheduled). Nothing is published.

## When to use
The user wants ONE post ("write a post", "tweet about…", "an Instagram post for Friday").

## Steps
1. If the brand voice is unknown, call get_brand_profile first.
2. Pick the platform(s) the user named; default to the brand's main platform.
3. Write the caption for that platform (X ≤ 280 chars incl. hashtags).
4. Add 3–8 hashtags: broad + niche + local SEO.
5. Add a one-sentence poster_idea.
6. Set scheduled_at only if the user gave a day/time or asked to schedule it.
7. Call draft_post once. Tell the user it awaits approval.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
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
      },
      "minItems": 1
    },
    "caption": {
      "type": "string",
      "minLength": 1,
      "maxLength": 5000
    },
    "hashtags": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "maxItems": 30
    },
    "poster_idea": {
      "type": "string",
      "maxLength": 600
    },
    "scheduled_at": {
      "type": "string",
      "description": "ISO 8601 local date-time, e.g. 2026-10-20T18:30 (user timezone if no offset)"
    },
    "title": {
      "type": "string",
      "maxLength": 120
    },
    "brand": {
      "type": "string"
    }
  },
  "required": [
    "platforms",
    "caption"
  ],
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "draft_post", "arguments": {"platforms": ["instagram"], "caption": "Double smash, double cheese, zero regrets. Our new Midnight Melt drops Friday — who's in? 🍔", "hashtags": ["#burger", "#smashburger", "#bangalorefood", "#midnightmelt"], "poster_idea": "Close-up of the burger with dripping cheese on a dark background, neon text 'MIDNIGHT MELT'.", "scheduled_at": "2026-10-23T19:00"}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
