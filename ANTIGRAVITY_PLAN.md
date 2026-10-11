# Bahi-Khata Bolo: Build Plan for the Coding Agent

> Paste this whole file as the first prompt to Antigravity. Work phase by phase. Do not start a phase until the previous phase's **acceptance check** passes.

## 0. Mission

**One user:** a kirana (small shop) owner who keeps a handwritten credit ledger (bahi-khata).
**One task:** "Who owes me how much?"
**One flow:** photograph ledger page -> Sarvam Vision turns handwriting into a clean table -> owner reviews/fixes it -> owner asks a question **by voice** in their own language -> app answers **by voice** with a number computed by code, plus a ready-to-send reminder message.

### Hackathon rules this build must respect
1. Keep scope small. One flow that works end to end beats a long feature list.
2. Build for Indian languages (Hindi first; Tamil/Gujarati/Marathi if time allows; code-mixed speech must work).
3. Test with real input: real handwriting, real voice, noisy room.
4. Keep API keys private: environment variables only, never commit.
5. Be demo-ready on time. Stage 1-3 working is a better demo than stages 1-6 half working.

### Core design principle (our differentiator)
**The LLM never does arithmetic or decides balances.** Code computes every balance from the database. The LLM only (a) turns a spoken question into a structured intent and (b) phrases the answer. Every answer shows the ledger rows it was computed from. This makes the system auditable and trustworthy.

---

## 1. Sarvam APIs used (verified against docs.sarvam.ai)

Install: `pip install -U sarvamai`. Client:

```python
import os
from sarvamai import SarvamAI
client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
```

| Stage | API | Key facts from the docs |
|---|---|---|
| Ledger photo -> table | **Document AI (Sarvam Vision)**: `client.doc_ai.extract(...)` (schema-based fields) and `client.doc_ai.digitise(...)` (full OCR) | PNG/JPG/PDF/ZIP; max 10 pages; async job: create -> poll `get_status` -> `get_results` (extract) or `get_download_url` (digitise). Terminal states: `completed`, `partially_completed`, `failed`, `rejected`. Uses `language` (e.g. `hi-IN`) and `output_format` (`json` for extract; `md`/`html`/`json` for digitise). **Do NOT use `language_code` or `"markdown"`** here. `schema` must be passed as a **JSON string** (`json.dumps`), not a dict. Each schema field needs a `type` and a non-empty `description`; root must be `type: object`; max nesting depth 4. |
| Voice question -> text | **Speech-to-Text, Saaras v3**: `client.speech_to_text.transcribe(...)` | `model="saaras:v3"`, `mode="transcribe"` (also `codemix`, `verbatim`, `translit`, `translate`). `language_code="unknown"` auto-detects and the response includes the detected `language_code`. Sync REST accepts **max 30 seconds**. Response has `transcript` and `language_code`. |
| Understand question + phrase answer | **Chat, Sarvam-105B**: `client.chat.completions(model="sarvam-105b", messages=[...])` | OpenAI-style response (`choices[0].message.content`). Thinking is on by default; pass `reasoning_effort=None` to disable it for speed. Supports OpenAI-compatible `response_format` for structured JSON (read the Chat Completions overview page for the exact shape before using it). |
| Answer -> voice | **Text-to-Speech, Bulbul v3**: `client.text_to_speech.convert(...)` | `model="bulbul:v3"`, `speaker="shubh"`, `target_language_code="hi-IN"`. Response `audios` is a **list of base64 strings**; decode with `base64.b64decode("".join(response.audios))`. Text limit **2500 chars** for v3. |
| Reminder message (stretch) | Chat 105B, or Mayura translate | Generate a polite reminder in the customer's language. |

### Doc lookup rules for the agent
Before writing any Sarvam call, confirm the signature in the docs. Useful sources:
- `https://docs.sarvam.ai/llms.txt` (index) and `https://docs.sarvam.ai/llms-full.txt`
- Append `.md` to any docs page URL for clean Markdown.
- Pages: Document Intelligence Overview, Speech-to-Text REST API, Text-to-Speech REST API, Chat Completions Overview, Errors & Troubleshooting, Rate Limits.
- Sarvam's official MCP server (`sarvam-mcp`, Python 3.11+, needs `SARVAM_API_KEY`) can be connected if Antigravity supports MCP.
- Test calls quickly in the dashboard playground (dashboard.sarvam.ai) before coding around them.

> Known uncertainties to resolve by reading docs / testing, not guessing:
> 1. The exact JSON-schema syntax for an **array of row objects** in Extract (`items`). Test in the dashboard playground first.
> 2. Whether the dashboard/API serves Vision 1.5 or 2.1 for your key (handwriting quality differs). Check the model page.
> 3. Exact `response_format` shape for structured JSON in chat.
> 4. Which language codes Bulbul v3 supports vs what STT can return (handled by `TTS_FALLBACK_LANGUAGE`).

---

## 2. Tech stack (fast and demo-safe)

- **Python 3.11**, **Streamlit** UI (camera/file upload, `st.audio_input` for the mic, `st.data_editor` for fixing rows, `st.audio` for playback).
- **SQLite** via the stdlib `sqlite3`.
- `python-dotenv`, `rapidfuzz` (fuzzy customer-name matching), `pandas`, `pytest`.
- `requirements.txt`: `sarvamai`, `streamlit`, `python-dotenv`, `rapidfuzz`, `pandas`, `pytest`.

## 3. Repository layout

```
bahi-khata-bolo/
  app.py                  # Streamlit UI, thin: calls src/*
  src/
    config.py             # loads .env, typed settings, validates key present
    sarvam_client.py      # ONLY file that imports sarvamai; retries, timeouts, error mapping
    ledger_extract.py     # photo -> schema extract -> validated rows
    db.py                 # sqlite schema + queries (balances computed here)
    normalize.py          # amounts, dates, names, fuzzy match
    intents.py            # transcript -> structured intent (LLM) + deterministic fallback
    answers.py            # intent + DB -> numbers + supporting rows + spoken text
    voice.py              # stt() and tts() wrappers, base64 decode, language fallback
    reminders.py          # (stretch) reminder text + wa.me link
  tests/
  fixtures/               # cached sarvam responses for DEMO_MODE (sample only, no real names in git)
  data/                   # sqlite db (gitignored)
  .env  .env.example  .gitignore  requirements.txt  README.md
```

## 4. Data model

`ledger_entries(id, customer_raw, customer_key, entry_date, description, amount, entry_type, source_image, needs_review, created_at)`
- `entry_type` in `{credit_given, payment_received}`. Balance for a customer = sum(credit_given) - sum(payment_received).
- `customer_key` = normalized name used for grouping (merge "Sharma ji", "Sharma Ji", the same name in a different spelling).
- `needs_review` = true when amount is missing or non-numeric, or the name is empty.

Extract schema (starting point; verify array syntax in the playground):

```python
schema = {
  "type": "object",
  "properties": {
    "entries": {
      "type": "array",
      "description": "Every row of the handwritten credit ledger page, top to bottom",
      "items": {
        "type": "object",
        "properties": {
          "date": {"type": "string", "description": "Date written on the row, as written"},
          "customer_name": {"type": "string", "description": "Name of the customer in the original script"},
          "description": {"type": "string", "description": "Items or note written for this row"},
          "amount": {"type": "number", "description": "Amount in rupees for this row"},
          "entry_type": {"type": "string", "enum": ["credit_given", "payment_received"],
                         "description": "credit_given if goods were given on credit; payment_received if the customer paid"}
        }
      }
    }
  }
}
```
Fallback if array extraction misbehaves: run `digitise(output_format="md" or "html")`, then have the chat model convert the table into the same row JSON, then validate in code.

---

## 5. Phases (with time boxes and acceptance checks)

### Phase 0: Scaffold and prove the key works (about 15 min)
Actions:
1. Create the layout above, `requirements.txt`, venv, install.
2. `config.py`: load `.env` with `python-dotenv`; fail fast with a clear message if `SARVAM_API_KEY` is missing or still the placeholder. Never print the key.
3. `sarvam_client.py`: one function per API with: request timeout, retry with exponential backoff on 429/5xx, and clean exceptions (map 400/402/403/404/409/413/422/429 to readable messages; read Errors & Troubleshooting).
4. `scripts/smoke_test.py`: one tiny call to chat (`reasoning_effort=None`), one TTS call that saves `out.wav`.

**Acceptance:** `python scripts/smoke_test.py` prints a chat reply and writes a playable `out.wav`.

### Phase 1: Photo -> table (the core, about 60 min)
Actions:
1. `ledger_extract.py`: `extract_ledger(image_path, language) -> list[Row]`.
   - `with open(path,"rb") as f: job = client.doc_ai.extract(file=[(name, f, mime)], schema=json.dumps(schema), language=LEDGER_LANGUAGE, output_format="json")`.
   - Poll `client.doc_ai.get_status(job_id=job.job_id)` every `DOC_POLL_INTERVAL_SECONDS`, until a terminal state or `DOC_TIMEOUT_SECONDS`.
   - On `completed` or `partially_completed`: `client.doc_ai.get_results(job_id=job.job_id).result`. Warn the user on `partially_completed`.
2. `normalize.py`: parse amounts (strip currency symbols/commas, convert Devanagari/regional digits to ASCII digits), parse dates leniently, set `needs_review`, compute `customer_key` (trim, lowercase, collapse whitespace, strip honorifics like "ji").
3. Streamlit page 1: upload/take photo -> "Read ledger" button -> show original image beside an editable `st.data_editor` table. Rows with `needs_review` highlighted. "Confirm and save" writes to SQLite.
4. Cache each successful Extract response to `fixtures/` (for DEMO_MODE).
5. Save the raw job_id and status on screen in a collapsible "Debug" panel.

**Acceptance:** a real handwritten ledger photo produces a table where most rows are right; the user can fix the rest and save. Record the row-level accuracy on 3 real pages (rows correct / total) in `README.md`. This number goes in the pitch.

### Phase 2: Deterministic ledger queries (about 30 min)
Actions:
1. `db.py` functions: `balance_of(customer_key)`, `outstanding_total()`, `top_debtors(n)`, `entries_of(customer_key)`, `list_customers()`.
2. `answers.py`: given an intent, return `{"numbers": {...}, "rows": [...], "text_en": "..."}` computed **only** from the DB.
3. Typed-input box in the UI that runs a query without voice (this is the demo fallback).
4. Unit tests with a small fixture DB: balances, partial payments, unknown customer, duplicate-name merge.

**Acceptance:** `pytest` passes; typing "Sharma ji" returns the correct balance and shows the supporting rows.

### Phase 3: Voice in (about 45 min)
Actions:
1. `voice.py: stt(wav_bytes) -> (transcript, language_code)`:
   `client.speech_to_text.transcribe(file=..., model=STT_MODEL, language_code=STT_LANGUAGE, mode=STT_MODE)`.
   Reject audio longer than `MAX_AUDIO_SECONDS` with a friendly message (sync limit is 30 s).
2. UI: `st.audio_input` -> show the transcript and detected language on screen.
3. `intents.py: parse_intent(transcript, known_customers) -> Intent`:
   - Call `client.chat.completions(model=CHAT_MODEL, reasoning_effort=None, ...)` with a strict system prompt: output **JSON only**: `{"intent": "balance_of|outstanding_total|top_debtors|entries_of|unknown", "customer_name": str|null, "n": int|null}`. Use `response_format` for structured JSON after confirming its syntax in the docs; always parse defensively.
   - Resolve `customer_name` with `rapidfuzz` against `known_customers` (threshold about 80). If two close matches, ask a clarifying question instead of guessing. If none, say so.
   - Deterministic fallback: if the LLM output is invalid, try a simple keyword/name match.

**Acceptance:** speaking a Hindi (or code-mixed) question like "Sharma ji ka kitna baaki hai?" shows the correct transcript, intent, and resolved customer. Test once in a noisy room.

### Phase 4: Voice out (about 30 min)
Actions:
1. `voice.py: tts(text, language_code) -> wav_bytes`:
   `client.text_to_speech.convert(text=..., target_language_code=lang, model=TTS_MODEL, speaker=TTS_SPEAKER)`; decode `base64.b64decode("".join(response.audios))`. If `lang` is unsupported, use `TTS_FALLBACK_LANGUAGE`. Truncate/split text above `TTS_MAX_CHARS`.
2. Answer phrasing: for `hi-IN` and `en-IN` use fixed templates; for other languages ask 105B to phrase the sentence **with the numbers given verbatim**, then **verify the digits appear in the output**; if not, fall back to a template.
3. UI: autoplay the audio and show the text and the supporting ledger rows below it. Label: "Computed from your ledger, not guessed."

**Acceptance:** full loop works: speak a question, hear a correct spoken answer in the same language.

### Phase 5: Reminder message (stretch, about 30 min)
Actions:
1. `reminders.py`: generate a polite, short reminder in the customer's language (105B) including the exact amount from the DB.
2. Show it with a copy button and a `https://wa.me/?text=<urlencoded>` link so the owner picks the contact and sends it.

**Acceptance:** clicking the link opens WhatsApp with the message prefilled.

### Phase 6: Demo hardening (reserve the last 30-40 min)
1. `DEMO_MODE=true` path: use cached `fixtures/` for Extract, STT, TTS so the demo survives bad Wi-Fi. Visible toggle in the sidebar.
2. Every stage shows its output on screen (extracted rows, transcript, intent JSON, answer text) so judges can see the pipeline.
3. Friendly error toasts, never raw stack traces.
4. Clean `README.md`: problem, architecture diagram (ASCII), the accuracy number, how to run, and the privacy note.
5. A 2-minute demo script (below) and a screen recording of one perfect run as a backup.
6. Final check that `.env` is not tracked: `git ls-files | grep -E '^\.env$'` must return nothing.

---

## 6. Guardrails for the agent
- Never hardcode or log the API key. Read it only via `config.py`.
- Only `sarvam_client.py` imports `sarvamai`. All other modules call its functions (easy to mock and to switch to DEMO_MODE).
- Never let the LLM compute or alter a balance.
- Handle `partially_completed`, `failed` and `rejected` Document AI jobs gracefully.
- Respect limits: 10 pages per Document AI job, 30 s per sync STT request, 2500 chars per TTS request.
- Keep real customer names out of git: use your own or a consented sample ledger; `data/` and `fixtures/private/` are gitignored.
- Do not add features beyond the phases above until Phase 4 passes.

## 7. Demo script (about 2 minutes)
1. (15 s) Problem: "Lakhs of shopkeepers track credit in handwritten ledgers; chasing payments is slow and error-prone."
2. (30 s) Photograph a real handwritten ledger page; show it becoming a table; fix one wrong cell to show human-in-the-loop.
3. (30 s) Speak in Hindi: "Sharma ji ka kitna baaki hai?" Show transcript, intent, and the matching rows.
4. (20 s) The app answers aloud; point out "computed from your ledger, not guessed."
5. (15 s) Tap the reminder link: WhatsApp opens with the message ready.
6. (10 s) Close with the accuracy number and the stack: Vision + Saaras + Sarvam-105B + Bulbul.

## 8. Definition of done
- [ ] Real ledger photo -> editable table -> saved
- [ ] Voice question (Hindi/code-mixed) -> correct spoken answer with supporting rows
- [ ] Typed fallback works without voice
- [ ] DEMO_MODE works offline
- [ ] Accuracy number measured on real pages and written in README
- [ ] `.env` untracked; `.env.example` committed
- [ ] One backup screen recording of a clean run
