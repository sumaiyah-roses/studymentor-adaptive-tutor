import os, re, json, math, time, random, sqlite3, hashlib
from collections import Counter
from groq import Groq

os.makedirs("data/uploads", exist_ok=True)
FAST = os.getenv("FAST_MODEL", "openai/gpt-oss-20b")   # also acts as the cross-model verifier
SMART = os.getenv("SMART_MODEL", "openai/gpt-oss-120b")
ASR = os.getenv("ASR_MODEL", "whisper-large-v3-turbo")
MIN_SCORE = float(os.getenv("MIN_SCORE", "0.3"))
P0, LEARN = 0.3, 0.15
_c = None

def groq():
    global _c
    if not _c:
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            for fname in ("groq_key.txt", "groq_key", ".env"):
                if os.path.exists(fname):
                    content = open(fname, encoding="utf-8").read().strip()
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"\'')
                            break
                        elif line and not line.startswith("#") and "=" not in line:
                            key = line
                            break
                    if key: break
        if not key:
            raise RuntimeError("No Groq key: put it alone in groq_key.txt next to core.py")
        _c = Groq(api_key=key)
    return _c

def ask(prompt, model=FAST):
    txt = "{}"
    try:
        r = groq().chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a precise tutoring assistant. Reply with JSON only."},
                {"role": "user", "content": prompt}
            ]
        )
        txt = r.choices[0].message.content or "{}"
    except Exception as e:
        try:
            # Fallback without json_object schema constraint
            r = groq().chat.completions.create(
                model=model,
                temperature=0.2,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": "You are a precise tutoring assistant. Reply with valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
            )
            txt = r.choices[0].message.content or "{}"
        except Exception:
            return {}

    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}

db = sqlite3.connect("data/app.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, loc TEXT, anchor TEXT, text TEXT, topic TEXT);
CREATE TABLE IF NOT EXISTS topics(name TEXT PRIMARY KEY, prereqs TEXT);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, topic TEXT, qtype TEXT, q TEXT, options TEXT, answer TEXT,
  expl TEXT, difficulty TEXT, chunk_id INTEGER, qhash TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS mastery(topic TEXT PRIMARY KEY, p REAL, n INTEGER, last REAL);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, qid INTEGER, resp TEXT, correct INTEGER, ts REAL);
""")

def rows(sql, *a):
    return [dict(r) for r in db.execute(sql, a).fetchall()]

def cite(c, n=None):
    return {"n": n, "id": c["id"], "source": c["source"], "loc": c["loc"],
            "url": f"/files/{c['source']}" + (f"#{c['anchor']}" if c["anchor"] else "")}

# ---------- Ingestion (PDF pages, PPTX slides, video/audio timestamps, text) ----------
def _chunks(text, size=900):
    out, cur = [], ""
    for part in re.split(r"\n\s*\n|(?<=[.!?])\s+", text):
        if len(cur) + len(part) > size and cur:
            out.append(cur.strip()); cur = ""
        cur += part + " "
    if cur.strip(): out.append(cur.strip())
    return out

def _ts(s): return f"{int(s)//60}:{int(s)%60:02d}"

def ingest(path, name):
    ext, items = name.rsplit(".", 1)[-1].lower(), []   # items: (loc, anchor, text)
    if ext == "pdf":
        from pypdf import PdfReader
        for i, p in enumerate(PdfReader(path).pages, 1):
            items += [(f"page {i}", f"page={i}", t) for t in _chunks(p.extract_text() or "")]
    elif ext == "docx":
        import zipfile, xml.etree.ElementTree as ET
        z = zipfile.ZipFile(path)
        tree = ET.fromstring(z.read("word/document.xml"))
        paras = []
        for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [node.text for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text]
            if texts:
                t = "".join(texts).strip()
                if t: paras.append(t)
        full_text = "\n\n".join(paras)
        chunks = _chunks(full_text)
        items = [(f"section {i+1}", f"section={i+1}", c) for i, c in enumerate(chunks)]
    elif ext == "pptx":
        from pptx import Presentation
        for i, s in enumerate(Presentation(path).slides, 1):
            t = "\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)
            if s.has_notes_slide: t += "\n" + s.notes_slide.notes_text_frame.text
            if t.strip(): items.append((f"slide {i}", "", t))
    elif ext in ("mp4", "mp3", "wav", "m4a", "webm", "mpeg"):   # Groq Whisper limit ~25MB per file
        with open(path, "rb") as f:
            r = groq().audio.transcriptions.create(file=(name, f.read()), model=ASR, response_format="verbose_json")
        segs = r.segments if hasattr(r, "segments") else r["segments"]
        g = lambda s, k: s[k] if isinstance(s, dict) else getattr(s, k)
        win, start = "", None
        for s in segs:
            start = g(s, "start") if start is None else start
            win += g(s, "text") + " "
            if g(s, "end") - start >= 60:
                items.append((_ts(start), f"t={int(start)}", win.strip())); win, start = "", None
        if win.strip(): items.append((_ts(start or 0), f"t={int(start or 0)}", win.strip()))
    else:
        try:
            content = open(path, "r", encoding="utf-8", errors="ignore").read()
            if "\x00" in content[:1000]: content = ""
        except Exception:
            content = ""
        items = [("text", "", t) for t in _chunks(content)]
    db.execute("DELETE FROM chunks WHERE source=?", (name,))
    db.executemany("INSERT INTO chunks(source,loc,anchor,text) VALUES(?,?,?,?)", [(name, *i) for i in items])
    db.commit()
    return len(items)

def tag_topics():
    cs = rows("SELECT id,text FROM chunks")
    if not cs: return
    sample = "\n".join(f"{c['id']}: {c['text'][:140]}" for c in cs[::max(1, len(cs) // 30)][:30])
    res = ask(f'List 3-6 main educational topics covered by these excerpts. JSON: {{"topics":["..."]}}\n{sample}', SMART)
    names = res.get("topics", [])
    if not names:
        names = ["Core Subject Matter"]
    for i in range(0, len(cs), 8):
        b = "\n".join(f"{c['id']}: {c['text'][:180]}" for c in cs[i:i + 8])
        try:
            m = ask(f'Topics: {names}\nAssign each excerpt ID to its single best topic from the list. JSON: {{"assign":{{"<id>":"<topic>"}}}}\n{b}').get("assign", {})
            for cid, t in m.items():
                if t in names:
                    db.execute("UPDATE chunks SET topic=? WHERE id=?", (t, int(cid)))
        except Exception:
            pass
    # Assign any untagged chunks to first topic
    if names:
        db.execute("UPDATE chunks SET topic=? WHERE topic IS NULL OR topic = ''", (names[0],))
    db.commit()
    # Keep only topics that actually have chunks
    assigned = [r["topic"] for r in rows("SELECT DISTINCT topic FROM chunks WHERE topic IS NOT NULL AND topic != ''")]
    names = [n for n in names if n in assigned] or assigned
    if names:
        pre = {}
        try:
            pre = ask(f'For each topic list prerequisite topics (only from the list). JSON: {{"prereqs":{{"topic":["..."]}}}}\n{names}', SMART).get("prereqs", {})
        except Exception:
            pass
        db.execute("DELETE FROM topics")
        for n in names:
            db.execute("INSERT OR REPLACE INTO topics VALUES(?,?)", (n, json.dumps([p for p in pre.get(n, []) if p in names and p != n])))
        db.commit()

# ---------- Retrieval (pure-Python BM25, no embeddings) ----------
STOP = set("a an the is are of to in and or what how why does do it this that for on with as be".split())
tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())

def search(q, k=5):
    cs = rows("SELECT * FROM chunks"); docs = [tok(c["text"]) for c in cs]
    if not docs: return []
    N, avg = len(docs), (sum(map(len, docs)) / len(docs)) or 1
    df, qt, out = Counter(t for d in docs for t in set(d)), set(tok(q)) - STOP, []
    for c, d in zip(cs, docs):
        tf = Counter(d)
        s = sum(math.log(1 + (N - df[t] + .5) / (df[t] + .5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (.25 + .75 * len(d) / avg))
                for t in qt if t in tf)
        if s > 0: out.append({**c, "score": s})
    return sorted(out, key=lambda x: -x["score"])[:k]

# ---------- Learner model (BKT + forgetting decay) ----------
def bkt(p, correct, diff, qtype):
    g = .25 if qtype == "mcq" else .05
    s = {"easy": .05, "medium": .1, "hard": .2}[diff]
    post = p * (1 - s) / (p * (1 - s) + (1 - p) * g) if correct else p * s / (p * s + (1 - p) * (1 - g))
    return post + (1 - post) * LEARN

def eff(m, half_life_days=7):
    if not m: return P0
    return P0 + (m["p"] - P0) * 0.5 ** ((time.time() - m["last"]) / 86400 / half_life_days)

def mastery_map():
    return {m["topic"]: m for m in rows("SELECT * FROM mastery")}

# ---------- Tutor (grounded, with refusal/flagging & practice questions) ----------
def tutor(msg):
    is_general = bool(re.search(r"^\s*(explain|summary|summarize|overview|tell me about|what is this|help me understand|start|what are the main concepts|intro|introduction|paper|notes)\b", msg.strip(), re.I))
    hits = search(msg)
    
    if is_general or not hits or hits[0]["score"] < MIN_SCORE:
        all_cs = rows("SELECT * FROM chunks LIMIT 6")
        if all_cs:
            hits = all_cs
            is_general = True

    ms = {t: round(eff(m), 2) for t, m in mastery_map().items()}
    if hits and (is_general or hits[0]["score"] >= MIN_SCORE):
        ctx = "\n".join(f"[{i+1}] ({h['source']}, {h['loc']}) {h['text']}" for i, h in enumerate(hits[:5]))
        r = ask(f"You are an expert, encouraging study tutor. "
                f"Explain and answer the student's request clearly, thoroughly, and concisely using ONLY the provided sources. "
                f"Cite sources using [1], [2], etc. where relevant. "
                f"Format the explanation with clear sections or bullet points. "
                f"Student mastery by topic (0-1): {ms}.\n"
                f"Sources:\n{ctx}\n"
                f"Student Request: {msg}\n"
                f'JSON: {{"covered":true,"answer":"..."}}', SMART)
        
        if r.get("covered") and r.get("answer"):
            used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", r["answer"]) if 0 < int(n) <= len(hits[:5])})
            if not used and hits:
                used = [1]
            citations = [cite(hits[n - 1], n) for n in used]
            
            # Generate a check-for-understanding practice question from cited chunk
            q_chunk = hits[used[0] - 1] if used else hits[0]
            q_topic = q_chunk.get("topic")
            if not q_topic:
                t_row = rows("SELECT name FROM topics LIMIT 1")
                q_topic = t_row[0]["name"] if t_row else "Core Concepts"
            
            q_data = gen_question_for_chunk(q_chunk, q_topic)
            
            return {
                "grounded": True,
                "answer": r["answer"],
                "citations": citations,
                "question": q_data
            }
            
    out = ask(f'Answer this question briefly from general knowledge: {msg}\nReply as {{"answer":"your answer"}}').get("answer", "No answer available.")
    return {
        "grounded": False,
        "citations": [],
        "question": None,
        "answer": "Your uploaded material does not cover this. Outside knowledge, not verified against your sources:\n\n" + out
    }

# ---------- Assessment (generate, verify, dedupe, grade) ----------
def gen_question_for_chunk(c, topic=None):
    if not topic:
        topic = c.get("topic") or "General"
    p = eff(mastery_map().get(topic))
    diff = "easy" if p < .35 else "medium" if p < .7 else "hard"
    try:
        r = ask(
            f'From this excerpt ONLY, create one high quality {diff} multiple-choice question (mcq) to check the student\'s understanding. '
            f'"options" must be a list of 4 distinct answers, starting with "A) ", "B) ", "C) ", "D) ". '
            f'"answer" must be the single letter A, B, C, or D corresponding to the correct option. '
            f'"explanation" is a brief explanation why that answer is correct. '
            f'JSON: {{"skip":false,"q":"Question text?","options":["A) ...","B) ...","C) ...","D) ..."],"answer":"A","explanation":"..."}}\n'
            f'Excerpt: {c["text"]}',
            SMART
        )
        if not r.get("skip") and r.get("q") and len(r.get("options", [])) >= 2:
            ans_letter = str(r.get("answer", "A")).strip().upper()[:1]
            if ans_letter not in ["A", "B", "C", "D"]:
                ans_letter = "A"
            norm = " ".join(tok(r["q"]))
            h = hashlib.md5(norm.encode()).hexdigest()
            existing = rows("SELECT id FROM questions WHERE qhash=?", h)
            if existing:
                qid = existing[0]["id"]
            else:
                cur_ = db.execute(
                    "INSERT INTO questions(topic,qtype,q,options,answer,expl,difficulty,chunk_id,qhash) VALUES(?,?,?,?,?,?,?,?,?)",
                    (topic, "mcq", r["q"], json.dumps(r.get("options", [])), ans_letter, r.get("explanation", ""), diff, c["id"], h)
                )
                db.commit()
                qid = cur_.lastrowid
            return {
                "id": qid,
                "topic": topic,
                "qtype": "mcq",
                "q": r["q"],
                "options": r.get("options", []),
                "difficulty": diff,
                "source": c["source"],
                "loc": c["loc"]
            }
    except Exception:
        pass
    return None

def pick_topics(n, scope=None):
    ts = [t["name"] for t in rows("SELECT DISTINCT topic AS name FROM chunks WHERE topic IS NOT NULL")]
    ts = [t for t in ts if not scope or t in scope] or ts
    ms = mastery_map()
    order = sorted(ts, key=lambda t: eff(ms.get(t)) + random.random() * .1)   # weakest/unseen first
    return [order[i % len(order)] for i in range(n)] if order else []

def same(a, b, qt):
    if qt == "mcq": return str(a).strip().upper()[:1] == str(b).strip().upper()[:1]
    if qt == "numeric":
        try: return abs(float(a) - float(b)) <= 1e-2 * max(1, abs(float(b)))
        except Exception: return False
    return True

def gen_question(topic):
    cs = rows("SELECT * FROM chunks WHERE topic=?", topic)
    if not cs:
        cs = rows("SELECT * FROM chunks")
    if not cs:
        return None
    random.shuffle(cs)
    for c in cs[:4]:
        q = gen_question_for_chunk(c, topic)
        if q:
            return q
    return None

def grade(qid, resp):
    q_rows = rows("SELECT * FROM questions WHERE id=?", qid)
    if not q_rows:
        return {"correct": False, "answer": "", "feedback": "Question not found", "mastery": 0.3, "citation": None}
    q = q_rows[0]
    c_rows = rows("SELECT * FROM chunks WHERE id=?", q["chunk_id"])
    c = c_rows[0] if c_rows else {"id": 0, "source": "", "loc": "", "anchor": ""}
    
    if q["qtype"] in ("mcq", "numeric"):
        ok, fb = same(resp, q["answer"], q["qtype"]), q["expl"]
    else:
        j = ask(f'Key: {q["answer"]}\nStudent: {resp}\nIs the student correct in substance? '
                f'JSON: {{"correct":true,"feedback":"one sentence"}}\nExcerpt: {c.get("text", "")}')
        ok, fb = bool(j.get("correct")), j.get("feedback", "")
    m = mastery_map().get(q["topic"])
    p2 = bkt(eff(m), ok, q["difficulty"], q["qtype"])
    db.execute("INSERT OR REPLACE INTO mastery VALUES(?,?,?,?)", (q["topic"], p2, (m["n"] if m else 0) + 1, time.time()))
    db.execute("INSERT INTO attempts(qid,resp,correct,ts) VALUES(?,?,?,?)", (qid, str(resp), int(ok), time.time()))
    db.commit()
    return {"correct": ok, "answer": q["answer"], "feedback": fb, "mastery": round(p2, 2), "citation": cite(c) if c.get("source") else None}

def report():
    wrong = rows("SELECT q.topic,q.q,q.answer,a.resp FROM attempts a JOIN questions q ON q.id=a.qid WHERE a.correct=0 ORDER BY a.id DESC LIMIT 10")
    if not wrong:
        return {"misconceptions": []}
    mis = ask(f'Name the likely misconception behind these wrong answers. JSON: {{"misconceptions":[{{"topic":"","misconception":""}}]}}\n{wrong}', SMART).get("misconceptions", [])
    return {"misconceptions": mis}

def reset_db(keep_files=False):
    db.execute("DELETE FROM chunks")
    db.execute("DELETE FROM topics")
    db.execute("DELETE FROM questions")
    db.execute("DELETE FROM mastery")
    db.execute("DELETE FROM attempts")
    db.commit()
    if not keep_files:
        for f in os.listdir("data/uploads"):
            fp = os.path.join("data/uploads", f)
            if os.path.isfile(fp):
                try: os.remove(fp)
                except Exception: pass