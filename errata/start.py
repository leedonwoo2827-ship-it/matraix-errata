"""처음 시작 — 웹앱 「＋ 새 시험 시작」 대화. 시작 프롬프트를 붙여 넣으면 조교가 읽고 빠진 것만 묻고 「시작안」을 낸다.

    data/_시작대화.json      대화 기록 + 지금 시작안(spec)
    [만들고 검수단 뽑기] →   data/pack/<팩>/pack.yaml·persona.yaml·roles.yaml + data/jobs/<잡>/job.json·job.local.json
                             → ingest → panel (웹앱 Runner 가 차례로 돌린다)

★ 조교 모델(`claude -p`)에게는 파일·셸 권한을 주지 않는다. 시작안을 JSON 으로 낼 뿐이고, 파일은 이 모듈이 쓴다.
★ 만들기·실행은 사람이 버튼으로 한다. 이미 있는 팩·잡은 덮지 않는다(덮으려면 버튼에서 「덮어쓰기」).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from errata.config import DATA, JOBS, PACKS, SOURCE_KEYS, TEMPLATE
from errata.util import read_json, write_json

CHAT = DATA / "_시작대화.json"
SPEC_KEYS = ["slug", "label", "exam", "subjects", "choices", "scope", "summary_files", "shorts_glob",
             "right_labels", "wrong_labels", "banned_words", "sources", "review_order", "trial_only", "persona_yaml"]


def _dims() -> Dict[str, List[str]]:
    p = DATA / "engine" / "MatrAIx-Persona-8B" / "persona" / "schema" / "dimensions.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {x["id"]: [str(v) for v in x.get("values") or []] for x in d.get("dimensions") or []}


def ensure_persona_code() -> None:
    """검수단 차원 목록(dimensions.json)이 있어야 조건을 검사할 수 있다 — 없으면 페르소나 코드를 받는다(MIT)."""
    from errata._engine import PERSONA_REPO
    import subprocess
    w = DATA / "engine"
    if (w / "MatrAIx-Persona-8B" / "persona").is_dir():
        return
    w.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", PERSONA_REPO, "MatrAIx-Persona-8B"], cwd=str(w))


def _conditions(persona: dict) -> List[dict]:
    """persona.yaml → [{자리, 관점, 조건 문자열}] (카드 표용)."""
    def cond(d: dict) -> str:
        parts = []
        for kind in ("must", "must_any", "prefer"):
            v = d.get(kind)
            if not v:
                continue
            items = v if isinstance(v, list) else [v]
            for it in items:
                for k, vals in (it or {}).items():
                    parts.append(f"{'필수' if kind == 'must' else ('하나 이상' if kind == 'must_any' else '우대')} {k}={'/'.join(map(str, vals))}")
        return " · ".join(parts)
    rows = [{"seat": "공통", "view": "", "n": "", "cond": cond(persona.get("common") or {})}]
    a = persona.get("author") or {}
    rows.append({"seat": "검수진 공통", "view": "", "n": a.get("n", ""), "cond": cond(a.get("common") or {})})
    for s in a.get("seats") or []:
        rows.append({"seat": s.get("id"), "view": s.get("view", ""), "n": 1, "cond": cond(s)})
    for s in (persona.get("reviewer") or {}).get("strata") or []:
        rows.append({"seat": f"수험생 {s.get('id')}", "view": s.get("desc", ""), "n": s.get("n", ""), "cond": cond(s)})
    return rows


def check(spec: dict) -> Dict[str, Any]:
    """시작안 점검 — 산물 폴더(있나·무엇이 들었나) · persona.yaml(읽히나·차원 이름과 값이 있나) · 이미 있는 팩·잡."""
    import yaml
    from errata.web.server import _count_source
    out: Dict[str, Any] = {"sources": [], "warnings": [], "conditions": []}
    for k in SOURCE_KEYS:
        v = ((spec.get("sources") or {}).get(k) or "").strip().strip('"')
        p = Path(v) if v else None
        ok = bool(p and p.exists())
        out["sources"].append({"key": k, "path": v, "ok": ok,
                               "count": (_count_source(k, p, spec.get("shorts_glob") or "*") if ok else ("지정 안 됨" if not v else "폴더가 없습니다"))})
        if v and not ok:
            out["warnings"].append(f"{k} 폴더가 없습니다: {v}")
    try:
        persona = yaml.safe_load(spec.get("persona_yaml") or "") or {}
        out["conditions"] = _conditions(persona)
        dims = _dims()
        if dims:
            def walk(d):
                for kind in ("must", "must_any", "prefer"):
                    for it in (d.get(kind) if isinstance(d.get(kind), list) else [d.get(kind) or {}]):
                        for k, vals in (it or {}).items():
                            if k not in dims:
                                out["warnings"].append(f"검수단 조건의 차원 「{k}」이 페르소나에 없습니다")
                            else:
                                bad = [v for v in vals if str(v) not in dims[k]]
                                if bad:
                                    out["warnings"].append(f"「{k}」에 없는 값: {', '.join(map(str, bad))}")
            for blk in [persona.get("common") or {}, (persona.get("author") or {}).get("common") or {},
                        *((persona.get("author") or {}).get("seats") or []), *((persona.get("reviewer") or {}).get("strata") or [])]:
                walk(blk)
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"검수단 조건(persona.yaml)을 읽지 못했습니다: {e}")
    slug = spec.get("slug") or ""
    out["exists"] = bool(slug and ((PACKS / slug).exists() or (JOBS / slug).exists()))
    return out


def load() -> dict:
    return read_json(CHAT, {}) or {"messages": [], "spec": None}


def turn(message: str) -> dict:
    """대화 한 차례 — 붙여 넣은 시작 프롬프트·답 → 조교 → 답 + 시작안(spec)."""
    import yaml
    from errata._engine import ask
    from errata.s03_review import _fill, extract_json
    ensure_persona_code()
    c = load()
    c["messages"].append({"role": "user", "text": message.strip(), "at": time.time()})
    hist = "\n\n".join(f"[{'사용자' if m['role'] == 'user' else '시작 조교'}] {m['text']}" for m in c["messages"][-20:])
    dims = _dims()
    tpl = (Path(__file__).resolve().parent / "prompts" / "10_시작.md").read_text(encoding="utf-8")
    prompt = _fill(tpl, pack_template=(TEMPLATE / "pack.yaml").read_text(encoding="utf-8"),
                   persona_template=(TEMPLATE / "persona.yaml").read_text(encoding="utf-8"),
                   dims="\n".join(f"- {k}: {', '.join(v[:14])}" for k, v in dims.items()) or "(차원 목록 없음)",
                   spec=json.dumps(c.get("spec"), ensure_ascii=False, indent=1) if c.get("spec") else "(없음)",
                   history=hist)
    t0 = time.time()
    DATA.mkdir(parents=True, exist_ok=True)
    raw = ask(prompt, cwd=str(DATA), timeout=900)
    res = extract_json(raw)
    c["messages"].append({"role": "assistant", "text": (res.get("reply") or "").strip(), "at": time.time(),
                          "sec": round(time.time() - t0)})
    if isinstance(res.get("spec"), dict) and res["spec"]:
        c["spec"] = {k: v for k, v in res["spec"].items() if k in SPEC_KEYS}
        if isinstance(c["spec"].get("persona_yaml"), (dict, list)):
            c["spec"]["persona_yaml"] = yaml.safe_dump(c["spec"]["persona_yaml"], allow_unicode=True, sort_keys=False)
    write_json(CHAT, c)
    return c


def create(overwrite: bool = False, slug: str = "") -> str:
    """시작안 → 팩·잡 파일. 반환: 잡 이름. slug 를 주면 시작안의 이름 대신 쓴다(카드에서 고친 이름)."""
    import yaml
    c = load()
    s = c.get("spec") or {}
    if slug.strip():
        s["slug"] = slug.strip().lower()
        c["spec"] = s
    slug = (s.get("slug") or "").strip()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        raise ValueError("팩·잡 이름(slug)이 없거나 영문·숫자가 아닙니다")
    pdir, jdir = PACKS / slug, JOBS / slug
    if (pdir.exists() or jdir.exists()) and not overwrite:
        raise ValueError(f"이미 있습니다: {slug} — 덮어쓰려면 「덮어쓰기」를 켜십시오")
    yaml.safe_load(s.get("persona_yaml") or "")             # 읽히는지 먼저
    pdir.mkdir(parents=True, exist_ok=True)
    pack = {"slug": slug, "label": s.get("label", ""), "exam": s.get("exam", ""),
            "subjects": {int(k): v for k, v in (s.get("subjects") or {}).items()},
            "choices": int(s.get("choices") or 4), "shorts_glob": s.get("shorts_glob") or "*",
            "summary_files": {k: int(v) for k, v in (s.get("summary_files") or {}).items()},
            "right_labels": s.get("right_labels") or [], "wrong_labels": s.get("wrong_labels") or [],
            "banned_words": s.get("banned_words") or {}, "scope": s.get("scope", ""), "batch": {"Q": 10}}
    (pdir / "pack.yaml").write_text("# 「＋ 새 시험 시작」 대화로 만든 팩. 손으로 고쳐도 된다.\n"
                                    + yaml.safe_dump(pack, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    (pdir / "persona.yaml").write_text(s["persona_yaml"], encoding="utf-8")
    if not (pdir / "roles.yaml").exists():
        shutil.copy(TEMPLATE / "roles.yaml", pdir / "roles.yaml")
    jdir.mkdir(parents=True, exist_ok=True)
    write_json(jdir / "job.json", {"name": slug, "label": f"{s.get('label', slug)} — 산물 정오표", "pack": slug,
                                   "llm": {"limit_wait_hours": 30, "limit_poll_min": 30, "retries": 3, "timeout_sec": 1800},
                                   "review": {"order": s.get("review_order") or ["Q", "L", "B", "S", "T"], "interim_every": 25},
                                   "trial_only": s.get("trial_only") or ""})
    write_json(jdir / "job.local.json", {"sources": {k: ((s.get("sources") or {}).get(k) or "").strip().strip('"') for k in SOURCE_KEYS}})
    c["created"] = slug
    c["messages"].append({"role": "assistant", "text": f"만들었습니다 — data/pack/{slug}/ · data/jobs/{slug}/ . 이어서 ingest → panel 을 돌립니다.",
                          "at": time.time()})
    write_json(CHAT, c)
    return slug


def reset() -> None:
    """새 대화로 — 지난 대화는 날짜를 붙여 남긴다."""
    if CHAT.exists():
        CHAT.replace(CHAT.with_name(f"_시작대화_{time.strftime('%m%d-%H%M%S')}.json"))
