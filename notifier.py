"""
Calipokehouse TikTok → Discord Notifier
----------------------------------------
Fully automated notification system that mimics Pipeline Dream's message style:
  1. Detects when Calipokehouse TikTok stream is actually live (no false positives)
  2. Posts day-specific messages for each shift (Pipeline Dream style)
  3. Detects shift changes during the continuous Thu-Mon stream
  4. Enforces "max one notification per shift per day" cooldown

Designed to run on GitHub Actions every 5 minutes.
State is persisted in `state.json` (committed back to the repo each run).
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TIKTOK_USERNAME = os.environ.get("TIKTOK_USERNAME", "calipokehouse")
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TIKTOK_LIVE_URL = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/live"
TIMEZONE = ZoneInfo("America/Los_Angeles")
STATE_FILE = Path(__file__).parent / "state.json"
SHIFTS_FILE = Path(__file__).parent / "shifts.json"
MESSAGES_FILE = Path(__file__).parent / "messages.json"

# ---------------------------------------------------------------------------
# TikTok live detection
# ---------------------------------------------------------------------------

def is_tiktok_live(username: str) -> bool:
    """
    Check if a TikTok user is currently live.

    Uses TikTok's public profile page and looks for live room indicators.
    No API key required. If TikTok changes their HTML structure, this is the
    function to update.
    """
    url = f"https://www.tiktok.com/@{username}/live"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        print(f"[warn] TikTok request failed: {e}", file=sys.stderr)
        return False

    if resp.status_code != 200:
        print(f"[warn] TikTok returned status {resp.status_code}", file=sys.stderr)
        return False

    body = resp.text

    # Live signals (TikTok embeds JSON in the page).
    #
    # Empirical findings (May 2026 inspection of live vs non-live profile pages):
    #   - '"isLiveBroadcast":true' and '"liveRoomStatus":0' appear in BOTH
    #     live and not-live pages whenever a creator has ever opened a live
    #     room. They are unreliable signals.
    #   - The actual broadcast state lives in the JSON field '"status"':
    #         2 = currently broadcasting
    #         4 = ended
    #     '"status":2' appears (twice) on a truly-live page in unambiguous
    #     live-related JSON contexts (alongside roomId, liveRoom, startTime,
    #     liveRoomStats, userCount, enterCount...). '"status":4' appears on a
    #     not-live page. They are reliable signals.
    live_signals = [
        '"status":2',
    ]
    offline_signals = [
        '"status":4',
    ]

    is_live = any(sig in body for sig in live_signals)
    is_offline = any(sig in body for sig in offline_signals)

    return is_live and not is_offline


# ---------------------------------------------------------------------------
# Shift schedule
# ---------------------------------------------------------------------------

def load_shifts() -> dict:
    with open(SHIFTS_FILE, "r") as f:
        return json.load(f)


def load_messages() -> dict:
    with open(MESSAGES_FILE, "r") as f:
        return json.load(f)


def parse_time(t: str) -> tuple[int, int]:
    """'13:00' -> (13, 0)"""
    h, m = t.split(":")
    return int(h), int(m)


def shift_window(now: datetime, day: str, start: str, end: str) -> tuple[datetime, datetime]:
    """
    Compute the absolute datetime window for a shift on a given day.
    Handles overnight shifts where end < start (rolls to next day).
    """
    days = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]
    target_weekday = days.index(day)

    days_back = (now.weekday() - target_weekday) % 7
    base_date = (now - timedelta(days=days_back)).date()

    sh, sm = parse_time(start)
    eh, em = parse_time(end)
    start_dt = datetime.combine(base_date, datetime.min.time(),
                                tzinfo=TIMEZONE).replace(hour=sh, minute=sm)
    end_dt = datetime.combine(base_date, datetime.min.time(),
                              tzinfo=TIMEZONE).replace(hour=eh, minute=em)

    # Overnight shift: end time is on the next day
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


def get_active_shift(now: datetime, shifts: dict) -> dict | None:
    """
    Find which shift is currently active based on the wall clock.
    Returns dict with keys: shift_id, day, shift_num, start, end, message_key
    or None if no shift is currently scheduled.

    When multiple shifts overlap (e.g., Sun day shift overlapping with Sun
    overnight shift's 11:40pm start), prefer the shift that started most
    recently — that's the new streamer taking over.
    """
    days = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]

    candidates = []

    # Check shifts that started today AND shifts that started yesterday
    # (overnight shifts may still be active).
    for offset in (0, 1):
        check_date = now - timedelta(days=offset)
        day_name = days[check_date.weekday()]
        day_shifts = shifts.get(day_name, [])

        for idx, shift in enumerate(day_shifts):
            start_dt, end_dt = shift_window(now, day_name,
                                            shift["start"], shift["end"])
            if start_dt <= now < end_dt:
                candidates.append({
                    "shift_id": f"{day_name}-{idx}-{start_dt.date().isoformat()}",
                    "day": day_name,
                    "shift_num": idx + 1,
                    "start": start_dt,
                    "end": end_dt,
                    "message_key": shift["message_key"],
                })

    if not candidates:
        return None

    # Prefer the most recently started shift (handles overlap at shift change)
    candidates.sort(key=lambda c: c["start"], reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "was_live": False,
        "announced_shift_ids": [],
        "last_check": None,
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def prune_old_announced_ids(state: dict, now: datetime) -> None:
    """Remove shift IDs older than 7 days to keep state.json small."""
    cutoff = (now - timedelta(days=7)).date().isoformat()
    pruned = []
    for sid in state["announced_shift_ids"]:
        # ID format: day-idx-YYYY-MM-DD (date is the last 10 chars)
        date_part = sid[-10:]
        if date_part >= cutoff:
            pruned.append(sid)
    state["announced_shift_ids"] = pruned


# ---------------------------------------------------------------------------
# Discord posting
# ---------------------------------------------------------------------------

def post_to_discord(content: str) -> None:
    payload = {
        "content": content,
        "allowed_mentions": {"parse": ["roles", "everyone"]},
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"[ok] Posted to Discord ({len(content)} chars)")
    except requests.RequestException as e:
        print(f"[error] Discord post failed: {e}", file=sys.stderr)
        sys.exit(1)


def get_message_for_shift(active_shift: dict, messages: dict) -> str:
    """Look up the right message variant for the current shift + day."""
    message_key = active_shift["message_key"]
    day = active_shift["day"]

    if message_key not in messages:
        print(f"[warn] No message group for key '{message_key}'", file=sys.stderr)
        return f"🔴 Calipokehouse is LIVE on TikTok! {TIKTOK_LIVE_URL}"

    group = messages[message_key]
    if day in group:
        return group[day]
    if "default" in group:
        return group["default"]

    print(f"[warn] No message for {message_key}/{day}", file=sys.stderr)
    return f"🔴 Calipokehouse is LIVE on TikTok! {TIKTOK_LIVE_URL}"


# ---------------------------------------------------------------------------
# Main loop (single tick)
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(TIMEZONE)
    print(f"[info] Tick at {now.isoformat()}")

    shifts = load_shifts()
    messages = load_messages()
    state = load_state()
    prune_old_announced_ids(state, now)

    is_live = is_tiktok_live(TIKTOK_USERNAME)
    active_shift = get_active_shift(now, shifts)

    if active_shift:
        print(f"[info] is_live={is_live}  active_shift="
              f"{active_shift['message_key']}/{active_shift['day']}")
    else:
        print(f"[info] is_live={is_live}  active_shift=None")

    # Decision rule (with two-tick confirmation):
    # Fire notification IFF:
    #   - Stream is currently live, AND
    #   - Stream was also live on the PREVIOUS tick (guards against TikTok
    #     transient/cached "live" indicators that can flicker even when the
    #     stream isn't actually running yet), AND
    #   - There's a scheduled shift active right now, AND
    #   - This shift hasn't been announced yet today
    was_live_previous = state.get("was_live", False)
    if is_live and active_shift:
        if active_shift["shift_id"] in state["announced_shift_ids"]:
            print(f"[info] Shift already announced today: {active_shift['shift_id']}")
        elif not was_live_previous:
            print("[info] First tick with is_live=True; waiting for next "
                  "tick to confirm before announcing.")
        else:
            message = get_message_for_shift(active_shift, messages)
            post_to_discord(message)
            state["announced_shift_ids"].append(active_shift["shift_id"])
            print(f"[info] Announced shift: {active_shift['shift_id']}")
    elif is_live and not active_shift:
        print("[warn] Stream is live but no shift is scheduled. "
              "Skipping notification.", file=sys.stderr)

    state["was_live"] = is_live
    state["last_check"] = now.isoformat()
    save_state(state)
    print("[info] Done.")


if __name__ == "__main__":
    main()
