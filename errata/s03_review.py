"""s03 검토 — 검수단이 배치 하나를 읽고 지적한다. 배치 하나 = 모델 호출 한 번(provider.ask).

★ 무인 4일 운전을 전제로 만든다.
  - 배치 결과는 03_검토/<배치>.json 하나. **있으면 건너뛴다**(덮지 않음) — 끊겨도 같은 명령이 남은 것만 이어 한다.
  - 사용 한도에 걸리면 llm_wait.wait_for_claude 로 풀릴 때까지 기다린다(절전 방지 포함).
  - 일시 오류는 재시도, 그래도 안 되면 그 배치만 _실패/ 에 적고 다음 배치로 넘어간다.
  - progress.json 에 현재 상태를 적는다(웹 화면이 읽는다). interim_every 배치마다 중간 정오표를 뽑는다.
★ 프롬프트와 응답 원문은 03_검토/_prompts/ 에 남는다 — "그 지적은 어떻게 나왔나" 에 답하기 위해서다.
"""
from __future__ import annotations

import collections
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from errata.config import Job
from errata.findings import make
from errata.ingest import PRODUCTS
from errata.util import CIRCLED, log, norm, read_json, read_jsonl, warn, write_json

FOCUS = {
    "S": "요약노트(과목별 핵심 정리 — 모든 강의·교재·쇼츠의 원천). 정의·분류·공식·예시의 사실 오류, 항목끼리 모순, "
         "같은 개념의 다른 표기, 시험 범위 밖 서술.",
    "L": "롱폼 화이트보드 강의(화면 = 제목·불릿, 자막 = say, 발음 = read 가 TTS 로 읽힌다). 사실 오류, 화면과 자막의 어긋남, "
         "발음 대본의 잘못 읽기(숫자·영문 약어·기호·수식을 소리로 옮길 때의 오류 — ◆ADsP 예: R 함수명, p-value, 10^3), 예제 풀이의 오류.",
    "B": "과목별 교재(종이책·PDF·HWPX 로 나간다). 사실 오류, 정의·공식 오류, 절 안의 모순, 핵심 정리·주의 상자가 본문과 다른 것, "
         "표기·띄어쓰기 오류.",
    "T": "용어 쇼츠(유튜브 40~60초). 장면마다 [칩]·[주장]·[자막], 끝에 유튜브 제목·설명·태그. 사실 오류, 과장·단정으로 틀린 말이 되는 것, "
         "자막과 칩의 모순, 제목·설명·고정댓글이 본문과 다르거나 틀린 것. "
         "★ 해시태그·태그의 시험 이름 오류와 {원본링크} 자리표는 기계검사가 이미 잡았다 — 다시 내지 않는다.",
}


def _cards(job: Job, role: str) -> str:
    from errata.s01_panel import cards
    return cards(job, role)


def _batches(units: List[dict], order: List[str], only: List[str]) -> List[tuple]:
    groups: Dict[str, List[dict]] = collections.OrderedDict()
    for u in units:
        if u.get("batch"):
            groups.setdefault(u["batch"], []).append(u)
    rank = {p: i for i, p in enumerate(order)}
    out = sorted(groups.items(), key=lambda kv: (rank.get(kv[0].split(":")[0], 99), kv[0]))
    if only:
        out = [kv for kv in out if any(kv[0].startswith(o) for o in only)]
    return out


def _safe(batch: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", batch)


def _fill(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def build_prompt(job: Job, batch: str, us: List[dict]) -> str:
    pack = job.pack()
    import yaml
    pcfg = yaml.safe_load((job.pack_dir / "persona.yaml").read_text(encoding="utf-8")) or {}
    from errata import subjects
    common = dict(exam_label=pack.get("label", ""), scope=(pack.get("scope") or "").strip(),
                  subject_notes=subjects.prompt_block(job, us),
                  authors=_cards(job, "author"), students=_cards(job, "reviewer"),
                  require="\n".join("- " + r for r in pcfg.get("require") or []), n=len(us), batch=batch)
    here = Path(__file__).resolve().parent / "prompts"
    if us[0]["product"] == "Q":
        probs, ans = [], []
        for u in us:
            x = u["x"]
            probs.append(f"### {u['uid']} ({u['loc']} · {x.get('difficulty', '')})\n{u['text']}")
            ai = x["answer_index"]
            mark = CIRCLED[ai] if isinstance(ai, int) and 0 <= ai < 10 else str(ai)
            ans.append(f"### {u['uid']} — 표기된 정답 {mark}\n[해설]\n{x.get('explanation', '')}\n"
                       f"[해설 낭독 — 문제풀이 영상 자막]\n{x.get('explanation_speech', '')}")
        return _fill((here / "03_문항.md").read_text(encoding="utf-8"), problems="\n\n".join(probs),
                     answers="\n\n".join(ans), **common)
    blocks = []
    for u in us:
        b = [f"### {u['uid']} — {u['loc']}"]
        if u["product"] == "L":
            if u.get("text"):
                b.append(f"[화면]\n{u['text']}")
            b.append(f"[자막]\n{u.get('say', '')}")
            if u.get("read") and u.get("read") != u.get("say"):
                b.append(f"[발음]\n{u['read']}")
        elif u["product"] == "T":
            b.append(u["text"])
            if u.get("read") and norm(u.get("read")) != norm(u.get("say")):
                b.append(f"[발음] {u['read']}")
        else:
            b.append(u["text"])
        blocks.append("\n".join(b))
    p = us[0]["product"]
    return _fill((here / "03_산물.md").read_text(encoding="utf-8"), product_label=PRODUCTS.get(p, p),
                 focus=FOCUS.get(p, ""), units="\n\n".join(blocks), **common)


def extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    s = t.find("{")
    e = t.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("응답에 JSON 이 없습니다")
    return json.loads(t[s:e + 1])


def _haystack(u: dict) -> str:
    x = u.get("x") or {}
    parts = [u.get("title", ""), u.get("text", ""), u.get("say", ""), u.get("read", ""),
             x.get("explanation", ""), x.get("explanation_speech", ""), x.get("question", ""), str(x.get("passage", ""))]
    parts += [str(c) for c in x.get("choices") or []]
    return norm(" ".join(parts))


def to_findings(result: dict, us: List[dict], batch: str) -> List[dict]:
    by = {u["uid"]: u for u in us}
    out = []
    for f in result.get("findings") or []:
        uid = (f.get("uid") or "").strip()
        u = by.get(uid)
        if not u:
            # 모델이 uid 를 조금 틀리게 적은 경우 — 같은 배치에서 as_is 로 찾는다
            hit = [v for v in us if f.get("as_is") and norm(f["as_is"]) in _haystack(v)]
            u = hit[0] if hit else None
        if not u:
            continue
        as_is = (f.get("as_is") or "").strip()
        quote_ok = bool(as_is) and norm(as_is) in _haystack(u)
        reason = (f.get("reason") or "").strip()
        conf = f.get("confidence") or "의심"
        if not quote_ok:
            reason = "[인용 불일치 — 본문에서 그대로 찾지 못함] " + reason
            conf = "의심"
        field = (f.get("field") or "").strip()
        fd = make(u, as_is=as_is, to_be=f.get("to_be") or "", type=f.get("type") or "기타",
                  severity=f.get("severity") or "중", reason=(f"[{field}] " if field else "") + reason,
                  origin="검토단", rule="review", confidence=conf, reviewer=(f.get("reviewer") or "").strip(),
                  needs_web=bool(f.get("needs_web")), web_query=f.get("web_query") or "", batch=batch)
        fd["quote_ok"] = quote_ok
        out.append(fd)
    # 정답 대조 — 검수단 풀이가 표기 정답과 다르면, 지적이 없어도 상으로 올린다
    for s in result.get("solves") or []:
        u = by.get((s.get("uid") or "").strip())
        if not u or s.get("agree", True):
            continue
        ai = u["x"]["answer_index"]
        solved = s.get("solved")
        if any(f["uid"] == u["uid"] and f["type"] == "정답오류" for f in out):
            continue
        fd = make(u, as_is=f"정답 {CIRCLED[ai] if isinstance(ai, int) and 0 <= ai < 10 else ai}",
                  to_be=f"정답 재확인 (검수단 풀이: {solved})", type="정답오류", severity="상",
                  reason=f"[눈가림 풀이 불일치] {s.get('note', '')}", origin="검토단", rule="review-solve",
                  confidence="의심", reviewer="검수진", batch=batch)
        fd["quote_ok"] = True
        out.append(fd)
    return out


class Progress:
    def __init__(self, job: Job, total: int) -> None:
        self.job, self.path = job, job.p("progress.json")
        self.d = {"stage": "review", "total": total, "done": 0, "skipped": 0, "failed": [], "current": "",
                  "started": time.time(), "updated": time.time(), "waiting": "", "calls": 0, "sec": 0.0}

    def save(self, **kw) -> None:
        self.d.update(kw)
        self.d["updated"] = time.time()
        write_json(self.path, self.d)


def _ask(job: Job, prompt: str) -> str:
    """한 번 묻는다. 한도면 기다렸다 다시, 일시 오류는 재시도. 끝내 안 되면 예외."""
    from errata import llm_wait
    from errata._engine import NotAuthenticated, ProviderError, QuotaExceeded, ask
    retries = int(job.get("llm.retries", 3))
    timeout = int(job.get("llm.timeout_sec", 1800))
    cwd = str(job.p("03_검토"))
    last = ""
    tries = 0
    while tries < retries:
        try:
            return ask(prompt, cwd=cwd, timeout=timeout)
        except NotAuthenticated:
            raise
        except QuotaExceeded as e:
            if not llm_wait.wait_for_claude(job, str(e) or "limit"):
                raise
            continue                                  # 한도 대기는 재시도 횟수에 넣지 않는다
        except ProviderError as e:
            last = str(e)
            if llm_wait.is_limit(last):
                if not llm_wait.wait_for_claude(job, last):
                    raise
                continue
            tries += 1
            warn(f"    호출 실패({tries}/{retries}): {last[:160]}")
            time.sleep(min(300, 30 * tries))
    raise RuntimeError(last or "호출 실패")


def run(job: Job, only: Optional[List[str]] = None, force: bool = False) -> int:
    from errata import llm_wait
    from errata._engine import NotAuthenticated, ProviderError
    units = read_jsonl(job.p("00_대상", "units.jsonl"))
    if not units:
        raise SystemExit("00_대상/units.jsonl 이 없습니다 — ingest 를 먼저 돌리십시오")
    if not job.p("01_선정", "패널.json").exists():
        raise SystemExit("검수단 명부가 없습니다 — panel 을 먼저 돌리십시오")
    order = job.get("review.order") or ["Q", "L", "B", "S", "T"]
    batches = _batches(units, order, only or [])
    rdir, pdir, fdir = job.p("03_검토"), job.p("03_검토", "_prompts"), job.p("03_검토", "_실패")
    for d in (rdir, pdir, fdir):
        d.mkdir(parents=True, exist_ok=True)
    todo = [(b, us) for b, us in batches if force or not (rdir / f"{_safe(b)}.json").exists()]
    prog = Progress(job, len(batches))
    prog.save(done=len(batches) - len(todo), skipped=len(batches) - len(todo))
    log(f"  배치 {len(batches)}개 중 남은 것 {len(todo)}개 " + (f"(범위 {', '.join(only)})" if only else ""))
    every = int(job.get("review.interim_every", 25))
    llm_wait.keep_awake(True)                     # ★ 밤새 돌 때 PC 가 잠들지 않게
    n_new = 0
    try:
        for i, (b, us) in enumerate(todo, 1):
            out = rdir / f"{_safe(b)}.json"
            prompt = build_prompt(job, b, us)
            (pdir / f"{_safe(b)}.프롬프트.txt").write_text(prompt, encoding="utf-8")
            prog.save(current=b)
            log(f"  [{i}/{len(todo)}] {b} — 단위 {len(us)} · 프롬프트 {len(prompt):,}자")
            t0 = time.time()
            text, result, err = "", None, ""
            for attempt in range(2):                  # JSON 이 깨지면 한 번 더 묻는다
                try:
                    text = _ask(job, prompt)
                except NotAuthenticated as e:
                    warn(f"Claude 로그인이 필요합니다 — 멈춥니다: {e}")
                    prog.save(current="", waiting="로그인 필요")
                    return 3
                except (ProviderError, RuntimeError) as e:
                    err = str(e)
                    if llm_wait.is_limit(err):
                        warn("한도가 정해 둔 시간 안에 풀리지 않아 멈춥니다 — 같은 명령을 다시 돌리면 이어 합니다")
                        prog.save(current="", waiting="한도")
                        return 2
                    break
                (pdir / f"{_safe(b)}.응답.txt").write_text(text, encoding="utf-8")
                try:
                    result = extract_json(text)
                    break
                except Exception as e:  # noqa: BLE001
                    err = f"JSON 해석 실패: {e}"
                    warn(f"    {err} — 다시 묻습니다" if attempt == 0 else f"    {err}")
            sec = time.time() - t0
            if result is None:
                write_json(fdir / f"{_safe(b)}.json", {"batch": b, "error": err, "at": time.time()})
                prog.d["failed"] = sorted(set(prog.d["failed"]) | {b})
                prog.save(sec=prog.d["sec"] + sec)
                warn(f"    ✗ {b} 실패 — 건너뜁니다 ({err[:120]})")
                continue
            fs = to_findings(result, us, b)
            covered = {f["uid"] for f in fs} | set(result.get("clean") or [])
            missing = [u["uid"] for u in us if u["uid"] not in covered]
            write_json(out, {"batch": b, "product": us[0]["product"], "units": [u["uid"] for u in us],
                             "sec": round(sec), "prompt_chars": len(prompt), "at": time.time(),
                             "solves": result.get("solves") or [], "clean": result.get("clean") or [],
                             "unmentioned": missing, "findings": fs})
            (fdir / f"{_safe(b)}.json").unlink(missing_ok=True)
            prog.d["failed"] = [x for x in prog.d["failed"] if x != b]
            n_new += 1
            prog.save(done=prog.d["done"] + 1, calls=prog.d["calls"] + 1, sec=prog.d["sec"] + sec)
            sev = collections.Counter(f["severity"] for f in fs)
            log(f"    ✓ {sec:.0f}초 · 지적 {len(fs)} (상 {sev['상']} · 중 {sev['중']} · 하 {sev['하']})"
                + (f" · 언급 없음 {len(missing)}" if missing else ""))
            if every and n_new % every == 0:
                try:
                    from errata import s04_merge, s05_errata
                    s04_merge.run(job, quiet=True)
                    s05_errata.run(job, quiet=True)
                    log("    · 중간 정오표를 뽑았습니다")
                except Exception as e:  # noqa: BLE001
                    warn(f"    중간 정오표 실패(계속 진행): {e}")
    finally:
        llm_wait.keep_awake(False)
        prog.save(current="", stage="review-done")
    if prog.d["failed"]:
        warn(f"  실패 배치 {len(prog.d['failed'])}개: {', '.join(prog.d['failed'][:10])} — 다시 돌리면 그것만 다시 합니다")
    return 0
