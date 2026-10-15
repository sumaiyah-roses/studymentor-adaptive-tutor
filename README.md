# Study Companion

Source-grounded tutor, adaptive quizzes and a learner model. No local ML models: Groq hosts the LLM and Whisper; retrieval is pure-Python BM25; storage is SQLite; the UI is one static HTML file.

## Run
```
pip install -r requirements.txt
export GROQ_API_KEY=...        # Windows: set GROQ_API_KEY=...
uvicorn main:app --reload
```
Open http://127.0.0.1:8000, upload material, then use Tutor, Quiz and Dashboard.

## How it maps to the agents
- Ingestion (`core.ingest`): PDF page, PPTX slide, video/audio timestamp (Whisper segments) and text become chunks with a locator. Citations link to `#page=N` or `#t=SECONDS`.
- Curriculum (`core.tag_topics`): topics, chunk tags and prerequisite edges.
- Tutor (`core.tutor`): BM25 retrieval, answers only from sources with `[n]` citations. Below the score threshold or when the model says it is not covered, the reply is flagged and outside knowledge is labelled separately.
- Assessment and Verifier (`core.gen_question`): the 70B model writes a question from one chunk, the 8B model solves it blind, mismatches are dropped, and near-duplicates are rejected.
- Learner model (`core.bkt`, `core.eff`): Bayesian Knowledge Tracing per topic with difficulty-adjusted slip, guess rate by question type, and forgetting decay (7-day half-life). New students start at the prior, so the weakest/unseen topics are asked first (diagnostic).
- Router (`main.chat`): quiz intent goes to Assessment, everything else to the Tutor.

## Tuning
`MIN_SCORE` controls refusal strictness. Model names are env overrides (see `.env.example`) in case Groq deprecates one.

## Not included
Diagram/figure captioning, RAGAS evaluation, simulated-student runs, flow map, study schedule, audio and Hindi support.
