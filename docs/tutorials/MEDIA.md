# Images: every post gets its picture

`agent/nexus_media.py` makes sure a post never goes out without the image its platform needs.
It runs three times:

1. **Task created** (Chat, MCP, UI, API): in the background, so the picture is on the task card
   before you approve it.
2. **Approval**: if the task still has no image, it is made before the approve call returns.
3. **Scheduler, right before posting**: last check. If every provider failed, the offline brand card is
   rendered, then the post goes out. The image never makes a post fail.

An image you attached yourself (`media`) is never replaced automatically.

## Media rules

| Platform | Image | Rendered size |
|---|---|---|
| Instagram | **required** | 1080×1350 (4:5). 1080×1080 is also supported |
| Facebook | preferred | 1200×630 |
| LinkedIn | preferred | 1200×627 |
| X | optional. Added when another platform of the task needs an image, or always with `NEXUS_IMAGE_FOR_X=1` | 1600×900 |

Each platform gets its own size (`media_variants`). The scheduler hands `publish.py` the right file
for each platform (`media_by_platform`). `media[0]` is the main image (the Instagram one when Instagram is a target).

## Provider chain (`NEXUS_IMAGE_PROVIDERS`)

Default: `openai,gemini,xai,canva,stock,card`. A provider that is not configured is skipped
without making any network call. `card` is always added as the last provider.

| Provider | Needs | Notes |
|---|---|---|
| `openai` | `OPENAI_API_KEY` (`NEXUS_OPENAI_IMAGE_MODEL`, default `gpt-image-1`) | Paid per image |
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` (`NEXUS_GEMINI_IMAGE_MODEL`, default `imagen-4.0-generate-001`) | Paid per image |
| `xai` | `XAI_API_KEY` (`NEXUS_XAI_IMAGE_MODEL`, default `grok-2-image`) | Paid per image |
| `canva` | `NEXUS_CANVA_TOKEN` (Connect API access token: `design:content:read/write`, `brandtemplate:content:read`) + `NEXUS_CANVA_TEMPLATE_ID` | Autofills the template text fields `headline`, `subline`, `cta`, then exports a PNG. Brand templates and autofill need Canva Enterprise. Without the token, Canva is skipped |
| `stock` | `PEXELS_API_KEY`, `UNSPLASH_ACCESS_KEY`, `PIXABAY_API_KEY` (any one), or none | Without keys it uses the keyless [Openverse](https://api.openverse.org) API, limited to **CC0 / public-domain** images (`NEXUS_STOCK_KEYLESS=0` turns this off). The photo gets the headline, CTA and handle overlaid in the brand colours |
| `card` | nothing (Pillow) | Offline text card in the brand colours: logo (or monogram), headline auto-fitted, subline, CTA button, @handle, website |

To spend nothing on cloud images: `NEXUS_IMAGE_PROVIDERS=stock,card` (or `card`).
Other settings: `NEXUS_IMAGE_TIMEOUT=60` (seconds per HTTP call), `NEXUS_MEDIA_AUTO=0` (no image at
create/approve; the scheduler still checks before posting), and `NEXUS_CARD_FONT` / `NEXUS_CARD_FONT_BOLD` (.ttf paths).

Brand look: `data/brands/<brand>/brand.json` or `profile.json` → `colors` (`{"primary","secondary","text","accent"}`
or a list of `#RRGGBB`), `logo` (path) or a `logo.png` file in the brand folder. If no colours are set, a palette is chosen from the brand id (the same brand always gets the same palette).

## Where things are
- Files: `data/brands/<brand>/media/<task>-<platform>-<stamp>.png`
- Task fields: `media`, `media_variants`, `media_source` (`card`, `stock`, `openai`…), `image_brief`
- History event `media` (with `when` = created / approved / before_post / ui / tool, and the provider attempts) and an
  activity-log entry `task_media`

## Image brief
Brains add `image_brief` to every post: `headline` (≤ 8 words), `subline`, `cta`, `style`, `keywords` (stock search words).
Without a brief, Nexus builds one from the title and text: the first sentence becomes the headline, and the CTA comes from words like "download" or "link in bio".
Colour overrides: `bg`, `bg2`, `fg`, `accent`.

## Changing an image
- **UI**: every task card shows a preview and a **Generate image / Regenerate image** button. The dialog lets you change the text, colours, stock keywords and source. Regenerating from the UI keeps the approval as it is.
- **Brain / MCP** (`agent/nexus_tasks_mcp.py`): `generate_task_image {task_id}` and
  `regenerate_task_image {task_id, edits}`. If the model regenerates the image of an approved post, the post goes back to `pending_approval`.
- **HTTP**: `POST /tasks/<id>/image {"edits": {"headline": "...", "accent": "#FFB703", "provider": "stock"}}`
- **CLI**: `.venv/bin/python agent/nexus_media.py <task_id> --force --providers card --edit headline="New text"`

## LinkedIn note
LinkedIn posting is API-only (`LINKEDIN_ACCESS_TOKEN` + `LINKEDIN_PERSON_URN`). There is no browser
fallback for LinkedIn, so without those credentials a LinkedIn target always fails.
