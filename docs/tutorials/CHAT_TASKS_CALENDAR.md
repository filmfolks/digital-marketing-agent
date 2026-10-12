# Nexus Chat, Tasks and the Task Calendar

Tell Nexus what you want in plain words. Any brain (LM Studio or Ollama Hermes, cloud APIs,
Antigravity, or Sho) turns it into **tasks on a calendar**. You approve them, and Nexus posts
approved tasks at their time.

```
you ──▶ Nexus Chat ──▶ brain (any) ──▶ tool calls (validated) ──▶ tasks: pending_approval
                                                               │
                        you click Approve (or Approve all) ◀───┘
                                                               ▼
               relay scheduler (every 30 s) ──▶ publish.py (API first, browser fallback) ──▶ done / failed
```

## 1. Start it
```bash
./start.sh            # macOS/Linux (Windows: start.bat / start.ps1)
```
The relay starts the scheduler automatically and prints
`scheduler: on (every 30s, grace 120 min)`. Open the app (`http://127.0.0.1:8712/app/`).

## 2. Chat page (Nexus Chat)
1. Choose a brain in the dropdown. **Brain from Settings** uses whatever is selected in
   Settings. You can also pick LM Studio, Ollama, Antigravity, Sho or OpenRouter (Hermes 4)
   directly.
2. Type, for example, `plan 3 Instagram posts for next week for our burger launch`.
3. You'll see live progress (`Running plan_campaign…`), then one **card per task**:
   - **Approve**: the task can be posted at its time. If you changed the date/time box first,
     that time is saved along with the approval.
   - **Edit**: change the text, poster idea, platforms or time.
   - **Schedule**: save the date/time from the box.
   - **Cancel**.
   - **Approve all N**: appears when the brain created several tasks.
4. Nothing is posted from the chat. The brain has no tool that can post or approve.

Example prompts:
- `Write an X post for tomorrow 9am about our weekend offer`
- `Plan the campaign for this whole month on Instagram and Facebook`
- `What is waiting for my approval?`
- `Move the Wednesday post to Thursday 6pm`
- `Cancel the post about the new menu`

## 3. Task Calendar page
- Switch between **Month** and **Week** views, and use ◀ ▶ / **Today** to move around.
- **Drag** a post to another day. The time stays the same.
- **Click** a post to open its card (Approve, Edit, Schedule, Cancel).
- **Approve visible** approves every `pending_approval` task in the current view, after a
  confirmation.
- **.ics** downloads the calendar for Apple Calendar, Google Calendar or Outlook. To subscribe,
  use `http://127.0.0.1:8712/calendar.ics?token=<relay token>`. Treat that URL like a password.
- Times use your timezone: `NEXUS_TZ`, otherwise `data/settings.json` →
  `{"timezone": "Asia/Kolkata"}`, otherwise **Asia/Kolkata**. On Windows, run
  `pip install tzdata` if you use a zone other than Asia/Kolkata or UTC.

## 4. Tasks page
A filterable list (Needs approval, Approved, Posted, Failed, Draft, Cancelled) with the same
cards. **New task** adds a draft by hand.

## 5. Statuses
| Status | Meaning |
|---|---|
| `draft` | Hand-made, not ready |
| `pending_approval` | Created by a brain (or you) and waiting for you |
| `approved` | You approved it. The scheduler posts it at `scheduled_at` |
| `running` | Being posted right now |
| `done` | Posted. Results are stored on the task |
| `failed` | The error is shown on the card. Fix it, then approve again |
| `cancelled` | Won't run |

Rules:
- A task can't be approved without a date/time, platforms and text.
- If a brain edits an approved post, it goes back to `pending_approval`. Rescheduling keeps the
  approval.

**Auto-approve (opt-in):** `POST /campaigns/<id>/auto-approve {"value": true}` marks a campaign
so that new tasks the brain adds to it start as `approved`. Only a human can set this (the
model can't), and it's off by default.

## 6. Scheduler safety
- It only posts `approved` tasks whose time has come.
- `approved → running` is an atomic compare-and-set under a file lock, so two ticks or two relays
  can never post the same task. A second lock (`data/tasks/scheduler.lock`) keeps one scheduler
  per data folder.
- **Relay was off?** Tasks that became due within the grace window (`NEXUS_SCHEDULER_GRACE_MIN`,
  default 120 minutes) still run. Older ones are not posted late. They become `failed` with the
  error "missed…", and you reschedule them.
- **Crash while posting?** At the next start, a task left in `running` becomes `failed` with the
  error "interrupted — check the platform before retrying". It is not retried, because the post
  may already be live.
- Settings:
  - `NEXUS_SCHEDULER=0` (or `--no-scheduler`) turns the scheduler off.
  - `NEXUS_SCHEDULER_DRY_RUN=1` only previews (`publish.py --dry-run`) and changes nothing.
  - `POST /scheduler/tick {"dry_run": true}` previews on demand.
- Every change is written to the activity log (`task_created`, `task_approved`, `task_done`, …).

## 7. Relay endpoints
| Method | Path | Body or query |
|---|---|---|
| POST | `/nexus/chat` (SSE) | `{message, history?, brain: {provider, model?, base_url?, mode?, api_key?}, brand?}` → events `meta`, `tool_call`, `tool_result`, `task`, `warning`, `text`, `done`, `error` |
| GET | `/tasks` | `?status=a,b&brand=&campaign_id=&start=&end=` |
| GET | `/tasks/<id>` | |
| POST | `/tasks` | `{title, content, platforms, scheduled_at, …, status?: draft\|pending_approval}` |
| POST | `/tasks/<id>/update` | any of `title content platforms scheduled_at media hashtags poster_idea notes` |
| POST | `/tasks/<id>/approve` | `{scheduled_at?}` |
| POST | `/tasks/<id>/unapprove`, `/tasks/<id>/cancel` | |
| POST | `/tasks/approve-all` | `{ids?: [...], campaign_id?}` |
| GET | `/campaigns` | |
| POST | `/campaigns/<id>/auto-approve` | `{value: bool}` |
| GET | `/calendar` | `?start=YYYY-MM-DD&end=YYYY-MM-DD&brand=` (grouped by day) |
| GET | `/calendar.ics` | `?token=` allowed |
| GET | `/nexus/tools` | the action schema |
| GET | `/nexus/brain-status` | `?provider=lmstudio` → reachable + loaded model |
| GET | `/scheduler` / POST `/scheduler/tick` | status / run now (`dry_run` defaults to true) |

All of these use the relay's token (`X-Nexus-Token`) and origin rules.

## 8. From the terminal
```bash
python3 agent/nexus_chat.py "plan 3 Instagram posts for next week" --brain lmstudio
python3 agent/nexus_tasks.py list --status pending_approval
python3 agent/nexus_tasks.py approve <task-id>
python3 agent/nexus_tasks.py ics > nexus.ics
```

## 9. Where data lives
`data/tasks/tasks.json`, written atomically and git-ignored. Tests use a temporary folder
(`NEXUS_DATA_DIR`).
