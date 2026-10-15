import json, re, shutil
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import core

app = FastAPI()
class Chat(BaseModel): message: str
class Quiz(BaseModel): n: int = 5; topics: list[str] | None = None
class Ans(BaseModel): qid: int; response: str

@app.get("/")
def index(): return FileResponse("static/index.html")

@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    path = f"data/uploads/{file.filename}"
    with open(path, "wb") as f: shutil.copyfileobj(file.file, f)
    n = core.ingest(path, file.filename)
    try:
        core.tag_topics()
    except Exception as e:
        print("tag_topics error:", e)
    return {"chunks": n}

@app.post("/api/chat")   # router: assessment intent -> quiz, otherwise grounded tutor
def chat(b: Chat):
    if re.search(r"\b(quiz me|test me|practice questions?|mock exam)\b", b.message, re.I):
        return {"route": "quiz", "grounded": True, "answer": "Starting a quiz on your weakest topics.", "citations": []}
    return core.tutor(b.message)

@app.post("/api/quiz")
def quiz(b: Quiz):
    qs = [core.gen_question(t) for t in core.pick_topics(min(b.n, 10), b.topics)]
    return [q for q in qs if q]

@app.post("/api/answer")
def answer(b: Ans): return core.grade(b.qid, b.response)

@app.get("/api/topics")
def topics():
    ms = core.mastery_map()
    return [{"name": t["name"], "prereqs": json.loads(t["prereqs"]), "p": core.eff(ms.get(t["name"])),
             "n": ms.get(t["name"], {}).get("n", 0),
             "chunks": core.rows("SELECT COUNT(*) c FROM chunks WHERE topic=?", t["name"])[0]["c"]}
            for t in core.rows("SELECT * FROM topics")]

@app.get("/api/report")
def report(): return core.report()

@app.get("/api/status")
def status():
    import os
    chunks = core.rows("SELECT COUNT(*) c FROM chunks")[0]["c"]
    topics = core.rows("SELECT COUNT(*) c FROM topics")[0]["c"]
    attempts = core.rows("SELECT COUNT(*) c FROM attempts")[0]["c"]
    files = [f for f in os.listdir("data/uploads") if os.path.isfile(os.path.join("data/uploads", f))] if os.path.exists("data/uploads") else []
    return {"chunks": chunks, "topics": topics, "attempts": attempts, "files": files}

@app.post("/api/clear")
def clear():
    core.reset_db(keep_files=False)
    return {"status": "ok"}

@app.post("/api/reindex")
def reindex():
    import os
    core.reset_db(keep_files=True)
    count = 0
    if os.path.exists("data/uploads"):
        for f in os.listdir("data/uploads"):
            fp = os.path.join("data/uploads", f)
            if os.path.isfile(fp):
                count += core.ingest(fp, f)
    if count:
        core.tag_topics()
    return {"chunks": count}

app.mount("/files", StaticFiles(directory="data/uploads"))
