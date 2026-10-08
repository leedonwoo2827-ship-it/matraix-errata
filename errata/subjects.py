"""과목 세팅 — 과목마다 검수 기준을 대화로 정하고 사람이 「확정」한다. 웹앱 /setup 이 부른다.

    data/pack/<팩>/subjects.yaml           확정한 세팅(과목 번호 → 세팅). 확정 버튼만 쓴다.
    data/pack/<팩>/_세팅대화/<n>.json       대화 기록 + 지금 세팅안(draft). 확정 전에는 여기만 바뀐다.

★ 모델은 묻고 정리해 세팅안을 낼 뿐, 확정하지 않는다. 확정은 사람이 버튼으로 한다.
★ 확정한 세팅은 그 과목 단위가 든 검토 배치의 프롬프트에 「과목 세팅」으로 들어간다(s03_review.build_prompt).
"""
from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from errata.config import Job
from errata.util import read_json, read_jsonl, write_json

FIELDS = {          # 세팅 칸 — 화면·프롬프트가 같은 이름을 쓴다
    "scope": "검수 범위",
    "focus": "검수 중점",
    "out_of_scope": "범위 밖(지적 대상)",
    "terms": "표기 통일",
    "banned_words": "금지어",
    "seat_focus": "검수진 자리별 중점",
    "notes": "메모",
}


def _yaml():
    import yaml
    return yaml


def path(job: Job) -> Path:
    return job.pack_dir / "subjects.yaml"


def chat_path(job: Job, n: str) -> Path:
    return job.pack_dir / "_세팅대화" / f"{n}.json"


def load(job: Job) -> Dict[str, dict]:
    p = path(job)
    d = (_yaml().safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    return {str(k): v or {} for k, v in d.items()}


def save(job: Job, data: Dict[str, dict]) -> None:
    p = path(job)
    if p.exists():                                    # 고치기 전 판은 남긴다
        bak = p.with_name("_세팅대화") / f"subjects_{dt.datetime.now():%m%d-%H%M%S}.yaml"
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    head = "# 과목 세팅 — 웹앱 「과목 세팅」에서 대화로 정하고 「확정」한 것. 손으로 고쳐도 된다.\n"
    p.write_text(head + _yaml().safe_dump(data, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")


def subject_of(u: dict) -> Optional[int]:
    """단위 → 과목 번호. 산물마다 과목이 들어 있는 자리가 다르다."""
    p, b, x = u.get("product"), u.get("batch") or "", u.get("x") or {}
    try:
        if p == "Q":
            return int(x.get("subject_no"))
        if p == "S":
            return int(x.get("subject"))
        if p == "L":
            m = re.match(r"s(\d+)-", x.get("section") or "")
            return int(m.group(1)) if m else None
        if p == "B":
            return int(b.split(":")[1])
        if p == "T":
            m = re.match(r"T:q(\d+)-", b)
            return int(m.group(1)) if m else None
    except (TypeError, ValueError, IndexError):
        return None
    return None


def subjects(job: Job) -> List[dict]:
    """과목 목록 + 상태(미시작·대화 중·세팅안 있음·확정)."""
    names = job.pack().get("subjects") or {}
    conf = load(job)
    out = []
    for k, name in names.items():
        n = str(k)
        c = read_json(chat_path(job, n), {}) or {}
        st = "확정" if conf.get(n, {}).get("confirmed_at") else ("세팅안 있음" if c.get("draft") else
                                                              ("대화 중" if c.get("messages") else "미시작"))
        if st == "확정" and c.get("draft") and c.get("draft_at", 0) > conf[n].get("_draft_at", 0):
            st = "확정 · 새 세팅안"
        out.append({"n": n, "name": name, "status": st, "confirmed_at": conf.get(n, {}).get("confirmed_at", "")})
    return out


def outline(job: Job, n: str, limit: int = 60) -> str:
    """그 과목 산물의 목차 — 요약 h2·롱폼 섹션·교재 절 제목. 모델이 과목을 알고 묻게 한다."""
    units = read_jsonl(job.p("00_대상", "units.jsonl"))
    seen, lines = set(), []
    counts: Dict[str, int] = {}
    for u in units:
        if str(subject_of(u)) != n:
            continue
        counts[u["product"]] = counts.get(u["product"], 0) + 1
        x = u.get("x") or {}
        t = x.get("h2") if u["product"] == "S" else (f"{x.get('section')} {x.get('group') or ''}".strip() if u["product"] == "L" else None)
        if t and t not in seen and len(lines) < limit:
            seen.add(t)
            lines.append(f"- {t}")
    from errata.ingest import PRODUCTS
    head = "산물 단위 수: " + " · ".join(f"{PRODUCTS.get(k, k)} {v}" for k, v in counts.items())
    return head + "\n" + ("\n".join(lines) or "(목차 없음 — ingest 를 먼저 돌리면 목차가 보인다)")


def render(s: dict) -> str:
    """세팅 → 사람이 읽는 글(프롬프트·미리보기 공용)."""
    out = []
    for k, lab in FIELDS.items():
        v = s.get(k)
        if not v:
            continue
        if isinstance(v, dict):
            if k == "terms":
                items = [f"{a} ({'·'.join(b) if isinstance(b, list) else b} 아님)" for a, b in v.items()]
            elif k == "banned_words":
                items = [f"「{a}」→「{b}」" if b else f"「{a}」 지움" for a, b in v.items()]
            else:
                items = [f"{a}: {b}" for a, b in v.items()]
            out.append(f"- {lab}: " + " / ".join(items))
        elif isinstance(v, list):
            out.append(f"- {lab}: " + " / ".join(map(str, v)))
        else:
            out.append(f"- {lab}: {v}")
    return "\n".join(out)


def prompt_block(job: Job, units: List[dict]) -> str:
    """검토 프롬프트용 — 이 배치 단위들의 과목 중 확정된 세팅만."""
    conf = load(job)
    ns = sorted({str(subject_of(u)) for u in units if subject_of(u) is not None})
    names = job.pack().get("subjects") or {}
    parts = [f"### {n}과목 {names.get(int(n), names.get(n, ''))}\n{render(conf[n])}" for n in ns
             if conf.get(n, {}).get("confirmed_at")]
    if not parts:
        return ""
    return "## 과목 세팅 (편집자가 확정한 검수 기준 — 이 기준을 따른다)\n" + "\n".join(parts) + "\n"


def turn(job: Job, n: str, message: str) -> dict:
    """대화 한 차례 — 사용자 말 → 모델(구독 `claude -p`) → 답 + 세팅안. 기록은 _세팅대화/<n>.json."""
    from errata._engine import ask
    from errata.s03_review import _fill, extract_json
    names = job.pack().get("subjects") or {}
    name = names.get(int(n), names.get(n, ""))
    cp = chat_path(job, n)
    c = read_json(cp, {}) or {"messages": [], "draft": None}
    start = message.strip() == "__start__"
    if not start:
        c["messages"].append({"role": "user", "text": message.strip(), "at": time.time()})
    hist = "\n\n".join(f"[{'편집자' if m['role'] == 'user' else '세팅 조교'}] {m['text']}" for m in c["messages"][-24:])
    import yaml
    tpl = (Path(__file__).resolve().parent / "prompts" / "09_과목세팅.md").read_text(encoding="utf-8")
    conf = load(job).get(n) or {}
    prompt = _fill(tpl, exam_label=job.pack().get("label", ""), scope=(job.pack().get("scope") or "").strip(),
                   n=n, name=name, outline=outline(job, n),
                   seats="\n".join(f"- {s.get('id')} {s.get('view')}: {s.get('asks')}" for s in
                                   ((yaml.safe_load((job.pack_dir / 'persona.yaml').read_text(encoding='utf-8')) or {})
                                    .get("author") or {}).get("seats") or []),
                   confirmed=render(conf) or "(없음)",
                   draft=yaml.safe_dump(c.get("draft"), allow_unicode=True, sort_keys=False) if c.get("draft") else "(없음)",
                   history=hist or "(아직 없음 — 첫 차례다)")
    t0 = time.time()
    raw = ask(prompt, cwd=str(cp.parent.parent), timeout=900)
    res = extract_json(raw)
    reply = (res.get("reply") or "").strip()
    c["messages"].append({"role": "assistant", "text": reply, "at": time.time(), "sec": round(time.time() - t0)})
    if isinstance(res.get("draft"), dict) and res["draft"]:
        c["draft"] = {k: v for k, v in res["draft"].items() if k in FIELDS}
        c["draft_at"] = time.time()
    write_json(cp, c)
    return {"reply": reply, "draft": c.get("draft"), "has_new_draft": bool(res.get("draft")), "sec": round(time.time() - t0)}


def confirm(job: Job, n: str) -> dict:
    """지금 세팅안을 subjects.yaml 에 확정한다(사람이 버튼으로)."""
    c = read_json(chat_path(job, n), {}) or {}
    if not c.get("draft"):
        raise ValueError("확정할 세팅안이 없습니다 — 대화로 세팅안을 먼저 만드십시오")
    data = load(job)
    data[n] = {**c["draft"], "confirmed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "_draft_at": c.get("draft_at", 0)}
    save(job, data)
    return data[n]
