# Gemini WebRTC Safety Call Service (FastAPI)

FastAPI service that dynamically initiates real-time WebRTC voice AI check-in calls with truck drivers via Gemini Live upon receiving safety event webhooks, records calls in 1-channel mono MP3, performs AI conversation analysis, uploads recordings to Google Cloud Storage (with Signed URLs), logs everything into PostgreSQL, and dispatches analysis results via webhook.

---

## 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 2. Database & Migrations (PostgreSQL + Alembic)

### Run Alembic Migrations:
```bash
alembic upgrade head
```

### Database Tables:
1. **`events`**: Tracks all incoming safety event requests from `/api/event` (CDL, driver name, truck speed, speed limit, speeding count, raw payload, status).
2. **`calls`**: Tracks call lifecycle (status, ringing/answered/ended timestamps, duration, talk duration, Gemini AI costs & token usage, GCS URI, GCS Signed URL).
3. **`call_analyses`**: Stores AI conversation analysis (sentiment, driver/assistant tone, confirmed fatigue, reason, evidence, conflict detection, speech segments, call summary, webhook dispatch status).

---

## 3. Configuration (.env)

Ensure `.env` is configured:
```env
# WebRTC & STOMP Service
CALL_SUPPORT_URL=https://dev-support.gleld.com
CALL_SERVICE_URL=https://dev-api.gleld.com
CALL_WSS_URL=wss://dev-api.gleld.com/call-service/ws/calls

CALL_AUTH_USERNAME=safety.ops@gmail.com
CALL_AUTH_PASSWORD=bbEYu2L6!
CALL_AUTH_HASH=

# Gemini Live AI
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_VOICE=Aoede
CALL_TYPE=DIRECT

# Google Cloud Storage (GCS)
APP_ENV=dev
GCS_BUCKET_NAME=util-dev
GCS_CREDS_PATH=app/utils/creds/green-light-eld-access-dev.json
RECORDINGS_DIR=app/utils/recordings

# Database (PostgreSQL)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ai_assistant_db
DB_USER=akbarov
DB_PASSWORD=akbarov
DATABASE_URL=postgresql+asyncpg://akbarov:akbarov@localhost:5432/ai_assistant_db
DATABASE_URL_SYNC=postgresql+psycopg2://akbarov:akbarov@localhost:5432/ai_assistant_db

# Call Analysis & Webhook
ANALYSE_PROVIDER=gemini
ANALYSE_MODEL=gemini-3.7-flash
ANALYSIS_WEBHOOK_URL=http://localhost:8000/api/webhook/call-analysis
```

---

## 4. Run FastAPI Service

Run directly with Python:
```bash
python main.py
```

Or via Uvicorn:
```bash
uvicorn main:app --host 0.0.0.0 --port 8787 --workers 2 --limit-concurrency 400
```

---

## 5. Trigger a Call via REST API

### `POST /api/event`

**Headers:**
`Content-Type: application/json`

**Request Body:**
```json
{
  "call_id": "call_123456789",
  "driver_full_name": "John Doe",
  "unit_number": "345",
  "company": "Robertson Trucks",
  "cdl": "23423456",
  "phone_number": "+1 555 123 4567",
  "event": "Severe Speeding",
  "truck_speed": "60 mph",
  "speed_limit": "50 mph",
  "speeding_today": 5,
  "datetime": "2026-09-08T09:32:18Z",
  "event_assessment": "2 severe speeding events detected on the same day."
}
```

**Response (HTTP 202 Accepted):**
```json
{
  "status": "accepted",
  "message": "Call initiated for driver",
  "call_id": "call_123456789",
  "cdl": "23423456",
  "event": "Severe Speeding",
  "active_calls_count": 1
}
```

---

## 6. Complete Post-Call Pipeline Flow

Upon call completion (`CallClient.close()`):
1. **Audio Recording**: Both driver and Gemini AI audio are mixed into **1 single Mono channel** at 24kHz and saved to `app/utils/recordings/{call_id}.mp3`.
2. **AI Voice Analysis**: `analyse(output_path)` runs automated structured conversation analysis.
3. **GCS Upload & Signed URL**: The audio file is uploaded to `gs://<bucket>/ai_call_recordings/{call_id}.mp3` and a 7-day Google Cloud Storage Signed URL is generated.
4. **Local Cleanup**: The local `.mp3` file is deleted from disk after successful analysis and GCS upload.
5. **Database Logging**: `events`, `calls`, and `call_analyses` records are updated in PostgreSQL.
6. **Webhook Dispatch**: The analysis result with `call_id` and `recording_url` (Signed URL) is POSTed to `ANALYSIS_WEBHOOK_URL`.

---

## 7. Close Call by CDL (Python Function)

```python
from app.utils import close_call_by_cdl

# Close active call immediately by CDL
was_closed = await close_call_by_cdl("23423456")
```
