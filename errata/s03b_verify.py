"""s03b 재검증 — 검토단의 무거운 지적(상 전부 + 중 중 정답오류·사실오류)을 **다른 호출**에서 독립으로 다시 판정한다.

사업계획의 「교차 검증」 자리다. 한 회의가 낸 지적을 같은 회의가 확인하면 같은 것을 같이 믿는다 —
그래서 지적 원문과 그 단위의 본문만 주고, 동의 · 수정동의 · 반대 · 불확실 로 다시 묻는다.
결과는 03b_재검증/<묶음>.json. 있으면 건너뛴다. 묶음 = 같은 검토 배치의 지적 최대 12건.
merge 가 이것을 읽어 제안을 고친다(반대 → 보류, 불확실 → 웹확인 필요, 수정동의 → 바름 교체).
"""
from __future__ import annotations

import collections
import time
from typing import Dict, List, Optional

from errata.config import Job
from errata.s03_review import Progress, _ask, _cards, _fill, _safe, extract_json
from errata.util import log, read_json, read_jsonl, warn, write_json

HEAVY_TYPES = {"정답오류", "사실오류"}
CHUNK = 12


def targets(job: Job) -> Dict[str, List[dict]]:
    """재검증할 지적을 검토 배치별로."""
    out: Dict[str, List[dict]] = collections.OrderedDict()
    for p in sorted(job.p("03_검토").glob("*.json")):
        d = read_json(p, {}) or {}
        for f in d.get("findings") or []:
            if f["severity"] == "상" or (f["severity"] == "중" and f["type"] in HEAVY_TYPES):
                out.setdefault(d["batch"], []).append(f)
    return out


def _unit_text(u: dict) -> str:
    x = u.get("x") or {}
    if u["product"] == "Q":
        from errata.util import CIRCLED
        ai = x.get("answer_index")
        mark = CIRCLED[ai] if isinstance(ai, int) and 0 <= ai < 10 else ai
        return (f"{u['text']}\n[표기된 정답] {mark}\n[해설]\n{x.get('explanation', '')}\n"
                f"[해설 낭독]\n{x.get('explanation_speech', '')}")
    parts = [u.get("text", "")]
    if u.get("say") and u.get("say") != u.get("text"):
        parts.append(f"[자막] {u['say']}")
    return "\n".join(p for p in parts if p)


def run(job: Job, only: Optional[List[str]] = None, force: bool = False) -> int:
    from errata import llm_wait
    from errata._engine import NotAuthenticated, ProviderError
    units = {u["uid"]: u for u in read_jsonl(job.p("00_대상", "units.jsonl"))}
    pack = job.pack()
    vdir = job.p("03b_재검증")
    (vdir / "_prompts").mkdir(parents=True, exist_ok=True)
    tasks = []
    for b, fs in targets(job).items():
        if only and not any(b.startswith(o) for o in only):
            continue
        for i in range(0, len(fs), CHUNK):
            tasks.append((f"{b}#{i // CHUNK + 1}", fs[i:i + CHUNK]))
    todo = [t for t in tasks if force or not (vdir / f"{_safe(t[0])}.json").exists()]
    log(f"  재검증 묶음 {len(tasks)}개 중 남은 것 {len(todo)}개 (지적 {sum(len(t[1]) for t in tasks)}건)")
    if not todo:
        return 0
    prog = Progress(job, len(tasks))
    prog.d["stage"] = "verify"
    prog.save(done=len(tasks) - len(todo))
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent / "prompts" / "03b_재검증.md").read_text(encoding="utf-8")
    llm_wait.keep_awake(True)
    try:
        for k, (name, fs) in enumerate(todo, 1):
            items = []
            for f in fs:
                u = units.get(f["uid"]) or {}
                items.append(f"### fid {f['fid']} — {f['loc']}\n[지적 유형·심각도] {f['type']} · {f['severity']}\n"
                             f"[잘못이라는 원문] {f['as_is']}\n[수정안] {f['to_be']}\n[지적 근거] {f['reason']}\n"
                             f"[그 단위의 본문]\n{_unit_text(u)[:4000]}")
            prompt = _fill(tpl, exam_label=pack.get("label", ""), scope=(pack.get("scope") or "").strip(),
                           authors=_cards(job, "author"), n=len(fs), items="\n\n".join(items))
            (vdir / "_prompts" / f"{_safe(name)}.프롬프트.txt").write_text(prompt, encoding="utf-8")
            prog.save(current=name)
            log(f"  [{k}/{len(todo)}] {name} — 지적 {len(fs)}건")
            t0 = time.time()
            try:
                text = _ask(job, prompt)
                (vdir / "_prompts" / f"{_safe(name)}.응답.txt").write_text(text, encoding="utf-8")
                res = extract_json(text)
            except NotAuthenticated as e:
                warn(f"Claude 로그인이 필요합니다 — 멈춥니다: {e}")
                return 3
            except (ProviderError, RuntimeError, ValueError) as e:
                if llm_wait.is_limit(str(e)):
                    return 2
                warn(f"    ✗ {name} 실패 — 건너뜁니다 ({str(e)[:120]})")
                continue
            got = {v.get("fid"): v for v in res.get("verdicts") or [] if v.get("fid")}
            write_json(vdir / f"{_safe(name)}.json", {"name": name, "at": time.time(), "sec": round(time.time() - t0),
                                                       "verdicts": got, "asked": [f["fid"] for f in fs]})
            c = collections.Counter(v.get("verdict") for v in got.values())
            prog.save(done=prog.d["done"] + 1, calls=prog.d["calls"] + 1, sec=prog.d["sec"] + time.time() - t0)
            log(f"    ✓ {time.time() - t0:.0f}초 · " + " · ".join(f"{k} {n}" for k, n in c.most_common()))
    finally:
        llm_wait.keep_awake(False)
        prog.save(current="", stage="verify-done")
    return 0


def load(job: Job) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for p in job.p("03b_재검증").glob("*.json") if job.p("03b_재검증").exists() else []:
        out.update((read_json(p, {}) or {}).get("verdicts") or {})
    return out
