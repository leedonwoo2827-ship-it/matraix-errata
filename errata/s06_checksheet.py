"""s06 검수표·이력 — 산물별 한 장 + 이력 한 장. 모델을 부르지 않는다.

판정은 엔진 checksheet.py 와 같은 넷: 통과 · 보류 · 사람 확인 · 대조 불가. **빈칸을 두지 않는다.**
    대조 불가  단위가 없거나, 검토 배치가 다 돌지 않았다(안 본 것을 통과로 읽히게 두지 않는다)
    보류       미판정 상 지적이 있다 / 반영으로 확정했으나 산물에 아직 고쳐지지 않았다(고친 뒤 다시 돌리면 사라진다)
    사람 확인  웹확인 필요·의심 지적이 남았다
    통과       위 어느 것도 아니다 — ★ "좋은 산물인가" 는 답하지 않았다. 그것은 세서 알 수 없다.
07_이력.md 는 **안 끝난 것을 맨 위에** 둔다.
"""
from __future__ import annotations

import collections
import datetime as dt
from typing import Dict, List

from errata.config import Job
from errata.ingest import PRODUCTS
from errata.util import log, read_json, read_jsonl, warn


def _state(job: Job):
    units = read_jsonl(job.p("00_대상", "units.jsonl"))
    merged = read_jsonl(job.p("04_수용", "merged.jsonl"))
    dec = read_json(job.p("05_정오표", "decisions.json"), {}) or {}
    batches: Dict[str, set] = collections.defaultdict(set)
    for u in units:
        if u.get("batch"):
            batches[u["product"]].add(u["batch"])
    reviewed: Dict[str, set] = collections.defaultdict(set)
    for p in job.p("03_검토").glob("*.json") if job.p("03_검토").exists() else []:
        d = read_json(p, {}) or {}
        if d.get("batch"):
            reviewed[d.get("product", "")].add(d["batch"])
    failed = [read_json(p, {}).get("batch") for p in job.p("03_검토", "_실패").glob("*.json")] \
        if job.p("03_검토", "_실패").exists() else []
    return units, merged, dec, batches, reviewed, failed


def run(job: Job) -> None:
    units, merged, dec, batches, reviewed, failed = _state(job)
    cnt = collections.Counter(u["product"] for u in units)
    out_dir = job.p("06_검수표")
    out_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = []
    for p in "QVSDLBT":
        fs = [f for f in merged if f["product"] == p]
        d = lambda f: (dec.get(f["fid"]) or {}).get("판정", "")  # noqa: E731
        open_hi = [f for f in fs if f["severity"] == "상" and not d(f)]
        applied = [f for f in fs if d(f) == "반영"]
        human = [f for f in fs if not d(f) and (f.get("needs_web") or f["confidence"] == "의심")]
        nb, nr = len(batches.get(p, ())), len(reviewed.get(p, set()) & batches.get(p, set()))
        if not cnt.get(p):
            verdict, why = "대조 불가", "단위가 없다(산물 폴더 미지정)"
        elif nb and nr < nb:
            verdict, why = "대조 불가", f"검토 배치 {nr}/{nb} 만 돌았다"
        elif open_hi or applied:
            verdict, why = "보류", f"미판정 상 {len(open_hi)}건 · 반영 확정(산물 수정 대기) {len(applied)}건"
        elif human:
            verdict, why = "사람 확인", f"웹확인·의심 미판정 {len(human)}건"
        else:
            verdict, why = "통과", "미해결 지적 없음 — 셀 수 있는 것만 본 판정이다"
        summary.append((p, verdict, why, len(fs), nr, nb))
        sev = collections.Counter(f["severity"] for f in fs)
        lines = [f"# 검수표 — {PRODUCTS[p]}", "", f"- 판정: **{verdict}** — {why}", f"- 갱신: {now}",
                 f"- 단위 {cnt.get(p, 0)} · 검토 배치 {nr}/{nb}" + (" (기계검사만 하는 산물)" if not nb else ""),
                 f"- 지적 {len(fs)} (상 {sev['상']} · 중 {sev['중']} · 하 {sev['하']}) · 판정 완료 {sum(1 for f in fs if d(f))}", "",
                 "| 항목 | 값 | 판정 |", "|---|---|---|"]
        rules = collections.Counter(f.get("rule") or f["origin"] for f in fs)
        for k, v in rules.most_common():
            lines.append(f"| {k} | {v}건 | {'보류' if any(f['severity'] == '상' and (f.get('rule') or f['origin']) == k for f in fs) else '사람 확인'} |")
        if not rules:
            lines.append(f"| 지적 | 0건 | {'통과' if verdict == '통과' else verdict} |")
        lines += ["", "## 미판정 상 (최대 30)", ""]
        lines += [f"- {f['loc']} — 「{f['as_is'][:80]}」 → 「{f['to_be'][:80]}」 ({f['reason'][:80]})" for f in open_hi[:30]] or ["- 없음"]
        (out_dir / f"{p}_{PRODUCTS[p]}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 07 이력 — 안 끝난 것이 맨 위
    prog = read_json(job.p("progress.json"), {}) or {}
    open_hi_all = [f for f in merged if f["severity"] == "상" and not (dec.get(f["fid"]) or {}).get("판정")]
    web = [f for f in merged if f.get("needs_web") and not (dec.get(f["fid"]) or {}).get("판정")]
    L = [f"# 이력 — {job.cfg.get('label', job.name)}", "", f"갱신 {now}", "", "## 아직 안 끝난 것", ""]
    if failed:
        L.append(f"- 실패한 검토 배치 {len(failed)}개: {', '.join(map(str, failed[:20]))} — `run.bat review` 를 다시 돌리면 그것만 다시 한다")
    nb_all = sum(len(v) for v in batches.values())
    nr_all = sum(len(reviewed.get(p, set()) & batches.get(p, set())) for p in batches)
    if nr_all < nb_all:
        L.append(f"- 검토 배치 {nr_all}/{nb_all} 완료")
    L.append(f"- 미판정 상 지적 {len(open_hi_all)}건 · 웹확인 필요 {len(web)}건")
    L += ["", "## 산물별 검수표", "", "| 산물 | 판정 | 이유 | 지적 | 검토 배치 |", "|---|---|---|---|---|"]
    L += [f"| {PRODUCTS[p]} | {v} | {w} | {n} | {r}/{b} |" for p, v, w, n, r, b in summary]
    L += ["", "## 지나온 자리", "",
          "- 00 대상: 산물 폴더 4곳을 읽기만 해서 단위 레코드로 폈다 (`00_대상/units.jsonl`)",
          "- 01 선정: MatrAIx Persona-1M 에서 검수진 4 + 수험생 6 (`01_선정/패널명부.md`)",
          "- 02 기계검사: 셀 수 있는 것 (`02_기계검사/findings.jsonl`)",
          f"- 03 검토: 배치 {nr_all}/{nb_all} · 호출 {prog.get('calls', 0)}회 · {prog.get('sec', 0) / 3600:.1f}시간",
          "- 04 수용: 합치기·파급·제안 (`04_수용/merged.jsonl`)",
          "- 05 정오표: 내부 엑셀(판정) → 게시 HTML(반영만)", ""]
    job.p("07_이력.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    for p, v, w, n, r, b in summary:
        log(f"  {p} {PRODUCTS[p]:<12} {v:<6} {w}")
    try:
        from errata import s06_coverage
        s06_coverage.run(job)
    except Exception as e:  # noqa: BLE001 — 기록 엑셀이 실패해도 검수표·이력은 남긴다
        warn(f"검수기록.xlsx 를 만들지 못했습니다({e})")


def status(job: Job) -> None:
    units, merged, dec, batches, reviewed, failed = _state(job)
    prog = read_json(job.p("progress.json"), {}) or {}
    log(f"  단위 {len(units)} · 지적 {len(merged)} · 판정 {len(dec)}")
    for p in "QVSDLBT":
        nb, nr = len(batches.get(p, ())), len(reviewed.get(p, set()) & batches.get(p, set()))
        if nb or any(u["product"] == p for u in units):
            log(f"  {p} {PRODUCTS[p]:<12} 검토 {nr}/{nb}")
    if prog:
        log(f"  진행 {prog.get('stage')} · 현재 {prog.get('current') or '-'} · 실패 {len(failed)}")
