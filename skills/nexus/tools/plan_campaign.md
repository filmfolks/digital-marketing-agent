# Tool: `plan_campaign`

Plan a campaign (e.g. a whole month): returns many dated posts with captions, SEO hashtags and poster ideas. Every post becomes a pending_approval task.

## When to use
Several posts: "plan the month", "3 posts for next week", "a launch campaign".

## Steps
1. Call research_brand (or get_brand_profile) once if you have not seen the brand yet.
2. Decide the date range (whole month = first to last day; next week = Monday–Sunday).
3. Spread posts evenly; no two posts on one platform at the same time.
4. For EACH post: scheduled_at, platforms, caption, hashtags, poster_idea, short title.
5. Put ALL posts in ONE plan_campaign call (max 62).
6. Answer with a 1–3 line summary; the user approves on the calendar.

## Arguments (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 120
    },
    "goal": {
      "type": "string",
      "maxLength": 500
    },
    "brand": {
      "type": "string"
    },
    "start": {
      "type": "string",
      "description": "YYYY-MM-DD"
    },
    "end": {
      "type": "string",
      "description": "YYYY-MM-DD"
    },
    "posts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "scheduled_at": {
            "type": "string",
            "description": "ISO 8601 local date-time, e.g. 2026-10-20T18:30 (user timezone if no offset)"
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
            },
            "minItems": 1
          },
          "title": {
            "type": "string",
            "maxLength": 120
          },
          "caption": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2200,
            "description": "Final post text"
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
            "maxLength": 600,
            "description": "Visual / poster concept"
          }
        },
        "required": [
          "scheduled_at",
          "platforms",
          "caption"
        ]
      },
      "minItems": 1,
      "maxItems": 62
    }
  },
  "required": [
    "name",
    "posts"
  ],
  "additionalProperties": false
}
```

## Example call
```
<tool_call>
{"name": "plan_campaign", "arguments": {"name": "Burger week", "goal": "Launch the Midnight Melt", "start": "2026-10-19", "end": "2026-10-25", "posts": [{"scheduled_at": "2026-10-19T19:00", "platforms": ["instagram"], "title": "Teaser", "caption": "Something cheesy is coming Friday… 👀", "hashtags": ["#burger", "#comingsoon"], "poster_idea": "Silhouette of a burger under a sheet, 'FRIDAY' in neon."}, {"scheduled_at": "2026-10-23T19:00", "platforms": ["instagram", "facebook"], "title": "Launch", "caption": "Midnight Melt is HERE. Double smash, double cheese. Tag your burger buddy!", "hashtags": ["#smashburger", "#newlaunch"], "poster_idea": "Hero shot with cheese pull."}]}}
</tool_call>
```

## Never
- Never say it was posted: approval and posting happen in the app.
- Never invent task ids; get them from list_tasks/get_calendar.
