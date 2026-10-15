# StudyMentor: Source-Grounded Adaptive Study Companion

Multimodal AI Hackathon 2026, Track D (Personalized Tutoring & Adaptive Learning).

Upload lecture videos, slides and textbooks. StudyMentor builds a source-linked knowledge base, answers questions with citations that open the exact page, slide or timestamp, flags anything your material does not cover, and runs adaptive quizzes driven by a per-topic learner model.

The stack is deliberately light: no local ML models. Groq hosts the LLMs and Whisper, retrieval is pure-Python BM25, storage is SQLite, and the UI is one static HTML file.

## Run

Requires Python 3.10+ and a free Groq API key.

```
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

Provide your key in **one** of two ways:

1. Create a file named `groq_key.txt` next to `core.py` containing only the key (no quotes, no `GROQ_API_KEY=`), or
2. Set an environment variable with no spaces around `=`:
   `set GROQ_API_KEY=your_key` (Windows cmd) or `export GROQ_API_KEY=your_key` (macOS/Linux).

Then start the server from the project folder:

```
uvicorn main:app --reload
```

Open http://127.0.0.1:8000 and:

1. **Upload** a PDF, PPTX, `.txt`, or video/audio file (up to ~25 MB). Ingestion and topic tagging take 30 to 60 seconds.
2. **Tutor**: ask full questions about the material. Say "quiz me" to jump to a quiz.
3. **Quiz**: answer verified questions on your weakest topics and get cited feedback.
4. **Dashboard**: see per-topic mastery, prerequisites and likely misconceptions.

`groq_key.txt`, `.env` and `data/` are listed in `.gitignore`. Never commit a key.

## Architecture

| Component | Where | What it does |
|---|---|---|
| Ingestion | `core.ingest` | PDF pages, PPTX slides (with speaker notes), video/audio (Whisper segments grouped into ~60 s windows) and text become chunks with a locator. Citations link to `#page=N` or `#t=SECONDS`. |
| Curriculum | `core.tag_topics` | Extracts topics, tags every chunk to one, and proposes prerequisite links. |
| Tutor | `core.tutor` | BM25 retrieval, answers only from retrieved sources with `[n]` citations. If the best score is below `MIN_SCORE`, or the model judges the sources insufficient, the reply is flagged and any outside knowledge is labelled as unverified. |
| Assessment + Verifier | `core.gen_question` | The generator model writes an MCQ, short-answer or numerical question from a single chunk. The verifier model answers it blind from the same excerpt. Mismatches are dropped, and near-duplicates are rejected by token overlap against the topic's history. |
| Learner model | `core.bkt`, `core.eff` | Bayesian Knowledge Tracing per topic with difficulty-adjusted slip, a guess rate by question type, and forgetting decay toward the prior (7-day half-life). New students start at the prior, so unseen and weak topics are asked first, which acts as a diagnostic. |
| Router | `main.chat` | Quiz intent goes to Assessment, everything else to the Tutor. |

Files: `main.py` (FastAPI routes), `core.py` (all logic), `static/index.html` (UI), `data/` (SQLite DB and uploads, created at runtime).

## Configuration

Optional environment variables (see `.env.example`; the app does not read `.env` itself):

| Variable | Default | Purpose |
|---|---|---|
| `SMART_MODEL` | `openai/gpt-oss-120b` | Topic extraction, question generation, grounded answers |
| `FAST_MODEL` | `openai/gpt-oss-20b` | Chunk tagging, blind verification, grading |
| `ASR_MODEL` | `whisper-large-v3-turbo` | Audio and video transcription |
| `MIN_SCORE` | `0.3` | BM25 cutoff below which the tutor refuses. Lower it if it refuses too much, raise it if it answers off-topic questions. |

If Groq retires a model, set the variable to a current model name from Groq's model list.

## Known limitations

- The generator and verifier come from the same model family, which weakens the independence of the answer-key check.
- The default models are reasoning models, so each call is slower than with a small chat model.
- Topic prerequisite links are LLM-proposed and not validated.
- BM25 is lexical, so paraphrased questions can miss relevant chunks.
- Single-student state: mastery is stored globally, not per user.

## Not included

Diagram and figure captioning, RAGAS evaluation, simulated-student runs, course flow map, study schedule, and audio or Hindi support.
