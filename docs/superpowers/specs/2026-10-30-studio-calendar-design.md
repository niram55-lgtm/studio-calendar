# Studio Calendar — Design Spec

**Date:** 2026-10-30
**Status:** Draft awaiting user review
**Owner:** Nir Amor (niram55@gmail.com)

---

## 1. מה האפליקציה עושה (Intent)

אפליקציית ווב רב-משתמשית המסנכרנת שיעורי כושר מלוח הזמנים של סטודיו ב-BoostApp
ישירות ליומן של המשתמש:

1. המשתמש מתחבר עם חשבון Google.
2. מדביק קישור של סטודיו ב-BoostApp (למשל `https://app.boostapp.co.il/Rest.php?datepicker=02/02&StudioUrl=636a5a359ccfa`).
3. בוחר אילו מדריכות הוא רוצה לעקוב אחריהן.
4. המערכת מסנכרנת את השיעורים ליומן Google שלו (יומן ייעודי: "סטודיו נופר") וגם מספקת לו URL מנוי `.ics` ל-Apple Calendar.
5. ברקע: רענון יומי (06:00 שעון ישראל) + זיהוי שינויים וביטולים ושליחת פוש ל-ntfy.sh.

**Success criteria:**
- משתמש יכול להירשם, להדביק לינק, לבחור מדריכות, ולראות שיעורים ביומן תוך פחות מ-5 דקות.
- שינוי בלוח (ביטול/שינוי שעה/שינוי חדר/שינוי מדריכה) של שיעור מתוך 24 השעות הקרובות = פוש לנייד תוך דקות.
- אין כפילויות ביומן אחרי sync חוזר.
- המערכת רצה גם מקומית וגם ב-cloud ללא שינויי קוד — רק קונפיג.

**Non-goals (YAGNI):**
- אין פאנל admin.
- אין היסטוריית syncs/לוג audit.
- אין התראות על שיעורים חדשים כברירת מחדל (ניתן להפעיל ידנית).
- אין רישום חוצה-סטודיואים (user אחד = סטודיו אחד; תמיכה רב-סטודיואית = גרסה עתידית).

---

## 2. סטאק טכני (Tech Stack)

| שכבה | טכנולוגיה | סיבה |
|------|-----------|------|
| Backend | Python 3.11+, Flask | פשוט, קהילת ספריות עשירה |
| DB | SQLite + SQLAlchemy ORM | אפס תחזוקה, מספיק ל-100+ משתמשים |
| Scraper | `requests` + `beautifulsoup4` | התקן de-facto |
| ICS | `icalendar` | התקן de-facto |
| Google API | `google-api-python-client` + `google-auth` + `google-auth-oauthlib` | ספרייה רשמית |
| Scheduler | `APScheduler` (BackgroundScheduler) | פשוט לפריסה ב-process יחיד |
| Push | `requests` → ntfy.sh HTTP API | חינם, אפס תלויות |
| Templates | Jinja2 (מובנה ב-Flask) | — |
| Frontend | HTML + CSS + vanilla JS | אין צורך ב-SPA |
| Encryption | `cryptography.fernet` | הצפנת refresh tokens ב-DB |
| Config | `pyyaml` | — |

---

## 3. ארכיטקטורה כללית

```
                        ┌────────────────────────┐
                        │   User (Browser / iOS) │
                        └───────────┬────────────┘
                                    │ HTTPS
                                    ▼
            ┌───────────────────────────────────────────┐
            │  Flask app (app.py)                       │
            │  ┌─ Routes ─────────────────────────────┐ │
            │  │ /                     Dashboard      │ │
            │  │ /auth/google          OAuth init     │ │
            │  │ /auth/callback        OAuth return   │ │
            │  │ /auth/logout                         │ │
            │  │ /api/load             POST studio    │ │
            │  │ /api/trainers         GET/POST       │ │
            │  │ /api/sync             POST manual    │ │
            │  │ /api/settings         GET/POST       │ │
            │  │ /feed/<token>.ics     Public ICS     │ │
            │  │ /download.ics         Logged-in ICS  │ │
            │  └──────────────────────────────────────┘ │
            └──┬─────────────┬────────────┬────────────┘
               │             │            │
               ▼             ▼            ▼
         ┌──────────┐   ┌─────────┐   ┌──────────┐
         │ scraper  │   │ google  │   │ ics      │
         │ .py      │   │ cal.py  │   │ cal.py   │
         └────┬─────┘   └────┬────┘   └────┬─────┘
              │              │              │
              │              ▼              │
              │      ┌────────────────┐     │
              │      │ Google Cal API │     │
              │      └────────────────┘     │
              │                             │
              ▼                             ▼
       ┌─────────────┐                ┌──────────────┐
       │ BoostApp    │                │ Apple device │
       │ (HTML HTTP) │                │ subscribes   │
       └─────────────┘                └──────────────┘

                   APScheduler (background)
                    ├── daily 06:00 → sync_all_users()
                    └── daily 06:30 → send_digests()

                   notifications.py → ntfy.sh/<topic>
```

---

## 4. מודל נתונים (DB Schema)

```sql
-- משתמשים (זהות = Google)
CREATE TABLE users (
    id              INTEGER PRIMARY KEY,
    google_sub      TEXT UNIQUE NOT NULL,   -- Google user id
    email           TEXT NOT NULL,
    display_name    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- טוקני Google OAuth (encrypted)
CREATE TABLE google_tokens (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    encrypted_access   BLOB NOT NULL,
    encrypted_refresh  BLOB NOT NULL,
    expiry          TIMESTAMP NOT NULL,
    calendar_id     TEXT                 -- נוצר בפעם הראשונה שסונכרן
);

-- הגדרות לכל משתמש
CREATE TABLE user_settings (
    user_id                     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    studio_url                  TEXT,                 -- "636a5a359ccfa"
    studio_display_name         TEXT DEFAULT 'סטודיו נופר',
    feed_token                  TEXT UNIQUE NOT NULL, -- 32 hex chars
    ntfy_topic                  TEXT UNIQUE NOT NULL, -- random slug
    urgent_window_hours         INTEGER DEFAULT 24,
    duration_minutes            INTEGER DEFAULT 55,
    weeks_ahead                 INTEGER DEFAULT 1,  -- מעבר לנוכחי (1 = נוכחי + הבא)
    notify_on_cancellation      BOOLEAN DEFAULT 1,
    notify_on_change            BOOLEAN DEFAULT 1,
    notify_on_new_class         BOOLEAN DEFAULT 0,
    digest_time                 TEXT DEFAULT '06:30',  -- HH:MM
    last_sync_at                TIMESTAMP,
    last_sync_status            TEXT                   -- 'ok' | 'error:...'
);

-- מדריכות שהמשתמש בוחר
CREATE TABLE trainers (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trainer_name    TEXT NOT NULL,
    enabled         BOOLEAN DEFAULT 1,
    UNIQUE (user_id, trainer_name)
);

-- Snapshot של השיעורים שסונכרנו (לצורך diff)
CREATE TABLE class_snapshots (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    loose_uid       TEXT NOT NULL,       -- sha1(date + trainer + class_type)
    strict_uid      TEXT NOT NULL,       -- sha1(date + time + trainer + class_type + room)
    class_date      DATE NOT NULL,
    class_time      TEXT NOT NULL,       -- "HH:MM"
    trainer         TEXT NOT NULL,
    class_type      TEXT NOT NULL,
    room            TEXT NOT NULL,
    google_event_id TEXT,
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, loose_uid)
);

-- שינויים שממתינים ל-digest
CREATE TABLE pending_digest (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    change_type     TEXT NOT NULL,       -- 'added' | 'removed' | 'changed'
    payload_json    TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- לוג התראות שנשלחו (idempotency)
CREATE TABLE notifications_log (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    loose_uid       TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, loose_uid, change_type)
);

-- Cache של דפי BoostApp (משותף בין משתמשים)
CREATE TABLE scrape_cache (
    studio_url      TEXT NOT NULL,
    week_start      DATE NOT NULL,       -- יום ראשון של השבוע הנדרש
    html            TEXT NOT NULL,
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (studio_url, week_start)
);
```

---

## 5. פירוט מודולים

### 5.1 `scraper.py`

**אחריות:** למשוך דפי BoostApp ולהחזיר רשימה טיפוסית של שיעורים.

**API:**
```python
def fetch_schedule(studio_url: str, weeks_ahead: int = 1) -> list[ClassItem]:
    """
    מחזיר שיעורים לשבוע הנוכחי + weeks_ahead שבועות קדימה.
    weeks_ahead=1 → 2 שבועות סה"כ (נוכחי + הבא). weeks_ahead=0 → רק הנוכחי.
    week_start מוגדר כיום ראשון (ה-locale הישראלי).
    משתמש ב-cache (TTL = 1 שעה לכל צירוף studio_url+week_start).
    אם BoostApp לא מגיב, מחזיר מה-cache ומסמן source='cache-stale'.
    """

@dataclass
class ClassItem:
    date: datetime.date      # 2026-02-02
    time: datetime.time      # 06:00
    trainer: str             # "שירן אמויאל"
    class_type: str          # "Body Pump"
    room: str                # "POWER" | "HOME" | "MOTION" | ...
    source: str              # "live" | "cache"
```

**לוגיקת פרסור (על בסיס HTML אמיתי של 636a5a359ccfa):**

1. למשוך URL `https://app.boostapp.co.il/Rest.php?datepicker=MM/DD&StudioUrl=<id>`
   - תאריך = יום ראשון של שבוע היעד (פורמט MM/DD, URL-encoded `MM%2FDD`).
   - **הערה:** דף יחיד מחזיר 8 ימים (שבוע + יום הבא). שתי משיכות (שבוע נוכחי + שבוע הבא) נותנות כיסוי מלא עם חפיפה — נבצע dedup לפי `strict_uid`.
2. פרסור ב-BeautifulSoup:
   ```python
   soup = BeautifulSoup(html, 'html.parser')
   # יום = h4 בטקסט "יום שני 02/02/2026"
   # לפרסר רק h4 שבתוך tbody של טבלת לוח (לא ה-h4 של ה-datepicker)
   day_headers = soup.select('table.tableListClass thead h4')
   for day_h4 in day_headers:
       m = re.match(r'(יום \S+)\s+(\d{2}/\d{2}/\d{4})', day_h4.get_text(strip=True))
       class_date = datetime.strptime(m.group(2), '%d/%m/%Y').date()
       # ... iterate <tr> rows inside sibling <tbody>
       # First <td>: <h5>HH:MM</h5> = time
       # Second <td>:
       #   <h5>...</h5> = class_type (strip inner <a> if present)
       #   <p>
       #     <i class="fa fa-user"></i> trainer_name
       #     <i class="fa fa-map-marker"></i> ROOM
   ```
3. חילוץ מדריכה וחדר: שימוש ב-sibling text אחרי כל `<i>`:
   ```python
   p = row.select_one('td:nth-of-type(2) p')
   # הטקסט אחרי כל אייקון
   icons = p.select('i.fa')
   trainer = icons[0].next_sibling.strip()
   room = icons[1].next_sibling.strip()
   ```
4. UTF-8: `response.encoding = 'utf-8'` במפורש, לא לסמוך על detect.

**Cache:** `scrape_cache` עם TTL של שעה. בכישלון רשת → נסה cache גם אם stale.

**בדיקה שתידרש:** fixture של ה-HTML האמיתי (קובץ `tests/fixtures/boost_week_2026-02-02.html`) → parser → רשימה של שיעורים. בדיקה ש-123 שיעורים נספרו, שהמדריכות ושמות החדרים תקינים.

---

### 5.2 `calendar_ics.py`

**אחריות:** ליצור `.ics` מרשימת שיעורים.

**API:**
```python
def build_ics(classes: list[ClassItem], calendar_name: str = 'סטודיו נופר',
              duration_minutes: int = 55) -> bytes:
    """
    מחזיר ICS בפורמט UTF-8 bytes.
    - VTIMEZONE: Asia/Jerusalem
    - UID לכל אירוע: strict_uid (יציב)
    - SUMMARY: "{class_type} — {trainer}"
    - LOCATION: "סטודיו נופר — {room}"
    - DESCRIPTION: "{class_type} | {trainer} | אולם {room}"
    """
```

**פורמט UID:**
```python
def strict_uid(c: ClassItem) -> str:
    s = f"{c.date.isoformat()}|{c.time.isoformat()}|{c.trainer}|{c.class_type}|{c.room}"
    return hashlib.sha1(s.encode('utf-8')).hexdigest() + '@studio-calendar'
```

---

### 5.3 `calendar_google.py`

**אחריות:** לסנכרן רשימת שיעורים ליומן Google ייעודי של משתמש.

**API:**
```python
def ensure_calendar(user_id: int) -> str:
    """
    מחזיר את ה-calendar_id של "סטודיו נופר" אצל המשתמש.
    יוצר אותו אם לא קיים (עם timezone Asia/Jerusalem).
    """

def sync_user(user_id: int, classes: list[ClassItem]) -> SyncDiff:
    """
    משווה את classes מול class_snapshots הקיימים.
    מבצע בפועל:
      - events.insert עבור added
      - events.delete עבור removed
      - events.update עבור changed (אותו loose_uid, strict_uid שונה)
    מעדכן class_snapshots.
    מחזיר SyncDiff לצורך שליחת התראות.
    """

@dataclass
class SyncDiff:
    added:    list[ClassItem]
    removed:  list[ClassItem]
    changed:  list[tuple[ClassItem, ClassItem]]   # (לפני, אחרי)
```

**התאמת זהות (identity matching):**
- **loose_uid** (לזיהוי diff): `sha1(date + trainer + class_type)` — שומר על זהות גם אם השעה או החדר השתנו.
- **strict_uid** (ל-Google event id): `sha1(date + time + trainer + class_type + room)` — ייחודי לרכב-ערכים.
- אלגוריתם:
  ```
  old = {snapshot.loose_uid: snapshot for snapshot in db}
  new = {loose_uid(c): c for c in classes}
  
  added    = [c for uid, c in new.items() if uid not in old]
  removed  = [s for uid, s in old.items() if uid not in new]
  changed  = [(old[uid], new[uid]) for uid in old & new
              if old[uid].strict_uid != strict_uid(new[uid])]
  ```

**Rate limits של Google:** batch request של עד 50 פעולות בבת אחת. חזרה עם `exponential backoff` ב-429.

**Scope OAuth:**
```
openid email profile          # לזיהוי משתמש
https://www.googleapis.com/auth/calendar  # יצירת/עדכון יומן ואירועים
```

**Refresh token:** נשמר מוצפן ב-DB. בכישלון refresh → מסמן את המשתמש כ-`needs_reauth` ומראה בבאנר ב-UI "התחבר מחדש".

---

### 5.4 `notifications.py`

**אחריות:** לשלוח פוש ל-ntfy.sh ולנהל digest.

**API:**
```python
def process_diff(user_id: int, diff: SyncDiff) -> None:
    """
    עבור כל פריט ב-diff:
      1. בדוק הגדרות משתמש (notify_on_*).
      2. חשב delta: class.start - now
      3. אם delta <= urgent_window_hours: send_push(immediate)
         else: queue_for_digest()
    רושם ב-notifications_log למניעת כפילויות.
    """

def send_digest(user_id: int) -> None:
    """
    נקרא פעם ביום ע"י scheduler.
    מרכז את כל pending_digest של 24ש האחרונות להודעה אחת ושולח.
    """

def _ntfy_push(topic: str, title: str, body: str,
               priority: int = 4, tags: list[str] = None) -> None:
    """
    POST https://ntfy.sh/<topic>
    Headers: Title (base64 אם Hebrew), Priority, Tags
    Body: גוף ההודעה.
    """
```

**פורמט הודעה מיידית:**
```
Title: ⚠️ בוטל: Body Pump — שירן אמויאל
Body:  היום 18:00 · POWER
Tags:  warning
```

**פורמט digest:**
```
Title: 📋 עדכוני לוח שבועי (3 שינויים)
Body:
• בוטל: Pilates — תמי בבקוב — יום רביעי 05/02 09:35
• שעה חדשה: Body Pump — שירן אמויאל — יום חמישי 06/02 18:00 → 19:00
• חדר חדש: Nufunctional — אור כץ — יום שישי 07/02 → MOTION
```

**הערת UTF-8:** ntfy.sh דורש Title encoded כ-RFC 2047 כאשר יש תווים לא-ASCII:
```python
from email.header import Header
title_encoded = Header(title, 'utf-8').encode()
```

---

### 5.5 `scheduler.py`

**אחריות:** להריץ sync יומי לכל המשתמשים.

**API:**
```python
def start_scheduler(app: Flask) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone='Asia/Jerusalem')
    scheduler.add_job(sync_all_users, 'cron', hour=6, minute=0, id='daily_sync')
    scheduler.add_job(send_all_digests, 'cron', hour=6, minute=30, id='daily_digest')
    scheduler.start()
    return scheduler

def sync_all_users() -> None:
    """לכל user פעיל: fetch_schedule → sync_user → process_diff.
    משתמשים עם needs_reauth או שגיאת סקרייפ כרונית — נדלגים."""
```

**הערה חשובה:** APScheduler + Flask dev server לא תמיד מתיישרים (reloader מריץ שני processes). שליטה: `app.run(use_reloader=False)` בפרודקשן, או `use_reloader=True` רק ב-debug.

---

### 5.6 `auth.py`

**אחריות:** Google OAuth 2.0 flow.

**זרימת רישום / כניסה:**
```
1. GET /auth/google
     → generate state (session), redirect to Google authorize URL
     → scopes: openid email profile + calendar
2. GET /auth/callback?code=...&state=...
     → verify state
     → exchange code for tokens (access + refresh)
     → decode id_token → google_sub, email, name
     → upsert user; store encrypted tokens
     → create user_settings עם ntfy_topic ו-feed_token חדשים אם לא קיימים
     → set session['user_id']
     → redirect /
3. GET /auth/logout → session.clear() → /
```

**מתג מצב פריסה:**
```python
redirect_uri = f"{config.base_url}/auth/callback"
# local:  http://localhost:5000/auth/callback  (loopback — דורש אישור ב-Google Cloud Console)
# cloud:  https://studio-cal.example.com/auth/callback
```

**הצפנה:**
```python
# .env
SECRET_KEY=<flask secret>
FERNET_KEY=<32-byte base64 Fernet key>
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

---

### 5.7 `app.py`

**Entry point.** Flask app + routing + session + middleware.

**Session:** `flask.session` עם secure cookie (secret ב-env).

**Public routes (ללא auth):**
- `/feed/<token>.ics` — ICS feed (זהות דרך feed_token של user_settings).
- `/healthz` — health check.

**Authenticated routes:**
- `/` — Dashboard עיקרי.
- `/api/load` — POST `{studio_link: ...}`. חילוץ `StudioUrl`, הפעלת scraper, החזרת רשימת מדריכות + ספירת שיעורים.
- `/api/trainers` — GET/POST של רשימת מדריכות + enabled.
- `/api/sync` — POST — הפעלת sync ידני.
- `/api/settings` — GET/POST של הגדרות משתמש.

---

## 6. UI

דף יחיד (`index.html`) ב-RTL, Hebrew. Vanilla JS + fetch.

```
┌──────────────────────────────────────────────┐
│  סטודיו קלנדר                     [👤 יציאה] │
├──────────────────────────────────────────────┤
│                                              │
│  📎 הדבק קישור של סטודיו ב-BoostApp:         │
│  [https://...                          ] [טען]│
│                                              │
│  ─────  מדריכות (12)  ─────  [בחר הכל / נקה] │
│  ☑ שירן אמויאל          (23 שיעורים השבוע)  │
│  ☑ אור כץ               (8)                 │
│  ☐ תמי בבקוב            (12)                │
│  ... [שמור]                                  │
│                                              │
│  ─────  סנכרון  ─────                        │
│  [🟢 סנכרן ל-Google]  [🍎 לינק Apple]  [📥 ICS]│
│                                              │
│  ⚙️ הגדרות התראות                             │
│  חלון דחיפות: [24▼] שעות                     │
│  ☑ ביטולים  ☑ שינויים  ☐ שיעורים חדשים       │
│  שעת digest: [06:30]                         │
│  📱 ntfy topic: nufar-studio-a7f3b2c1        │
│     [העתק] [הוראות התקנה באייפון]            │
│                                              │
│  ─────  סטטוס  ─────                         │
│  סנכרון אחרון: לפני 2 שעות ✅                │
│  סנכרון הבא: מחר 06:00                       │
└──────────────────────────────────────────────┘
```

---

## 7. קונפיגורציה

**`config.yaml`:**
```yaml
# מצב פריסה
deployment_mode: local            # local | cloud
base_url: http://localhost:5000   # יוחלף ב-cloud

# שרת
host: 127.0.0.1                   # 0.0.0.0 ב-cloud
port: 5000

# ברירות מחדל ל-user settings חדשים
defaults:
  timezone: Asia/Jerusalem
  class_duration_minutes: 55
  weeks_ahead: 1              # 2 שבועות סה"כ (נוכחי + 1 קדימה)
  urgent_window_hours: 24
  digest_time: "06:30"

# תזמון
scheduler:
  daily_sync_hour: 6
  daily_sync_minute: 0
  cache_ttl_hours: 1

# BoostApp
boostapp:
  base_url: https://app.boostapp.co.il/Rest.php
  user_agent: "StudioCalendar/1.0"
  request_timeout: 15

# ntfy
ntfy:
  server: https://ntfy.sh
```

**`.env`** (לא ב-git):
```
SECRET_KEY=<random 64 chars>
FERNET_KEY=<Fernet.generate_key()>
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
```

---

## 8. מבנה פרויקט סופי

```
C:\Users\Nir Amor\Documents\studio-calendar\
├── app.py
├── auth.py
├── scraper.py
├── calendar_ics.py
├── calendar_google.py
├── notifications.py
├── scheduler.py
├── db.py
├── models.py                      # SQLAlchemy models
├── config.py                      # YAML loader + env
├── config.yaml
├── config.example.yaml
├── .env.example
├── .gitignore
├── requirements.txt
├── studio.db                      # (gitignored, נוצר בריצה)
├── templates/
│   ├── base.html
│   ├── login.html
│   └── index.html
├── static/
│   ├── style.css
│   └── app.js
├── tests/
│   ├── conftest.py
│   ├── test_scraper.py
│   ├── test_ics.py
│   ├── test_notifications.py
│   ├── test_diff.py
│   └── fixtures/
│       └── boost_week_2026-02-02.html
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-10-30-studio-calendar-design.md   # (this file)
└── README.md
```

---

## 9. טיפול בשגיאות

| מצב | טיפול |
|-----|-------|
| BoostApp לא מגיב | שימוש ב-cache (גם אם stale), באנר "נתונים מ-{timestamp}" |
| HTML השתנה (parser נכשל) | log + sentry-like הודעת פוש לאדמין (אם מוגדר); החזר רשימה ריקה; UI מראה "לא ניתן לפרסר כרגע" |
| Google token פג + refresh נכשל | סמן `needs_reauth=True`; UI באנר "התחבר מחדש"; sync מדלג על המשתמש |
| Google 429 / 5xx | exponential backoff עם jitter, עד 3 ניסיונות |
| ntfy.sh נכשל | log; retry 3 פעמים; ללא חסימת ה-sync |
| משתמש הוסיף studio_url לא תקין | הודעת שגיאה בעברית ב-UI: "לא נמצא סטודיו עם המזהה הזה" |
| משתמש אין מדריכות ב-DB | sync מדלג (אין מה לסנכרן) |

---

## 10. אבטחה

- טוקנים של Google: Fernet-encrypted ב-DB; `FERNET_KEY` ב-env בלבד.
- `feed_token` ו-`ntfy_topic`: 32 hex chars אקראיים (`secrets.token_hex(16)`). לא ניתנים לניחוש.
- Flask session: cookie מוצפן, `SECRET_KEY` חזק, `httponly`, `secure` (ב-cloud).
- CSRF: ל-POST APIs — token ב-header.
- רישום מוגבל: אין self-signup גלוי — כל אחד עם חשבון Google ישתמש; אם צריך whitelist בעתיד, קל להוסיף.
- רייט לימיט: 10 בקשות/דקה לנקודות `/api/*` (Flask-Limiter).
- `/feed/<token>.ics` — לא דורש auth בכוונה (Apple Calendar צריך גישה אנונימית); האבטחה היא ה-token הארוך.

---

## 11. בדיקות (Testing strategy)

**יחידה (unit):**
- `test_scraper.py` — פרסור ה-fixture של 02/02/2026, בדיקה של:
  - מספר ימים נספר נכון (8)
  - שיעור ספציפי מופיע עם המדריכה והחדר הנכונים
  - פרסור של `h5` עם קישור פנימי (`<a class="ClickClass">`) עובד
  - התמודדות עם מרווחים/טאבים ב-trainer name
- `test_ics.py` — יצירת ICS מרשימת 3 שיעורים, בדיקה ש:
  - UIDs יציבים (אותו שיעור → אותו UID בשתי קריאות)
  - timezone Asia/Jerusalem קיים
  - תווים בעברית מופיעים כ-UTF-8
- `test_diff.py` — diff בין snapshot ישן לרשימה חדשה:
  - ביטול → removed
  - הוספה → added
  - שינוי שעה (אותו date+trainer+class) → changed, not removed+added
- `test_notifications.py` — mock של ntfy, בדיקה ש:
  - פריט דחוף נשלח מיידית
  - פריט לא-דחוף נכנס ל-pending_digest
  - אותו שינוי לא נשלח פעמיים (notifications_log)

**אינטגרציה (requires network, מסומנות `@pytest.mark.integration`):**
- משיכה אמיתית מ-BoostApp עם ה-studio_url של נופר, בדיקה שהפרסור מצליח.

**ידני:**
- OAuth round-trip (אי אפשר ל-mock במלואו).
- Apple Calendar subscription E2E.

---

## 12. פריסה

### מצב Local
```bash
# dev
python app.py                     # → http://localhost:5000
```
- OAuth redirect: `http://localhost:5000/auth/callback` (אפשר ב-Google Cloud Console כ-loopback).
- Apple ICS feed URL מקומי: `http://localhost:5000/feed/<token>.ics` — אפל לא תגיע לזה מהאינטרנט.
- פתרון למנוי אפל ממכשיר חיצוני: ngrok (README יכלול הוראות).

### מצב Cloud
- דוגמת פריסה: Render.com (web service חינם) או Railway.
- `DEPLOYMENT_MODE=cloud` + `BASE_URL=https://studio-cal.example.com`.
- OAuth redirect ב-Google Cloud Console: `https://studio-cal.example.com/auth/callback`.
- APScheduler עם `BackgroundScheduler` — יעבוד ב-single-worker deployment. למעבר ל-multi-worker, להעביר ל-cron חיצוני או `APScheduler` + Postgres.
- SQLite → ב-cloud עובר ל-persistent volume של השירות.

### `requirements.txt`
```
Flask>=3.0,<4
Flask-Limiter>=3.5,<4
SQLAlchemy>=2.0,<3
beautifulsoup4>=4.12,<5
requests>=2.31,<3
icalendar>=5.0,<6
google-api-python-client>=2.100,<3
google-auth>=2.23,<3
google-auth-oauthlib>=1.1,<2
APScheduler>=3.10,<4
PyYAML>=6.0,<7
cryptography>=41,<42
python-dotenv>=1.0,<2
pytest>=7.4,<9
```

---

## 13. README.md (סכמה)

יוכן כחלק מהיישום. ראשי פרקים:

1. **מה זה** — תיאור קצר בעברית
2. **התקנה** — pip install, יצירת `.env`
3. **הגדרת Google Cloud OAuth** — step-by-step עם צילומי מסך/קישורים:
   - יצירת פרויקט
   - הפעלת Google Calendar API
   - יצירת OAuth consent screen (External, מצב Testing עם המייל שלך ב-test users)
   - יצירת OAuth client ID (Web application)
   - הגדרת redirect URI
   - הורדת credentials והעתקת ID+Secret ל-.env
4. **הרצה מקומית** — `python app.py`
5. **הרצה מהטלפון** — ngrok setup לאפל/גוגל + base_url
6. **הגדרת ntfy על האייפון** — התקנת האפליקציה, הוספת topic, בדיקה
7. **פריסה ל-cloud** — Render / Railway
8. **עריכת config.yaml**
9. **פתרון בעיות נפוצות**

---

## 14. סיכונים ידועים

| סיכון | הפחתה |
|-------|-------|
| BoostApp ישנה HTML | Fixture tests + parsing יחיד; אם parser נכשל — notification לאדמין |
| ntfy.sh public רועש | אפשרות עתידית: self-host ntfy; לעת עתה — topics ארוכים ואקראיים |
| Google refresh token פג (6 חודשים ללא שימוש בפרויקט Testing) | ברגע שעוברים ל-Published (not Testing) זה נפתר; המלצה ב-README |
| APScheduler + Flask reloader double-run | `use_reloader=False` ב-production |
| שעון קיץ (DST) | שימוש ב-`pytz`/`zoneinfo` + VTIMEZONE ב-ICS, לא offset קבוע |

---

## 15. פתוח להרחבה (Future, לא בגרסה זו)

- תמיכה במספר סטודיואים לאותו משתמש
- Admin panel עם סטטוס sync לכולם
- Web Push PWA (לא דורש אפליקציה חיצונית)
- WhatsApp notifications (Twilio)
- יומן ציבורי לשיתוף (read-only)
- לוח "השיעורים הפופולריים השבוע" ב-dashboard

---

**End of design.**
