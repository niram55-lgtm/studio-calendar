# Studio Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-user Flask web app that scrapes BoostApp studio schedules, syncs selected trainers' classes to each user's Google Calendar, serves per-user Apple-subscribable `.ics` feeds, and pushes ntfy.sh notifications on cancellations / changes.

**Architecture:** Flask + SQLite + SQLAlchemy ORM + APScheduler single-process. Google Sign-in is the identity *and* the Calendar permission. Scraper uses `fa-user` / `fa-map-marker` icon anchors in BoostApp HTML. Diff uses a *loose UID* (date + trainer + class_type) so that moved classes read as "changed" rather than "removed + added", while *strict UID* names the Google event. Deployment mode (`local` vs `cloud`) is a config switch that governs host binding and the Google OAuth redirect URI only.

**Tech Stack:** Python 3.11+, Flask 3, SQLAlchemy 2, BeautifulSoup 4, `icalendar` 5, `google-api-python-client`, `google-auth-oauthlib`, APScheduler 3, `cryptography` (Fernet), PyYAML, ntfy.sh HTTP API.

**Spec:** [docs/superpowers/specs/2026-10-30-studio-calendar-design.md](../specs/2026-10-30-studio-calendar-design.md)

## Global Constraints

- Python 3.11+.
- All HTTP responses to/from BoostApp decoded explicitly as UTF-8; all Hebrew strings handled as `str`, never `bytes.decode('latin-1')`.
- Timezone for every datetime that reaches storage, ICS, Google or ntfy: `Asia/Jerusalem` (via `zoneinfo.ZoneInfo`, never fixed offset).
- Google OAuth scopes exactly: `openid email profile https://www.googleapis.com/auth/calendar`.
- Google refresh tokens and access tokens persisted only Fernet-encrypted with key `FERNET_KEY` from env; plaintext never written to DB or logs.
- `feed_token` and `ntfy_topic` are generated with `secrets.token_hex(16)` — 32 hex chars, unique.
- Google event UID = `sha1(date|time|trainer|class_type|room).hexdigest() + '@studio-calendar'` (strict UID).
- Diff identity = `sha1(date|trainer|class_type).hexdigest()` (loose UID).
- UI is Hebrew RTL (`<html dir="rtl" lang="he">`).
- Dedicated Google calendar name defaults to `סטודיו נופר`, overridable per user via `user_settings.studio_display_name`.
- Default class duration 55 minutes; default `urgent_window_hours` 24; default `weeks_ahead` 1 (= current + next).
- APScheduler runs on `Asia/Jerusalem`; daily sync 06:00, daily digest 06:30.
- Dev server must run with `use_reloader=False` so APScheduler is not duplicated.
- No destructive git operations in any task; each task ends with a single new commit.

## Review Focus

Five input classes the spec implies but no task's happy-path test exercises; each gets its own test added to the task that owns the code.

1. **Hebrew whitespace artifacts in trainer names** (BoostApp uses tabs + non-breaking spaces inside the `<p>`): parsed trainer name must equal the clean Hebrew string, trimmed. → Test added to Task 3 (scraper).
2. **BoostApp returns HTML with no classes** (e.g. holiday week): `fetch_schedule` must return `[]` without crashing, and sync must leave the user's calendar untouched (no mass-delete). → Test added to Task 3 and Task 7 (google sync).
3. **Google refresh token expired / revoked**: sync must mark `needs_reauth=True` on that user, skip them, log, and continue syncing other users — not crash the scheduler. → Test added to Task 7.
4. **Same change surfaced twice by two sync runs within 24 h**: `notifications_log` keeps the second push from firing. → Test added to Task 8 (notifications).
5. **DST transition weekend**: ICS event for a class on the Friday after Israel's DST change renders in the viewer's calendar at the correct wall-clock time (via VTIMEZONE, not fixed offset). → Test added to Task 4 (ICS).

---

## File Structure

Created files (every task names which of these it owns):

```
C:\Users\Nir Amor\Documents\studio-calendar\
├── app.py                   # Flask app factory, routes, session, startup
├── auth.py                  # Google OAuth flow + user identity
├── scraper.py               # BoostApp HTML → list[ClassItem]
├── calendar_ics.py          # list[ClassItem] → ICS bytes
├── calendar_google.py       # list[ClassItem] + user_id → Google events + SyncDiff
├── notifications.py         # SyncDiff → ntfy pushes + pending_digest; send_digest
├── scheduler.py             # APScheduler jobs: sync_all_users, send_all_digests
├── db.py                    # engine, Session, init_db()
├── models.py                # SQLAlchemy models
├── crypto.py                # Fernet encrypt/decrypt helpers
├── config.py                # YAML + .env loader, Config dataclass
├── config.yaml
├── config.example.yaml
├── .env.example
├── .gitignore
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── login.html
│   └── index.html
├── static/
│   ├── style.css
│   └── app.js
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── boost_week_2026-02-02.html   # copied from the real page
│   ├── test_scraper.py
│   ├── test_ics.py
│   ├── test_diff.py
│   ├── test_notifications.py
│   ├── test_scheduler.py
│   └── test_app_routes.py
└── README.md
```

---

## Task 1: Project scaffolding & config loader

**Files:**
- Create: `requirements.txt`, `.gitignore`, `.env.example`, `config.yaml`, `config.example.yaml`, `config.py`
- Test: `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `config.load_config(path: str | None = None) -> Config`
  - `Config` dataclass with fields: `deployment_mode: Literal['local','cloud']`, `base_url: str`, `host: str`, `port: int`, `secret_key: str`, `fernet_key: bytes`, `google_client_id: str`, `google_client_secret: str`, `defaults: Defaults`, `scheduler: SchedulerCfg`, `boostapp: BoostAppCfg`, `ntfy: NtfyCfg`.
  - `Defaults` fields: `timezone: str`, `class_duration_minutes: int`, `weeks_ahead: int`, `urgent_window_hours: int`, `digest_time: str`.

- [ ] **Step 1: Write `tests/conftest.py`** that sets env vars for `SECRET_KEY=test`, `FERNET_KEY=<fixed valid key>`, `GOOGLE_CLIENT_ID=test`, `GOOGLE_CLIENT_SECRET=test` for every test session.

- [ ] **Step 2: Write failing tests in `tests/test_config.py`:**
  - `test_load_config_reads_yaml_defaults`: loads `config.example.yaml`, asserts `defaults.class_duration_minutes == 55`, `defaults.weeks_ahead == 1`, `defaults.urgent_window_hours == 24`, `scheduler.daily_sync_hour == 6`, `scheduler.daily_sync_minute == 0`.
  - `test_load_config_overrides_base_url_from_env`: setting `BASE_URL=https://x.example` overrides `base_url`.
  - `test_load_config_rejects_missing_fernet_key`: raises `RuntimeError` when `FERNET_KEY` missing.

- [ ] **Step 3: Run tests to verify they fail.** `pytest tests/test_config.py -v` → ImportError.

- [ ] **Step 4: Write `requirements.txt`** with exact pins listed in spec §12.

- [ ] **Step 5: Write `config.example.yaml`** matching spec §7 exactly (deployment_mode, base_url, host, port, defaults, scheduler, boostapp, ntfy).

- [ ] **Step 6: Write `config.yaml`** as a copy of `config.example.yaml` (dev defaults).

- [ ] **Step 7: Write `.env.example`** listing `SECRET_KEY`, `FERNET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BASE_URL` (optional override).

- [ ] **Step 8: Write `.gitignore`:** `__pycache__/`, `*.pyc`, `.env`, `studio.db`, `studio.db-*`, `.venv/`, `.pytest_cache/`.

- [ ] **Step 9: Implement `config.py`.** `load_config()` reads YAML, overlays env vars (`SECRET_KEY`, `FERNET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BASE_URL`), returns a `Config` dataclass. Raise `RuntimeError` on missing required env.

- [ ] **Step 10: Run tests to verify they pass.** `pytest tests/test_config.py -v`.

- [ ] **Step 11: Commit.**
  ```bash
  git init
  git add .
  git commit -m "chore: scaffold project and config loader"
  ```

---

## Task 2: DB models, engine, Fernet helpers

**Files:**
- Create: `models.py`, `db.py`, `crypto.py`
- Test: `tests/test_db.py`, `tests/test_crypto.py`

**Interfaces:**
- Consumes: `Config` from Task 1.
- Produces:
  - `db.get_engine(url: str) -> Engine`, `db.SessionLocal` (sessionmaker), `db.init_db(engine)` creates tables.
  - `crypto.encrypt(plaintext: str) -> bytes`, `crypto.decrypt(ciphertext: bytes) -> str` using `FERNET_KEY`.
  - SQLAlchemy models matching spec §4 exactly: `User`, `GoogleToken`, `UserSettings`, `Trainer`, `ClassSnapshot`, `PendingDigest`, `NotificationsLog`, `ScrapeCache`. Column names and types match spec §4 verbatim.

- [ ] **Step 1: Write failing tests in `tests/test_crypto.py`:**
  - `test_round_trip`: `decrypt(encrypt("שלום")) == "שלום"`.
  - `test_decrypt_wrong_key_raises`: encrypting with one key and decrypting with another raises `InvalidToken`.

- [ ] **Step 2: Write failing tests in `tests/test_db.py`:**
  - `test_init_db_creates_all_tables`: after `init_db`, inspector lists all 9 tables from spec §4.
  - `test_user_settings_cascade_delete`: deleting a user cascades to `user_settings`, `trainers`, `class_snapshots`, `google_tokens`.
  - `test_scrape_cache_pk_is_studio_url_plus_week_start`.
  - `test_trainers_unique_per_user`: inserting two rows with same `(user_id, trainer_name)` raises `IntegrityError`.

- [ ] **Step 3: Run tests to verify they fail.**

- [ ] **Step 4: Implement `crypto.py`** with module-level `_fernet()` that reads `FERNET_KEY` lazily on first call.

- [ ] **Step 5: Implement `models.py`.** Declarative base; all 9 tables matching spec §4. Use `sqlalchemy.types.DateTime(timezone=True)`. `BLOB` columns use `sa.LargeBinary`. All FKs `ondelete='CASCADE'` where spec says so.

- [ ] **Step 6: Implement `db.py`.** `get_engine(url)` returns a SQLite engine with `connect_args={'check_same_thread': False}` and `foreign_keys=ON` pragma. `init_db(engine)` creates all tables.

- [ ] **Step 7: Run tests to verify they pass.**

- [ ] **Step 8: Commit.**
  ```bash
  git add models.py db.py crypto.py tests/test_db.py tests/test_crypto.py
  git commit -m "feat: db models, engine, and fernet helpers"
  ```

---

## Task 3: BoostApp scraper

**Files:**
- Create: `scraper.py`, `tests/fixtures/boost_week_2026-02-02.html`
- Test: `tests/test_scraper.py`

**Interfaces:**
- Consumes: `Config.boostapp`.
- Produces:
  - `scraper.ClassItem` dataclass: `date: date`, `time: time`, `trainer: str`, `class_type: str`, `room: str`, `source: Literal['live','cache-fresh','cache-stale']`.
  - `scraper.fetch_schedule(studio_url: str, weeks_ahead: int = 1, *, session: Session, now: datetime | None = None, http: requests.Session | None = None) -> list[ClassItem]` — returns deduped list sorted by `(date, time)`.
  - `scraper.parse_schedule_html(html: str) -> list[ClassItem]` — pure function; no network, no DB.
  - `scraper.extract_studio_id(link_or_id: str) -> str` — accepts a full BoostApp URL or a bare id; returns the id. Raises `ValueError` when input is empty or missing `StudioUrl`.

The fixture is the real HTML already captured at `C:\Users\Nir Amor\.claude\projects\...\tool-results\boost_raw.html` — copy that file into `tests/fixtures/boost_week_2026-02-02.html` as the first step of this task.

- [ ] **Step 1: Copy fixture.** `cp "<captured raw html path>" tests/fixtures/boost_week_2026-02-02.html` (in the plan author's machine this is `C:\Users\Nir Amor\.claude\projects\C--Users-Nir-Amor--claude\8b935ad9-1f5e-4c3c-8563-f7756128dce8\tool-results\boost_raw.html`).

- [ ] **Step 2: Write failing tests in `tests/test_scraper.py`:**
  - `test_extract_studio_id_from_url`: full link → `"636a5a359ccfa"`.
  - `test_extract_studio_id_from_bare_id`: `"636a5a359ccfa"` → `"636a5a359ccfa"`.
  - `test_extract_studio_id_rejects_empty`: raises `ValueError`.
  - `test_parse_schedule_counts_days`: fixture yields exactly 8 distinct dates (02/02–09/02/2026).
  - `test_parse_schedule_first_class_monday_6am`: a class with `date=2026-02-02, time=06:00, trainer="אור כץ", class_type="Body Pump", room="POWER"` is in the output.
  - `test_parse_schedule_trainer_name_trimmed`: for all parsed items, `item.trainer == item.trainer.strip()` and contains no tab or non-breaking space (`\u00A0`).  **[Review Focus #1]**
  - `test_parse_schedule_handles_h5_with_anchor`: a class whose `<h5>` wraps `<a class="ClickClass">...</a>` yields the clean class text (e.g. `"Body Pump"`), no HTML.
  - `test_parse_schedule_empty_week_returns_empty`: parsing an HTML with no `<h4>` day headers returns `[]`. **[Review Focus #2]**
  - `test_fetch_schedule_uses_cache_within_ttl` (uses a stub `http` + in-memory session): second call within the TTL does not call `http.get`.
  - `test_fetch_schedule_stale_cache_on_network_failure`: when `http.get` raises, `fetch_schedule` returns items with `source='cache-stale'` if cache exists, else `[]`.
  - `test_fetch_schedule_dedup_across_weeks`: `weeks_ahead=1` with overlapping day in both pages yields each strict identity once.

- [ ] **Step 3: Run tests to verify they fail.**

- [ ] **Step 4: Implement `scraper.extract_studio_id`.** `urllib.parse.urlparse` + `parse_qs`; bare id = passes `[0-9a-f]{10,}` check.

- [ ] **Step 5: Implement `scraper.parse_schedule_html`.** Walk `soup.select('table.tableListClass')` tables whose `<h4>` matches `יום \S+ \d{2}/\d{2}/\d{4}`. For each matching day, iterate rows of the following `<tbody>` whose `<tr>` is not `trAbout*` / `trGuide*`. First `<td> <h5>` = `HH:MM`. Second `<td>`: `<h5>` text (strip inner tags) = class_type; `<p>` → split on `i.fa` icons: text immediately after `i.fa.fa-user` = trainer, text after `i.fa.fa-map-marker` = room. `.strip()` each; collapse any run of whitespace (tabs, `\xa0`, newlines) to a single space inside the trainer.

- [ ] **Step 6: Implement `scraper.fetch_schedule`.**
  - Compute Sunday of current week in `Asia/Jerusalem` (ISO weekday 7 → Sunday). For `w in range(weeks_ahead + 1)`: build `week_start = current_sunday + 7*w`. Format `MM/DD` where MM = `week_start + 1 day` (Monday — the first class-day). *Actually use `week_start.strftime('%m/%d')` for the Monday the datepicker expects; see note.*
  - For each `week_start`, check `scrape_cache`: if fresh (within `boostapp.cache_ttl_hours` from config), parse cached HTML. Else GET `{boostapp.base_url}?datepicker={mm}%2F{dd}&StudioUrl={studio_url}` with `User-Agent` from config, `response.encoding='utf-8'`, parse, upsert cache row.
  - On `requests.RequestException`: return cached rows marked `source='cache-stale'` if any, else `[]`.
  - Dedup by `strict_uid` (date|time|trainer|class_type|room), keep first. Sort by `(date, time, trainer)`.

- [ ] **Step 7: Run tests to verify they pass.**

- [ ] **Step 8: Commit.**
  ```bash
  git add scraper.py tests/test_scraper.py tests/fixtures/boost_week_2026-02-02.html
  git commit -m "feat: BoostApp scraper with icon-anchored parsing"
  ```

---

## Task 4: ICS generator

**Files:**
- Create: `calendar_ics.py`
- Test: `tests/test_ics.py`

**Interfaces:**
- Consumes: `scraper.ClassItem`.
- Produces:
  - `calendar_ics.strict_uid(c: ClassItem) -> str` and `calendar_ics.loose_uid(c: ClassItem) -> str`.
  - `calendar_ics.build_ics(classes: list[ClassItem], *, calendar_name: str = 'סטודיו נופר', duration_minutes: int = 55) -> bytes` — valid iCalendar, UTF-8, with VTIMEZONE for Asia/Jerusalem.

- [ ] **Step 1: Write failing tests in `tests/test_ics.py`:**
  - `test_strict_uid_stable`: same `ClassItem` → same UID across two calls, both end with `@studio-calendar`.
  - `test_loose_uid_ignores_time_and_room`: two items identical except time differ → same `loose_uid`.
  - `test_build_ics_contains_vtimezone_jerusalem`: output bytes contain `BEGIN:VTIMEZONE` and `TZID:Asia/Jerusalem`.
  - `test_build_ics_hebrew_round_trip`: an event with trainer `"שירן אמויאל"` can be reparsed by `icalendar.Calendar.from_ical` and `summary` equals `"Body Pump — שירן אמויאל"`.
  - `test_build_ics_event_summary_location_description`:
    - SUMMARY = `"{class_type} — {trainer}"`
    - LOCATION = `"סטודיו נופר — {room}"`
    - DESCRIPTION = `"{class_type} | {trainer} | אולם {room}"`
  - `test_build_ics_duration_55_minutes`: `DTEND - DTSTART == timedelta(minutes=55)`.
  - `test_build_ics_dst_transition`: event on 2026-03-28 (Israeli DST start) 18:00 renders with `DTSTART;TZID=Asia/Jerusalem:20260328T180000` and the reparsed `dtstart.utcoffset()` equals the offset in effect that moment (+03:00). **[Review Focus #5]**

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `calendar_ics.py`.** `strict_uid` and `loose_uid` as spec §5.2. `build_ics` creates `icalendar.Calendar`, adds `PRODID`, `VERSION:2.0`, a VTIMEZONE block for Asia/Jerusalem (standard + daylight components with the Israeli rules), then one `VEVENT` per class. Serialize `cal.to_ical()`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.**
  ```bash
  git add calendar_ics.py tests/test_ics.py
  git commit -m "feat: iCalendar generator with Asia/Jerusalem VTIMEZONE"
  ```

---

## Task 5: Google OAuth flow (`auth.py`)

**Files:**
- Create: `auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Config`, `models.User`, `models.GoogleToken`, `models.UserSettings`, `crypto.encrypt`.
- Produces:
  - `auth.build_authorize_url(config: Config, state: str) -> str`
  - `auth.exchange_code(config: Config, code: str) -> GoogleAuthResult` where result has `google_sub`, `email`, `name`, `access_token`, `refresh_token`, `expiry: datetime`.
  - `auth.upsert_user_from_auth(session, result: GoogleAuthResult) -> User` — creates user + google_tokens (encrypted) + user_settings (with fresh `feed_token` and `ntfy_topic`) on first login; updates tokens on return login.
  - `auth.load_credentials(session, user_id) -> google.oauth2.credentials.Credentials` — returns credentials with refresh capability; re-encrypts updated tokens via a `token_updater` callback.

- [ ] **Step 1: Write failing tests in `tests/test_auth.py`:**
  - `test_build_authorize_url_contains_scopes_and_redirect`: URL contains `scope=openid+email+profile+...calendar` (URL-encoded), `redirect_uri=` matching `{base_url}/auth/callback`, and `state=<given>`.
  - `test_upsert_user_creates_settings_with_tokens` (with a stub `GoogleAuthResult`): inserts `User`, `GoogleToken` (encrypted blobs decrypt to given plaintext), and `UserSettings` with 32-hex `feed_token` and `ntfy_topic`.
  - `test_upsert_user_second_login_reuses_settings`: calling twice does not create a second `UserSettings`; refresh/access tokens are updated.
  - `test_load_credentials_decrypts_and_returns_valid_object`: returns a `google.oauth2.credentials.Credentials` whose `refresh_token` equals the plaintext stored.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `auth.py`.**
  - `build_authorize_url` uses `google_auth_oauthlib.flow.Flow.from_client_config(...)`.
  - `exchange_code` runs the Flow's `fetch_token`, then decodes id_token with `google.oauth2.id_token.verify_oauth2_token` to get `sub`, `email`, `name`.
  - `upsert_user_from_auth` does a `SELECT ... WHERE google_sub=:sub FOR UPDATE` (plain SELECT on SQLite); on miss creates `User` + `UserSettings` with `secrets.token_hex(16)` for both tokens; always upserts `GoogleToken` with `crypto.encrypt`.
  - `load_credentials` returns `Credentials(token=..., refresh_token=..., token_uri='https://oauth2.googleapis.com/token', client_id=..., client_secret=..., scopes=[...])`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.**
  ```bash
  git add auth.py tests/test_auth.py
  git commit -m "feat: Google OAuth flow and user upsert"
  ```

---

## Task 6: Diff engine

**Files:**
- Create: part of `calendar_google.py` — functions below. (Full module completes in Task 7.)
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `scraper.ClassItem`, `models.ClassSnapshot`.
- Produces:
  - `calendar_google.SyncDiff` dataclass: `added: list[ClassItem]`, `removed: list[ClassSnapshot]`, `changed: list[tuple[ClassSnapshot, ClassItem]]`.
  - `calendar_google.compute_diff(old: list[ClassSnapshot], new: list[ClassItem]) -> SyncDiff` — pure function.

- [ ] **Step 1: Write failing tests in `tests/test_diff.py`:**
  - `test_diff_added`: old empty, new has 2 items → `added==new`, others empty.
  - `test_diff_removed`: new empty, old has 2 items → `removed==old`, others empty.
  - `test_diff_time_change_is_changed_not_removed_and_added`: same (date, trainer, class_type), different time → one `changed` entry, nothing in `added`/`removed`.
  - `test_diff_room_change_is_changed`: same loose_uid, different room → `changed`.
  - `test_diff_trainer_change_is_removed_plus_added`: different trainers → different loose_uids, so counts as removed + added.
  - `test_diff_unchanged_item_not_in_any_bucket`: identical items in both lists → empty diff.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `compute_diff`.** Build `old_map = {s.loose_uid: s for s in old}` and `new_map = {calendar_ics.loose_uid(c): c for c in new}`. From spec §5.3:
  ```
  added    = [c for uid, c in new_map.items() if uid not in old_map]
  removed  = [s for uid, s in old_map.items() if uid not in new_map]
  changed  = [(old_map[uid], new_map[uid]) for uid in old_map.keys() & new_map.keys()
              if old_map[uid].strict_uid != calendar_ics.strict_uid(new_map[uid])]
  ```

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.**
  ```bash
  git add calendar_google.py tests/test_diff.py
  git commit -m "feat: diff engine with loose/strict uid identity"
  ```

---

## Task 7: Google Calendar sync

**Files:**
- Modify: `calendar_google.py` (add functions below)
- Test: `tests/test_google_sync.py`

**Interfaces:**
- Consumes: `auth.load_credentials`, `calendar_ics.strict_uid`, `calendar_ics.loose_uid`, `compute_diff` (Task 6), `models.ClassSnapshot`, `models.UserSettings`, `models.GoogleToken`.
- Produces:
  - `calendar_google.ensure_calendar(session, user_id: int, display_name: str) -> str` — returns calendar id, creating "סטודיו נופר" if needed, storing on `google_tokens.calendar_id`.
  - `calendar_google.sync_user(session, user_id: int, classes: list[ClassItem], duration_minutes: int) -> SyncDiff` — the full sync; writes events and updates `class_snapshots`.
  - On refresh failure, sets `user_settings.last_sync_status='needs_reauth'` and raises `NeedsReauth`.

- [ ] **Step 1: Write failing tests in `tests/test_google_sync.py`** (using a fake `googleapiclient.discovery.build` service that records calls):
  - `test_ensure_calendar_creates_once`: first call creates, second returns stored id.
  - `test_sync_user_inserts_events_for_added`: for 3 new classes, three `events.insert` calls happen; `class_snapshots` grows by 3.
  - `test_sync_user_deletes_removed`: old snapshot with no matching new → `events.delete(eventId=snapshot.google_event_id)`.
  - `test_sync_user_updates_changed`: time changed → `events.update(eventId=...)` with new `start`/`end`; snapshot row's `strict_uid`, `class_time`, `room` updated.
  - `test_sync_user_marks_needs_reauth_on_refresh_error`: injected `RefreshError` → `UserSettings.last_sync_status == 'needs_reauth'`, `NeedsReauth` raised. **[Review Focus #3]**
  - `test_sync_user_empty_new_list_does_not_delete_everything_if_no_old`: classes=[], old=[] → zero API calls; status 'ok'. **[Review Focus #2]**

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `ensure_calendar`.** Build service via `googleapiclient.discovery.build('calendar', 'v3', credentials=...)`. If `google_tokens.calendar_id` is None, `service.calendars().insert({'summary': display_name, 'timeZone': 'Asia/Jerusalem'})`; store id.

- [ ] **Step 4: Implement `sync_user`.** Load snapshots, compute diff, batch events in groups of ≤50 with exponential backoff on 429/5xx (3 retries, jitter). For each `added`: `events.insert` with event body including `summary`, `location`, `description`, `start.dateTime` + `TZID`, `end.dateTime` = start + duration, `iCalUID = strict_uid`. For each `changed`: `events.update(eventId=snapshot.google_event_id, body=...)`. For each `removed`: `events.delete`. Update `class_snapshots` to reflect new state. On `google.auth.exceptions.RefreshError`: set `UserSettings.last_sync_status='needs_reauth'`, raise `NeedsReauth`.

- [ ] **Step 5: Run tests to verify they pass.**

- [ ] **Step 6: Commit.**
  ```bash
  git add calendar_google.py tests/test_google_sync.py
  git commit -m "feat: Google Calendar sync with batching and reauth signal"
  ```

---

## Task 8: Notifications (ntfy.sh + digest)

**Files:**
- Create: `notifications.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Consumes: `calendar_google.SyncDiff`, `models.UserSettings`, `models.PendingDigest`, `models.NotificationsLog`.
- Produces:
  - `notifications.process_diff(session, user_id: int, diff: SyncDiff, now: datetime, http: requests.Session) -> None`
  - `notifications.send_digest(session, user_id: int, now: datetime, http: requests.Session) -> None` — merges and clears `pending_digest`.
  - `notifications.push(http, ntfy_server: str, topic: str, title: str, body: str, priority: int = 4, tags: list[str] | None = None) -> None` — raw push.

- [ ] **Step 1: Write failing tests in `tests/test_notifications.py`** (`http` is a fake session that records POSTs):
  - `test_urgent_cancellation_sends_push`: cancellation of a class 3 h from `now`, `urgent_window_hours=24` → one POST to `https://ntfy.sh/<topic>`, title starts with `"⚠️ בוטל"`.
  - `test_non_urgent_change_queues_digest`: change 48 h ahead, `urgent_window_hours=24` → zero POSTs; `pending_digest` row created.
  - `test_notify_toggle_off_cancellation_skips`: `notify_on_cancellation=False` → no push, no digest row.
  - `test_notifications_log_prevents_duplicate_push`: calling `process_diff` twice with the same diff → one POST only. **[Review Focus #4]**
  - `test_send_digest_merges_pending_rows`: 3 pending rows → one POST whose body contains all three lines; `pending_digest` empty afterward.
  - `test_push_hebrew_title_is_rfc2047_encoded`: title containing Hebrew sent via `Title` header is encoded with `email.header.Header(title,'utf-8').encode()`.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `notifications.py`.**
  - `push` POSTs to `{ntfy_server}/{topic}` with headers `Title`, `Priority`, `Tags` (comma-joined). Hebrew titles encoded via `Header(..., 'utf-8').encode()`.
  - `process_diff` iterates added/removed/changed, filtering by `notify_on_*`. For each, compute `delta = class.start_dt - now`. If `delta <= urgent_window_hours` and `delta >= -1h` → immediate push; else insert `pending_digest`. Insert `NotificationsLog(user_id, loose_uid, change_type)` on push; wrap in `INSERT OR IGNORE` to short-circuit duplicates.
  - `send_digest` reads `pending_digest` rows for the user, groups by `change_type`, formats one body per spec §5.4, pushes, then deletes those rows.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.**
  ```bash
  git add notifications.py tests/test_notifications.py
  git commit -m "feat: ntfy.sh push + daily digest with idempotency"
  ```

---

## Task 9: Scheduler

**Files:**
- Create: `scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `scraper.fetch_schedule`, `calendar_google.sync_user`, `notifications.process_diff`, `notifications.send_digest`, config.
- Produces:
  - `scheduler.sync_all_users(config, session_factory, http) -> None`
  - `scheduler.send_all_digests(config, session_factory, http) -> None`
  - `scheduler.start(config, session_factory, http) -> BackgroundScheduler`

- [ ] **Step 1: Write failing tests in `tests/test_scheduler.py`:**
  - `test_sync_all_users_calls_sync_for_each_configured_user`: two users with `studio_url` set → `fetch_schedule` + `sync_user` called twice.
  - `test_sync_all_users_skips_user_without_studio_url`: user with `studio_url=None` → not called.
  - `test_sync_all_users_continues_after_one_user_fails`: user A raises `NeedsReauth`, user B still processed; user A's `last_sync_status == 'needs_reauth'`. **[Review Focus #3]**
  - `test_start_registers_two_cron_jobs`: `scheduler.get_jobs()` includes id `daily_sync` with trigger hour=6/minute=0 and `daily_digest` with hour from config/minute from `digest_time`.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `scheduler.py`** per spec §5.5. Use `apscheduler.schedulers.background.BackgroundScheduler(timezone='Asia/Jerusalem')`. In `sync_all_users`, catch `NeedsReauth` and generic `Exception` per user, log, continue. After `sync_user`, call `notifications.process_diff` with the diff.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.**
  ```bash
  git add scheduler.py tests/test_scheduler.py
  git commit -m "feat: APScheduler jobs for sync and digest"
  ```

---

## Task 10: Flask app, routes, templates

**Files:**
- Create: `app.py`, `templates/base.html`, `templates/login.html`, `templates/index.html`, `static/style.css`, `static/app.js`
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a WSGI app `create_app(config: Config) -> Flask` and `app = create_app(load_config())` at module scope.

Routes (all per spec §5.7):

| Path | Method | Auth | Behavior |
|---|---|---|---|
| `/` | GET | required | renders `index.html` with current user + settings |
| `/login` | GET | — | renders `login.html` with Google button → `/auth/google` |
| `/auth/google` | GET | — | redirect to `auth.build_authorize_url` |
| `/auth/callback` | GET | — | `exchange_code` + `upsert_user_from_auth`; set `session['user_id']` |
| `/auth/logout` | GET | — | `session.clear()` → `/login` |
| `/api/load` | POST | required | body `{link}` → `extract_studio_id` → `fetch_schedule` → persist on `user_settings.studio_url` → return `{trainers: [{name, class_count}], weeks}` |
| `/api/trainers` | GET | required | returns `[{name, enabled, class_count}]` |
| `/api/trainers` | POST | required | body `{enabled_names: [...]}` → updates `trainers` table |
| `/api/sync` | POST | required | runs full sync for this user; returns diff summary |
| `/api/settings` | GET | required | returns `UserSettings` as JSON |
| `/api/settings` | POST | required | updates allowed fields; validates `urgent_window_hours ∈ [1, 168]`, `duration_minutes ∈ [15, 240]`, `digest_time` matches `HH:MM` |
| `/feed/<token>.ics` | GET | — | lookup `user_settings` by `feed_token`; fetch cached schedule; filter to enabled trainers; `build_ics`; `Content-Type: text/calendar; charset=utf-8` |
| `/download.ics` | GET | required | same as `/feed/*` but for current user |
| `/healthz` | GET | — | `"ok"` |

- [ ] **Step 1: Write failing tests in `tests/test_app_routes.py`** (Flask test client):
  - `test_root_redirects_to_login_when_not_authenticated`: GET `/` → 302 to `/login`.
  - `test_healthz`: 200, body `"ok"`.
  - `test_feed_route_returns_ics_for_enabled_trainers`: seeded user, cache, 2 trainers, 1 enabled → ICS body contains the enabled trainer's event SUMMARY and not the disabled one's.
  - `test_feed_route_404_on_unknown_token`.
  - `test_api_settings_rejects_bad_urgent_window`: POST `{urgent_window_hours: 999}` → 400.
  - `test_api_load_persists_studio_url`: POST a valid BoostApp link → `user_settings.studio_url == "636a5a359ccfa"`.
  - `test_api_trainers_post_toggles_enabled_flag`.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement `app.py`.** `create_app(config)` builds Flask app, sets `app.secret_key = config.secret_key`, registers `before_request` guard that redirects unauthenticated requests to `/login` for non-public paths. SQLAlchemy `SessionLocal` opened per-request via `g.db_session`.

- [ ] **Step 4: Implement templates and static files.**
  - `base.html`: `<html dir="rtl" lang="he">`, link to `style.css`, Hebrew navbar with username + logout.
  - `login.html`: single button → `/auth/google`.
  - `index.html`: UI per spec §6 — link input, trainers checklist, three sync buttons, settings panel, status bar; `app.js` wires the `fetch` calls to the `/api/*` endpoints.
  - `style.css`: minimal RTL layout, enough to be clean on phone + desktop.

- [ ] **Step 5: Run tests to verify they pass.**

- [ ] **Step 6: Commit.**
  ```bash
  git add app.py templates static tests/test_app_routes.py
  git commit -m "feat: Flask app, routes, and Hebrew RTL UI"
  ```

---

## Task 11: Entry point wiring + README

**Files:**
- Modify: `app.py` (add `__main__` guard that starts the scheduler + Flask)
- Create: `README.md`

**Interfaces:** none new. This task boots the system.

- [ ] **Step 1: Add `if __name__ == '__main__':` block in `app.py`** that:
  1. Loads config.
  2. Initializes DB (`init_db(get_engine(...))`).
  3. Starts `scheduler.start(...)`.
  4. Runs `app.run(host=config.host, port=config.port, use_reloader=False)`.

- [ ] **Step 2: Smoke test manually (not an automated test):**
  ```bash
  cp .env.example .env   # fill GOOGLE_* and generate FERNET_KEY + SECRET_KEY
  python app.py
  curl http://localhost:5000/healthz    # → ok
  ```

- [ ] **Step 3: Write `README.md`** in Hebrew covering:
  1. מה זה
  2. התקנה (`pip install -r requirements.txt`, יצירת `.env` + `FERNET_KEY = Fernet.generate_key()`)
  3. הגדרת Google Cloud OAuth — step-by-step: יצירת פרויקט, הפעלת Calendar API, OAuth consent screen (External, Testing עם המייל שלך ב-Test Users), OAuth client ID (Web application), redirect URI = `http://localhost:5000/auth/callback`, הורדת client ID + secret ל-`.env`
  4. הרצה מקומית — `python app.py`
  5. גישה מהטלפון דרך ngrok
  6. התקנת ntfy על האייפון וסריקת ה-topic מההגדרות
  7. הוספת ה-feed ל-Apple Calendar (iPhone → Settings → Calendar → Accounts → Add → Other → Add Subscribed Calendar)
  8. פריסה ל-Render / Railway (עדכון `BASE_URL` + redirect URI ב-Google Cloud)
  9. פתרון בעיות נפוצות

- [ ] **Step 4: Commit.**
  ```bash
  git add app.py README.md
  git commit -m "feat: entry point and Hebrew README"
  ```

---

## Dependencies between tasks

```
Task 1 (config)                 → everything
Task 2 (db / crypto)            → Tasks 5, 6, 7, 8, 9, 10, 11
Task 3 (scraper)                → Tasks 7 (via diff input), 9, 10
Task 4 (ics / uids)             → Tasks 6, 7, 10
Task 5 (auth)                   → Tasks 7, 10
Task 6 (diff)                   → Tasks 7, 8
Task 7 (google sync)            → Tasks 9, 10
Task 8 (notifications)          → Task 9
Task 9 (scheduler)              → Task 11
Task 10 (flask / ui)            → Task 11
```

**End of plan.**
