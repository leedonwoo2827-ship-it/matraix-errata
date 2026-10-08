"""산물 검수엔진 웹앱 — run.bat(인자 없이)이 띄운다.  http://127.0.0.1:5191

★ 화면은 **CLI 를 대신 눌러 주는 리모컨**이다(longform/web/server.py 와 같은 방식). 실제 일은
  `python -m errata <단계>` 하위 프로세스가 하고, 화면은 그 로그를 흘려 보여 준다. 화면에서 한 일과 CLI 에서 한 일이 늘 같다.
★ 한 번에 하나만 돈다. 무인 운전 중에 브라우저를 닫아도 서버 창이 켜져 있으면 계속 돈다.
★ 127.0.0.1 에만 묶는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from errata.config import DATA, JOBS, REPO, SOURCE_KEYS, SOURCE_LABELS, default_job, load_job
from errata.util import read_json, read_jsonl

STATIC = Path(__file__).resolve().parent / "static"
LOG = DATA / "logs" / "web-run.log"
STAGES = {"ingest", "panel", "machine", "review", "verify", "merge", "errata", "check", "all", "trial", "status"}

app = FastAPI(title="산물 검수엔진")


class Runner:
    """하위 프로세스 하나. 로그는 파일로 받아 화면이 offset 으로 읽어 간다. (longform Runner 그대로)"""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.cmd: List[str] = []
        self.started = 0.0
        self.ended = 0.0
        self.code: Optional[int] = None
        self.chain: List[List[str]] = []     # 앞 단계가 성공하면 이어서 돌릴 것(「＋ 새 시험 시작」의 ingest → panel 등)
        self.lock = threading.Lock()

    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def start(self, args: List[str], chain: Optional[List[List[str]]] = None) -> None:
        with self.lock:
            if self.running():
                raise HTTPException(409, "이미 실행 중입니다 — 끝나거나 중지한 뒤 다시 누르십시오")
            LOG.parent.mkdir(parents=True, exist_ok=True)
            # 지난 실행 로그는 날짜를 붙여 남긴다(4일 운전 기록이 덮이지 않게)
            if LOG.exists() and LOG.stat().st_size:
                LOG.replace(LOG.with_name(f"web-run_{time.strftime('%m%d-%H%M%S')}.log"))
            fh = LOG.open("w", encoding="utf-8")
            self.chain = list(chain or [])
            self._launch(args, fh)

    def _launch(self, args: List[str], fh) -> None:
        fh.write(f"$ run.bat {' '.join(args)}\n")
        fh.flush()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, "-m", "errata", *args], cwd=str(REPO),
                                     stdout=fh, stderr=subprocess.STDOUT, env=env)
        self.cmd, self.started, self.ended, self.code = args, time.time(), 0.0, None
        threading.Thread(target=self._wait, args=(fh,), daemon=True).start()

    def _wait(self, fh) -> None:
        code = self.proc.wait()
        fh.write(f"\n[종료 코드 {code}]\n")
        if code == 0 and self.chain:              # 이어서 — 같은 로그에 붙인다
            self._launch(self.chain.pop(0), fh)
            return
        self.chain = []
        self.code, self.ended = code, time.time()
        fh.close()

    def stop(self) -> None:
        self.chain = []
        if self.running():
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.terminate()

    def info(self) -> Dict[str, Any]:
        return {"running": self.running(), "cmd": self.cmd, "started": self.started,
                "ended": self.ended, "code": self.code, "next": [c[0] for c in self.chain]}


RUN = Runner()


def _job(name: str):
    try:
        return load_job(name or default_job())
    except SystemExit as e:
        raise HTTPException(404, str(e))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/setup", response_class=HTMLResponse)
def setup_page() -> str:
    return (STATIC / "setup.html").read_text(encoding="utf-8")


# ── 과목 세팅 — 대화로 정하고 사람이 확정한다(errata/subjects.py) ──────────────────────
@app.get("/api/subjects")
def api_subjects(job: str = "") -> Dict[str, Any]:
    from errata import subjects as S
    j = _job(job)
    return {"job": j.name, "label": j.pack().get("label", ""), "items": S.subjects(j), "fields": S.FIELDS}


@app.get("/api/subject")
def api_subject(job: str = "", n: str = "") -> Dict[str, Any]:
    from errata import subjects as S
    j = _job(job)
    c = read_json(S.chat_path(j, n), {}) or {}
    conf = S.load(j).get(n) or {}
    return {"n": n, "messages": c.get("messages") or [], "draft": c.get("draft"), "draft_text": S.render(c.get("draft") or {}),
            "confirmed": conf, "confirmed_text": S.render(conf), "status": next((s["status"] for s in S.subjects(j) if s["n"] == n), "")}


@app.post("/api/subject/chat")
def api_subject_chat(body: Dict[str, Any]) -> Dict[str, Any]:
    """한 차례 — 모델 호출(구독 claude -p)이라 수십 초 걸린다."""
    from errata import subjects as S
    j = _job(body.get("job", ""))
    try:
        r = S.turn(j, str(body.get("n")), str(body.get("message") or ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"세팅 조교 호출 실패: {e}")
    r["draft_text"] = S.render(r.get("draft") or {})
    return r


@app.post("/api/subject/confirm")
def api_subject_confirm(body: Dict[str, Any]) -> Dict[str, Any]:
    from errata import subjects as S
    j = _job(body.get("job", ""))
    try:
        s = S.confirm(j, str(body.get("n")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"confirmed": s, "confirmed_text": S.render(s)}


# ── 처음 시작 — 시작 프롬프트를 붙여 넣는 대화(errata/start.py). 만들기·실행은 버튼으로 ─────────────
def _start_state() -> Dict[str, Any]:
    from errata import start as ST
    c = ST.load()
    spec = c.get("spec")
    slug = c.get("created") or ""
    panel = ""
    if slug and (JOBS / slug / "01_선정" / "패널명부.md").exists():
        panel = (JOBS / slug / "01_선정" / "패널명부.md").read_text(encoding="utf-8")
    tail = ""
    if LOG.exists():
        tail = "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-14:])
    return {"messages": c.get("messages") or [], "spec": spec, "check": ST.check(spec) if spec else None,
            "created": slug, "panel": panel, "run": RUN.info(), "log": tail}


@app.get("/api/start")
def api_start() -> Dict[str, Any]:
    return _start_state()


@app.post("/api/start/chat")
def api_start_chat(body: Dict[str, Any]) -> Dict[str, Any]:
    from errata import start as ST
    try:
        ST.turn(str(body.get("message") or ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"시작 조교 호출 실패: {e}")
    return _start_state()


@app.post("/api/start/create")
def api_start_create(body: Dict[str, Any]) -> Dict[str, Any]:
    from errata import start as ST
    try:
        slug = ST.create(bool(body.get("overwrite")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    RUN.start(["ingest", "--job", slug], chain=[["panel", "--job", slug] + (["--force"] if body.get("overwrite") else [])])
    return _start_state()


@app.post("/api/start/run")
def api_start_run(body: Dict[str, Any]) -> Dict[str, Any]:
    """mode: trial_all(1회차 시험 → 전체) · trial · all"""
    from errata import start as ST
    slug = ST.load().get("created") or body.get("job") or ""
    if not slug:
        raise HTTPException(400, "먼저 「만들고 검수단 뽑기」를 누르십시오")
    mode = body.get("mode") or "trial_all"
    if mode == "trial_all":
        RUN.start(["trial", "--job", slug], chain=[["all", "--job", slug]])
    else:
        RUN.start([mode if mode in ("trial", "all") else "all", "--job", slug])
    return _start_state()


@app.post("/api/start/reset")
def api_start_reset() -> Dict[str, Any]:
    from errata import start as ST
    ST.reset()
    return _start_state()


@app.get("/api/jobs")
def jobs() -> List[str]:
    return sorted(d.name for d in JOBS.iterdir() if (d / "job.json").exists())


def _count_source(key: str, p: Path, shorts_glob: str = "*") -> str:
    """미리 세기 — 지정한 폴더에 무엇이 들었는지 한 줄로."""
    if not p or not p.exists():
        return "폴더가 없습니다"
    try:
        if key == "book":
            return (f"회차 {len(list((p / '_rounds').glob('m*.json')))} · 요약 {len(list((p / '03').glob('summary_*.htm*')))} · "
                    f"영상 번들 {len([d for d in (p / '05').glob('m*-*') if d.is_dir()])} · 문제덱 {len(list((p / '04-2').glob('*.pptx')))}")
        if key == "longform":
            cur = read_json(p / "curriculum.json", {}) or {}
            return (f"강 {len(cur.get('lectures') or [])} · 원고 섹션 {len([f for f in (p / 'manuscripts').glob('L*/*.json') if not f.name.endswith('.llm.json')])} · "
                    f"화이트보드 mp4 {len(list((p / 'out_wb').glob('L*/L*.mp4')))} · 덱 {len(list((p / 'bp').glob('*/10_덱/deck.json')))}")
        if key == "textbook":
            return f"md {len(list(p.glob('*.md')))} · pdf {len(list(p.glob('*.pdf')))} · hwpx {len(list(p.glob('*.hwpx')))}"
        if key == "shorts":
            g = shorts_glob
            return f"과목 {len([d for d in p.glob(g) if d.is_dir()])} · 쇼츠 {len(list(p.glob(g + '/q*/00_대본/script.json')))}"
    except Exception as e:  # noqa: BLE001
        return f"세기 실패: {e}"
    return ""


@app.get("/api/sources")
def get_sources(job: str = "") -> Dict[str, Any]:
    j = _job(job)
    return {"items": [{"key": k, "label": SOURCE_LABELS[k], "path": j.sources.get(k, ""),
                       "count": _count_source(k, j.src(k), j.pack().get("shorts_glob") or "*") if j.sources.get(k) else "지정 안 됨"} for k in SOURCE_KEYS]}


@app.put("/api/sources")
async def put_sources(request: Request) -> Dict[str, Any]:
    b = await request.json()
    j = _job(b.get("job", ""))
    j.set_sources(b.get("sources") or {})
    return get_sources(j.name)


@app.post("/api/pick")
async def pick(request: Request) -> Dict[str, Any]:
    """tkinter 폴더 고르기 창(이 PC 에서만 열려 있으므로 창도 이 PC 에 뜬다)."""
    p = subprocess.run([sys.executable, "-m", "errata.pick"], cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode == 3:
        raise HTTPException(501, "고르기 창을 띄울 수 없습니다 — 경로를 붙여 넣어 주세요")
    import json
    try:
        return json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        raise HTTPException(500, f"고르기 실패: {(p.stderr or p.stdout or '')[-300:]}")


@app.get("/api/progress")
def progress(job: str = "") -> Dict[str, Any]:
    j = _job(job)
    prog = read_json(j.p("progress.json"), {}) or {}
    counts = read_json(j.p("00_대상", "counts.json"), {}) or {}
    mfile = j.p("04_수용", "merged.jsonl")
    nf = sum(1 for _ in mfile.open(encoding="utf-8")) if mfile.exists() else 0
    files = {k: j.p("05_정오표", n).exists() for k, n in (("xlsx", "정오표_내부.xlsx"), ("html", "정오표_게시.html"))}
    return {"progress": prog, "counts": counts, "findings": nf, "files": files, "run": RUN.info(),
            "trial_only": j.get("trial_only", "")}


@app.get("/api/findings")
def findings(job: str = "", product: str = "", severity: str = "", origin: str = "", limit: int = 300) -> Dict[str, Any]:
    j = _job(job)
    rows = read_jsonl(j.p("04_수용", "merged.jsonl"))
    dec = read_json(j.p("05_정오표", "decisions.json"), {}) or {}
    if product:
        rows = [r for r in rows if r["product"] in product.split(",")]
    if severity:
        rows = [r for r in rows if r["severity"] in severity.split(",")]
    if origin:
        rows = [r for r in rows if r["origin"] == origin]
    keep = ("fid", "product", "loc", "as_is", "to_be", "type", "severity", "confidence", "origin", "reason", "suggest",
            "needs_web", "reviewer", "rule", "verify")
    return {"total": len(rows), "rows": [{**{k: r.get(k) for k in keep}, "decision": (dec.get(r["fid"]) or {}).get("판정", "")}
                                          for r in rows[:limit]]}


@app.get("/out/{job}/{name}")
def out_file(job: str, name: str):
    j = _job(job)
    allow = {"xlsx": ("05_정오표", "정오표_내부.xlsx"), "html": ("05_정오표", "정오표_게시.html"),
             "history": ("07_이력.md",), "panel": ("01_선정", "패널명부.md")}
    if name not in allow:
        raise HTTPException(404)
    p = j.p(*allow[name])
    if not p.exists():
        raise HTTPException(404, "아직 만들어지지 않았습니다")
    return FileResponse(p, filename=p.name if name == "xlsx" else None)


@app.post("/api/open")
def open_folder(job: str, what: str = "job") -> Dict[str, str]:
    j = _job(job)
    target = {"job": j.dir, "errata": j.p("05_정오표"), "xlsx": j.p("05_정오표", "정오표_내부.xlsx")}.get(what, j.dir)
    if not target.exists():
        raise HTTPException(404, "아직 없습니다")
    if os.name == "nt":
        os.startfile(str(target))  # noqa: S606
    return {"opened": str(target)}


@app.post("/api/run")
async def run(request: Request) -> Dict[str, Any]:
    b = await request.json()
    stage = b.get("stage", "")
    if stage not in STAGES:
        raise HTTPException(400, f"모르는 단계: {stage}")
    args = [stage, "--job", (b.get("job") or "")]
    if b.get("only"):
        args += ["--only", str(b["only"])]
    if b.get("force"):
        args.append("--force")
    RUN.start(args)
    return RUN.info()


@app.post("/api/stop")
def stop() -> Dict[str, Any]:
    RUN.stop()
    return RUN.info()


@app.get("/api/log")
def log(offset: int = 0) -> JSONResponse:
    if not LOG.exists():
        return JSONResponse({"text": "", "offset": 0, "run": RUN.info()})
    data = LOG.read_bytes()
    if offset > len(data):
        offset = 0
    chunk = data[offset:offset + 200_000].decode("utf-8", errors="replace")
    return JSONResponse({"text": chunk, "offset": offset + len(data[offset:offset + 200_000]), "run": RUN.info()})


def main() -> None:
    import uvicorn
    import webbrowser
    port = int(os.environ.get("ERRATA_PORT", "5191"))
    url = f"http://127.0.0.1:{port}"
    print(f"산물 검수엔진 → {url}  (끄려면 이 창을 닫으십시오 — 무인 운전 중에는 닫지 마십시오)")
    if not os.environ.get("ERRATA_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
