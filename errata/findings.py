"""지적(finding) 한 건의 모양 — 기계검사와 검토단이 같은 모양을 낸다.

    fid        안정 열쇠(uid·as_is·to_be·유형) — 다시 돌려도 같은 지적은 같은 fid → 사람 판정이 보존된다
    origin     기계 | 검토단
    type       정답오류 · 사실오류 · 불일치 · 표기 · 발음 · 라벨 · 범위 · 누락 · 인용불일치
    severity   상(틀린 것을 가르친다) · 중(혼동·불일치) · 하(표기·형식)
    confidence 확신 · 의심
"""
from __future__ import annotations

from typing import Optional

from errata.util import short_hash

TYPES = ["정답오류", "사실오류", "불일치", "표기", "발음", "라벨", "범위", "누락", "인용불일치", "기타"]
SEV = ["상", "중", "하"]


def make(unit: Optional[dict], *, as_is: str, to_be: str, type: str, severity: str, reason: str,
         origin: str = "기계", rule: str = "", confidence: str = "확신", reviewer: str = "(도구)",
         needs_web: bool = False, web_query: str = "", uid: str = "", product: str = "", loc: str = "",
         file: str = "", batch: str = "") -> dict:
    u = unit or {}
    uid = uid or u.get("uid", "")
    type = type if type in TYPES else "기타"
    severity = severity if severity in SEV else "중"
    return {
        "fid": short_hash(uid, as_is.strip(), to_be.strip(), type),
        "origin": origin, "rule": rule, "uid": uid, "product": product or u.get("product", ""),
        "loc": loc or u.get("loc", ""), "file": file or u.get("file", ""), "batch": batch or u.get("batch", ""),
        "src_key": u.get("src_key", ""),
        "as_is": as_is.strip(), "to_be": to_be.strip(), "type": type, "severity": severity,
        "confidence": confidence if confidence in ("확신", "의심") else "의심",
        "reason": reason.strip(), "reviewer": reviewer, "needs_web": bool(needs_web), "web_query": web_query.strip(),
    }
