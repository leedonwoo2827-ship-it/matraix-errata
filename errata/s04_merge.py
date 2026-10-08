"""s04 수용 — 기계검사·검토단 지적을 합치고, 중복을 없애고, 파급 위치를 붙이고, 초기 제안을 단다. 모델을 부르지 않는다.

파급: 원천 문항(Q:mNN-NN)의 지적에는 그 문항을 담은 파생 산물(영상 장면·롱폼 예제·요약 배지·문제 덱)을 붙이고,
     검토단 지적의 잘못된 문구(as_is)가 다른 산물에도 그대로 있으면 그 위치를 함께 적는다 — 한 번 고칠 때 같이 고치라고.
제안(초기 판정 후보 — 확정은 사람이 엑셀에서 한다)
    반영 후보   확신 · 상/중 · 인용 일치
    웹확인 필요 검수단이 외부 확인을 요청
    보류        그 밖
"""
from __future__ import annotations

import collections
from typing import Dict, List

from errata.config import Job
from errata.util import log, norm, read_json, read_jsonl, write_jsonl

PORDER = {p: i for i, p in enumerate("QVLDSBT")}
SORDER = {"상": 0, "중": 1, "하": 2}


def _suggest(f: dict) -> str:
    v = (f.get("verify") or {}).get("verdict")
    if v == "반대":
        return "보류"                      # 독립 재검증이 지적을 뒤집었다
    if v == "불확실":
        return "웹확인 필요"
    if v in ("동의", "수정동의") and f["severity"] in ("상", "중"):
        return "반영 후보"
    if f.get("needs_web"):
        return "웹확인 필요"
    if f["confidence"] == "확신" and f["severity"] in ("상", "중") and f.get("quote_ok", True):
        return "반영 후보"
    return "보류"


def run(job: Job, quiet: bool = False) -> List[dict]:
    units = read_jsonl(job.p("00_대상", "units.jsonl"))
    U = {u["uid"]: u for u in units}
    links: Dict[str, List[str]] = read_json(job.p("00_대상", "links.json"), {}) or {}
    F: List[dict] = list(read_jsonl(job.p("02_기계검사", "findings.jsonl")))
    n_machine = len(F)
    for p in sorted(job.p("03_검토").glob("*.json")):
        d = read_json(p, {}) or {}
        F += d.get("findings") or []
    seen: Dict[str, dict] = {}
    for f in F:
        if f["fid"] in seen:
            g = seen[f["fid"]]
            if f.get("reviewer") and f["reviewer"] not in (g.get("reviewer") or ""):
                g["reviewer"] = (g.get("reviewer") or "") + "·" + f["reviewer"]
            continue
        seen[f["fid"]] = dict(f)
    out = list(seen.values())
    # 기계가 이미 잡은 라벨을 검토단이 다시 낸 것은 버린다(같은 문제를 두 줄로 세지 않는다)
    machine_label = {f["uid"] for f in out if f["origin"] == "기계" and f.get("rule") in ("T-label", "T-book", "T-banned")}
    out = [f for f in out if not (f["origin"] == "검토단" and f["type"] == "라벨" and f["uid"] in machine_label)]
    from errata.s03b_verify import load as load_verify
    ver = load_verify(job)
    for f in out:
        v = ver.get(f["fid"])
        if v:
            f["verify"] = {"verdict": v.get("verdict", ""), "reason": v.get("reason", ""), "web_query": v.get("web_query", "")}
            if v.get("verdict") == "수정동의" and (v.get("better_to_be") or "").strip():
                f["verify"]["original_to_be"] = f["to_be"]
                f["to_be"] = v["better_to_be"].strip()
            if v.get("verdict") == "불확실" and v.get("web_query") and not f.get("web_query"):
                f["web_query"] = v["web_query"]
                f["needs_web"] = True
    # 파급 — 문구 검색용 정규화 본문(한 번만)
    hay = {u["uid"]: norm(" ".join([u.get("title", ""), u.get("text", ""), u.get("say", "")])) for u in units}
    for f in out:
        spread: List[str] = []
        key = f["uid"] if f["uid"].startswith("Q:") else (f"Q:{f['src_key']}" if f.get("src_key") else "")
        for uid in links.get(key, []) if key else []:
            if uid != f["uid"] and uid in U:
                spread.append(U[uid]["loc"])
        if key and key != f["uid"] and key in U:
            spread.insert(0, U[key]["loc"] + " (원천)")
        if f["origin"] == "검토단" and f["severity"] in ("상", "중") and f.get("quote_ok"):
            q = norm(f["as_is"])
            if len(q) >= 12:
                same = [U[uid]["loc"] for uid, h in hay.items() if uid != f["uid"] and q in h][:8]
                spread += [f"같은 문구: {loc}" for loc in same if loc not in spread]
        f["spread"] = spread
        f["suggest"] = _suggest(f)
    out.sort(key=lambda f: (SORDER.get(f["severity"], 9), PORDER.get(f["product"], 9), f["origin"] != "검토단", f["uid"]))
    write_jsonl(job.p("04_수용", "merged.jsonl"), out)
    if not quiet:
        c = collections.Counter((f["product"], f["severity"]) for f in out)
        log(f"  지적 {len(out)}건 (기계 {n_machine} · 검토단 {len(out) - min(n_machine, len(out))} 내외) · 파급 붙음 "
            f"{sum(1 for f in out if f['spread'])}")
        for p in "QVLDSBT":
            row = [c[(p, s)] for s in ("상", "중", "하")]
            if sum(row):
                log(f"    {p}  상 {row[0]:>4} · 중 {row[1]:>4} · 하 {row[2]:>4}")
    return out
