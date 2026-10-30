# סטודיו קלנדר — Studio Calendar

מסנכרן אוטומטית את לוח השיעורים של סטודיו ב-BoostApp ליומן Google שלך
ול-Apple Calendar, ושולח התראות פוש על ביטולים/שינויים — ל-ntfy.sh.

## מה האפליקציה עושה

1. אתה מתחבר עם חשבון Google.
2. מדביק קישור של סטודיו ב-BoostApp (למשל:
   `https://app.boostapp.co.il/Rest.php?datepicker=02/02&StudioUrl=636a5a359ccfa`).
3. בוחר אילו מדריכות לעקוב אחריהן.
4. האפליקציה מסנכרנת:
   - **Google Calendar** — יוצרת יומן בשם "סטודיו נופר" וכותבת שם את השיעורים.
   - **Apple Calendar** — נותנת לך URL מנוי ל-ICS; אפל סורקת אותו כל כמה שעות.
5. פעם ביום (ברירת מחדל: 06:00 שעון ישראל) האפליקציה בודקת שינויים ושולחת פוש
   ל-ntfy על ביטולים/שינויים לשיעורים בחלון הזמן שלך (ברירת מחדל: 24ש הקרובות).

---

## התקנה

דרישות:
- Python 3.11 ומעלה.
- חשבון Google.
- (אפציונלי לגישה מהטלפון) ngrok.

```bash
cd studio-calendar
py -m pip install -r requirements.txt
```

צור קובץ `.env` לפי `.env.example`:

```
SECRET_KEY=<מחרוזת ארוכה אקראית — py -c "import secrets; print(secrets.token_hex(32))">
FERNET_KEY=<py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
GOOGLE_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
BASE_URL=http://localhost:5000
```

ה-`SECRET_KEY` וה-`FERNET_KEY` חייבים להיות ייחודיים לך; **אל תשתף אותם**.

---

## הגדרת Google Cloud OAuth

גוגל דורשת שתגדיר פרויקט OAuth משלך (זה בחינם ולוקח ~5 דקות).

1. היכנס ל-[Google Cloud Console](https://console.cloud.google.com/).
2. **צור פרויקט חדש**: בחר שם למשל `studio-calendar`.
3. **הפעל את Google Calendar API**:
   - תפריט → APIs & Services → Library.
   - חפש "Google Calendar API" → לחץ Enable.
4. **הגדר OAuth consent screen**:
   - APIs & Services → OAuth consent screen.
   - User Type: **External**.
   - Application name: `Studio Calendar` (או מה שתרצה).
   - User support email: המייל שלך.
   - Developer contact: המייל שלך.
   - בשלב Scopes — אין צורך להוסיף כלום (האפליקציה מבקשת scopes בזמן אמת).
   - בשלב **Test users**: הוסף את המייל שלך (ואת מיילים של משתמשים נוספים).
   - שמור. בשלב זה האפליקציה במצב **Testing** — זה בסדר לשימוש אישי.
5. **צור OAuth Client ID**:
   - APIs & Services → Credentials → Create Credentials → OAuth client ID.
   - Application type: **Web application**.
   - Name: `Studio Calendar`.
   - Authorized redirect URIs: הוסף:
     - `http://localhost:5000/auth/callback` (לפיתוח מקומי)
     - `https://your-domain.com/auth/callback` (לאחר פריסה, אם רלוונטי)
   - Create. תקבל Client ID + Client Secret — העתק אותם ל-`.env`.

---

## הרצה מקומית

```bash
py app.py
```

פתח בדפדפן: http://localhost:5000

- לחץ "התחבר עם Google".
- אשר את ההרשאות (קריאה וכתיבה ליומן).
- הדבק את הקישור של הסטודיו, לחץ "טען".
- בחר מדריכות, שמור.
- לחץ "סנכרן ל-Google Calendar".

---

## גישה מהטלפון (ngrok)

1. [הורד ngrok](https://ngrok.com/download) והירשם לחשבון חינמי.
2. התקן את הטוקן שלך (`ngrok config add-authtoken <token>`).
3. בטרמינל נפרד:
   ```bash
   ngrok http 5000
   ```
4. ngrok יציג URL כמו `https://abc123.ngrok-free.app` — זה ה-URL הציבורי שלך.
5. עדכן ב-`.env`:
   ```
   BASE_URL=https://abc123.ngrok-free.app
   ```
   ובגוגל Cloud Console (Credentials → ה-OAuth client שלך):
   הוסף `https://abc123.ngrok-free.app/auth/callback` ל-Authorized redirect URIs.
6. הפעל מחדש את האפליקציה (`py app.py`).

**הערה:** ngrok Free מייצר URL חדש בכל הפעלה. בכל פעם צריך לעדכן את ה-`.env`
ואת ה-redirect URI בגוגל. לקבלת URL קבוע → ngrok Pro או פריסה לענן.

---

## התראות (ntfy.sh) — איך להתקין על האייפון

1. התקן את אפליקציית **ntfy** מה-App Store (חינם).
2. פתח את האפליקציה → "+" (הוספת subscription).
3. בשדה Topic, הדבק את ה-`ntfy topic` שמוצג בעמוד ההגדרות שלך
   (מחרוזת של 32 תווים).
4. סיימת — התראות על ביטולים/שינויים יגיעו ישר למסך הנעילה.

**בטיחות:** מכיוון ש-ntfy.sh הוא שרת ציבורי, כל אחד שמנחש את ה-topic שלך
יוכל לראות התראות. ה-topic אקראי של 32 hex (אין סיכוי מעשי לניחוש).
אם רוצים — אפשר להריץ ntfy עצמי (עתידי).

---

## התקנת מנוי ל-Apple Calendar

1. באפליקציה שלך לחץ "🍎 לינק ל-Apple" והעתק את ה-URL (`https://.../feed/<token>.ics`).
2. באייפון: **Settings → Calendar → Accounts → Add Account → Other
   → Add Subscribed Calendar**.
3. הדבק את ה-URL → Next → Save.
4. אפל תסרוק את ה-URL הזה פעם בכמה שעות ותעדכן את הלוח אוטומטית.

---

## פריסה ל-cloud (Render / Railway)

1. דחוף את הקוד ל-GitHub (public או private).
2. ב-Render.com → New Web Service → חבר את ה-repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python app.py`
5. Environment variables:
   - `SECRET_KEY`, `FERNET_KEY` — יצירה אקראית.
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
   - `BASE_URL=https://your-service.onrender.com`.
6. בגוגל Cloud Console — הוסף `https://your-service.onrender.com/auth/callback`
   ל-Authorized redirect URIs.
7. ב-`config.yaml` — שנה `deployment_mode: cloud` ו-`host: 0.0.0.0`.

הערה: ל-Render Free Tier יש "cold start" של ~30 שניות אחרי חוסר פעילות.
לסנכרון יומי זה לא משפיע.

---

## עריכת הגדרות (`config.yaml`)

```yaml
deployment_mode: local
base_url: http://localhost:5000
host: 127.0.0.1
port: 5000

defaults:
  class_duration_minutes: 55   # משך שיעור בדקות
  weeks_ahead: 1               # 2 שבועות סה"כ
  urgent_window_hours: 24      # ברירת מחדל לחלון דחיפות
  digest_time: "06:30"         # שעת סיכום יומי

scheduler:
  daily_sync_hour: 6
  daily_sync_minute: 0
  cache_ttl_hours: 1           # לכמה זמן לשמור cache של BoostApp

boostapp:
  base_url: https://app.boostapp.co.il/Rest.php
  user_agent: "StudioCalendar/1.0"
  request_timeout: 15
```

---

## פתרון בעיות נפוצות

- **"BoostApp לא נגיש" / "לא נמצא סטודיו"** — בדוק שה-StudioUrl בלינק נכון.
  אפשר להעתיק את הלינק מהאתר של הסטודיו (לרוב מופיע כקישור בעמוד הראשי).
- **Google: "Access blocked: Studio Calendar has not completed the Google
  verification process"** — אתה במצב Testing. הוסף את עצמך ל-Test users
  ב-OAuth consent screen.
- **"needs_reauth"** — Google refresh token פג. התחבר מחדש דרך "התחבר עם Google".
  במצב Testing טוקן ה-refresh פג אחרי 7 ימים ללא שימוש; עבור ל-Published כדי
  להימנע מזה.
- **אין פוש ל-ntfy** — ודא שהרשית לאפליקציית ntfy באייפון לשלוח התראות
  (Settings → Notifications → ntfy → Allow Notifications).
- **אפל לא מתעדכנת** — iOS מרענן calendars subscribed בערך פעם ב-6 שעות.
  לא ניתן להאיץ זאת (אפל לא מתירה פוש ליומנים).

---

## תרומה ופיתוח

```bash
# הרצת כל הבדיקות:
py -m pytest -v

# הרצת מודול ספציפי:
py -m pytest tests/test_scraper.py -v
```

מבנה הפרויקט:
```
├── app.py                  # Flask app
├── auth.py                 # Google OAuth
├── scraper.py              # BoostApp HTML parser
├── calendar_ics.py         # ICS generation
├── calendar_google.py      # Google Calendar sync + diff
├── notifications.py        # ntfy push + digest
├── scheduler.py            # APScheduler jobs
├── db.py / models.py       # SQLAlchemy
├── config.py               # YAML + env loader
├── crypto.py               # Fernet encryption
├── templates/, static/     # UI
└── tests/                  # pytest
```

---

נבנה ב-❤️ עם Claude Code.
