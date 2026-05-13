# KoiSlabs TikTok → Discord Notifier

Automated TikTok-to-Discord notification system for the [@koislabs](https://www.tiktok.com/@koislabs/live) channel. Built on the same architecture as the Calipokehouse notifier — same script, same Discord channel, different streams/messages.

## How it works

Every 10 minutes (via cron-job.org pinging GitHub's workflow_dispatch API), the script:
1. Checks if TikTok reports `@koislabs` as actually broadcasting (`"status":2` signal — not just having a live room open)
2. Checks if a scheduled shift is currently active per `shifts.json`
3. Requires `is_live=True` on two consecutive ticks (guards against TikTok flickers)
4. Posts the matching day-specific message to Discord
5. Records the shift announcement so it only fires once per shift per day

## Schedule

| Shift | Hours (PST) | Streamers |
|---|---|---|
| Day shift | 10:00 AM – 7:00 PM | Mon: Sahara & Presley · Tue–Fri: Kozy & Matthew · Sat–Sun: Presley & Sahara |
| Overnight shift | 7:00 PM – 5:00 AM | Mon: Adonis · Tue–Fri: Scooby · Sat–Sun: Adonis |

Both shifts run every day of the week.

## Files

| File | Purpose |
|---|---|
| `notifier.py` | Main script (identical to calipokehouse-notifier) |
| `shifts.json` | Shift schedule — hours and streamers |
| `messages.json` | All Discord messages (edit these to change wording) |
| `state.json` | Auto-managed; tracks what's been announced |
| `requirements.txt` | Python deps |
| `.github/workflows/notifier.yml` | GitHub Actions cron + workflow_dispatch entry point |

## Required secrets

In Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL (same channel as Calipokehouse) |
| `TIKTOK_USERNAME` | `koislabs` |

## Required permissions

Settings → Actions → General → Workflow permissions → **Read and write permissions** (so the bot can commit updated `state.json` back).

## Maintenance

- **Edit messages:** open `messages.json` on GitHub, hit the pencil, change text, commit. Next 10-min tick uses the new wording.
- **Edit schedule:** open `shifts.json`, change hours or message_key references, commit.
- **Health check:** Actions tab should show green checkmarks roughly every 10 minutes via cron-job.org.
