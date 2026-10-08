"""s01 선정 — MatrAIx Persona-1M 에서 검수단 10명(검수진 4 + 수험생 6)을 뽑는다. 모델을 부르지 않는다.

엔진 도구 engine/tools/sample_panel.py 를 이 venv 파이썬으로 부른다(작업 폴더 data/engine/).
data/pack/<팩>/persona.yaml 을 data/engine/packs/<팩>-qa/ 로 복사해서 돌린다. 처음이면 페르소나 코드·데이터 조각을 받는다.
같은 씨앗·같은 조각이면 같은 10명이 다시 나온다 — 그것이 이 명부의 근거다.

가명은 씨앗과 페르소나 키로 정한다(모델 호출 없음). 약력은 페르소나 차원에서 그대로 옮긴다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from errata.config import Job
from errata.util import log, read_json, warn, write_json

SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경"
GIVEN = ["서연", "민준", "지우", "하준", "서윤", "도윤", "지민", "예준", "수아", "시우", "하은", "주원", "지아", "은우",
         "윤서", "건우", "채원", "현우", "다은", "유준", "소윤", "지호", "예린", "선우", "가윤", "연우", "수빈", "정우"]

LABEL = {
    "age_bracket": "나이", "region": "지역", "lang_korean": "한국어", "domain": "분야",
    "domain_characteristics": "성격", "role_function": "직무", "highest_education": "학력",
    "years_experience": "경력", "fam_data_science": "데이터과학", "fam_statistics": "통계",
    "skill_data_analysis": "데이터분석", "skill_fact_checking": "사실확인", "skill_editing": "교정",
    "skill_mathematics": "수학", "skill_coding": "코딩",
}


def _alias(seed: int, key: str, used: set) -> str:
    h = int(hashlib.sha1(f"{seed}:{key}".encode()).hexdigest(), 16)
    for k in range(50):
        n = SURNAMES[(h >> (k * 3)) % len(SURNAMES)] + GIVEN[(h // 7 + k) % len(GIVEN)]
        if n not in used:
            used.add(n)
            return n
    return f"검수자{len(used) + 1}"


def _bio(persona: dict) -> str:
    attrs = persona.get("dimensions") or persona.get("attributes") or persona
    bits = [f"출처 {persona.get('source', '')}"] if persona.get("source") else []
    for k, lab in LABEL.items():
        v = attrs.get(k)
        if v not in (None, "", []):
            bits.append(f"{lab} {v if not isinstance(v, list) else '·'.join(map(str, v))}")
    return " · ".join(bits)


def yaml_load(p) -> dict:
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def run(job: Job, force: bool = False) -> dict:
    import sys
    from errata._engine import PERSONA_REPO, TOOLS, WORK as ENGINE
    out = job.p("01_선정", "패널.json")
    if out.exists() and not force:
        doc = read_json(out, {})
        log(f"  이미 있습니다: {out.name} — 검수진 {doc['counts']['author']} · 수험생 {doc['counts']['reviewer']}"
            " (다시 뽑으려면 --force)")
        return doc
    pack = f"{job.cfg.get('pack') or job.name}-qa"
    unit = "PANEL"
    epack = ENGINE / "packs" / pack
    epack.mkdir(parents=True, exist_ok=True)
    shutil.copy(job.pack_dir / "persona.yaml", epack / "persona.yaml")
    if not (epack / "pack.yaml").exists():
        (epack / "pack.yaml").write_text(
            f"# 패널 추출 전용 팩 (산물 검수엔진 panel 단계가 만든다).\n"
            f"slug: {pack}\nlabel: {job.pack().get('label', pack)} 검수단\npersona: persona.yaml\n", encoding="utf-8")
    (ENGINE / "data" / pack / unit).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # 처음이면 페르소나 코드(MIT)를 받고, persona.yaml 이 가리키는 데이터 조각을 내려받는다
    if not (ENGINE / "MatrAIx-Persona-8B" / "persona").is_dir():
        log(f"  페르소나 코드 받기 — {PERSONA_REPO}")
        if subprocess.run(["git", "clone", "--depth", "1", PERSONA_REPO, "MatrAIx-Persona-8B"], cwd=str(ENGINE)).returncode:
            raise SystemExit("페르소나 코드를 받지 못했습니다 (git 이 있어야 합니다)")
    shards = (yaml_load(job.pack_dir / "persona.yaml").get("source") or {})
    rel = Path(shards.get("local") or "")
    if not all((ENGINE / rel / s).exists() for s in shards.get("shards") or []):
        log("  페르소나 데이터 조각 내려받기 (Hugging Face · 처음 한 번)")
        if subprocess.run([sys.executable, str(TOOLS / "fetch_persona.py"), pack], cwd=str(ENGINE), env=env).returncode:
            raise SystemExit("페르소나 데이터를 받지 못했습니다 — 위 메시지를 보십시오")
    log(f"  엔진 sample_panel.py {pack} {unit} (조각 2개 · 수십 초)")
    r = subprocess.run([sys.executable, str(TOOLS / "sample_panel.py"), pack, unit], cwd=str(ENGINE), env=env)
    src = ENGINE / "data" / pack / unit / "01_선정" / f"01_{unit}_패널.json"
    if r.returncode != 0 or not src.exists():
        raise SystemExit("패널 추출 실패 (엔진 sample_panel.py) — 위 메시지를 보십시오")
    doc = read_json(src, {})
    seed = int(doc.get("seed") or 0)
    used: set = set()
    import yaml
    cfg = yaml.safe_load((job.pack_dir / "persona.yaml").read_text(encoding="utf-8"))
    seats = {s["id"]: s for s in (cfg.get("author") or {}).get("seats") or []}
    strata = {s["id"]: s for s in (cfg.get("reviewer") or {}).get("strata") or []}
    for i, p in enumerate(doc.get("panel") or [], 1):
        key = str((p.get("persona") or {}).get("id") or i)
        p["번호"] = f"{'E' if p['role'] == 'author' else 'R'}{i:02d}"
        p["가명"] = _alias(seed, key, used)
        p["약력"] = _bio(p.get("persona") or {})
        if p["role"] == "author":
            p["asks"] = p.get("asks") or seats.get(p["seat"], {}).get("asks", "")
        else:
            p["desc"] = p.get("desc") or strata.get(p["seat"], {}).get("desc", "")
    write_json(out, doc)
    md = ["# 검수단 명부", "", f"- 재현: 엔진 `python tools/sample_panel.py {pack} {unit}` · 씨앗 {seed} · 조각 {', '.join(doc.get('shards') or [])}",
          "- 가상의 응답자다. 실제 전문가·수험생 검수를 대체하지 않는다.", "",
          "| 번호 | 구분 | 자리 | 가명 | 묻는 것 / 층 | 약력 |", "|---|---|---|---|---|---|"]
    for p in doc.get("panel") or []:
        md.append(f"| {p['번호']} | {'검수진' if p['role'] == 'author' else '수험생'} | {p.get('view') or p['seat']} | {p['가명']} | "
                  f"{(p.get('asks') or p.get('desc') or '').replace('|', '/')} | {p['약력']} |")
    if doc.get("shortfall"):
        md += ["", "## 못 채운 자리", *("- " + s for s in doc["shortfall"])]
    (job.p("01_선정", "패널명부.md")).write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"  → {out}  (검수진 {doc['counts']['author']} · 수험생 {doc['counts']['reviewer']})")
    if doc.get("shortfall"):
        warn("못 채운 자리: " + " / ".join(doc["shortfall"]))
    return doc


def cards(job: Job, role: str) -> str:
    doc = read_json(job.p("01_선정", "패널.json"), {}) or {}
    out = []
    for p in doc.get("panel") or []:
        if p["role"] != role:
            continue
        if role == "author":
            out.append(f"- {p['번호']} {p['가명']} — {p.get('view', '')}. 묻는 것: {p.get('asks', '')}\n  약력: {p['약력']}")
        else:
            out.append(f"- {p['번호']} {p['가명']} — {p['seat']}. {p.get('desc', '')}\n  약력: {p['약력']}")
    return "\n".join(out) or "(명부 없음 — panel 단계를 먼저 돌리십시오)"
