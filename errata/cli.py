"""산물 검수엔진 CLI — 웹앱 버튼과 같은 것을 부른다.

    python -m errata <단계> [--job adsp] [--only Q:m01-,L:L01:] [--force]

단계
    ingest    s00 대상   산물 폴더 → data/jobs/<잡>/00_대상/units.jsonl · links.json      (도구)
    panel     s01 선정   MatrAIx 검수단 10명 → 01_선정/패널.json                     (도구, 처음엔 페르소나 내려받기)
    machine   s02 기계검사 셀 수 있는 것 → 02_기계검사/findings.jsonl              (도구, 무료)
    review    s03 검토   검수단이 배치마다 읽고 지적 → 03_검토/<배치>.json          (모델, 배치당 1회)
    verify    s03b 재검증 무거운 지적(상·정답/사실오류)을 다른 호출에서 독립 재판정       (모델, 12건당 1회)
    merge     s04 수용   합치기·중복 제거·파급 위치·초기 판정 → 04_수용/merged.jsonl   (도구)
    errata    s05 정오표 내부 엑셀 + 게시 HTML → 05_정오표/                         (도구)
    check     s06 검수표 산물별 검수표 + 이력 → 06_검수표/ · 07_이력.md            (도구)
    all       위 전부 차례로 (review 는 있으면 건너뛰고 남은 것만)
    trial     job.json trial_only 범위로 all (1회차 세로 관통 시험)
    status    어디까지 왔나
    proof     s08 교정지 조판 PDF → 메모 단 사본 + 쪽지 엑셀 → 08_교정지/  (모델, 5쪽당 1회)
              python -m errata proof --pdf <교정지.pdf> [--pages 1-10]
"""
from __future__ import annotations

import argparse
import sys
import time

from errata.config import load_job
from errata.util import log, warn

STAGES = ["ingest", "panel", "machine", "review", "verify", "merge", "errata", "check", "all", "trial", "status", "proof"]


def _stage(job, name: str, only: list, force: bool, pdf: str = "", pages: str = "") -> int:
    t0 = time.time()
    log(f"▶ {name}")
    if name == "ingest":
        from errata import ingest
        ingest.run(job)
    elif name == "panel":
        from errata import s01_panel
        s01_panel.run(job, force=force)
    elif name == "machine":
        from errata import s02_machine
        s02_machine.run(job)
    elif name == "review":
        from errata import s03_review
        code = s03_review.run(job, only=only, force=force)
        if code:
            return code
    elif name == "verify":
        from errata import s03b_verify
        code = s03b_verify.run(job, only=only, force=force)
        if code:
            return code
    elif name == "merge":
        from errata import s04_merge
        s04_merge.run(job)
    elif name == "errata":
        from errata import s05_errata
        s05_errata.run(job)
    elif name == "check":
        from errata import s06_checksheet
        s06_checksheet.run(job)
    elif name == "proof":
        from errata import s08_proof
        code = s08_proof.run(job, pdf, pages=pages, force=force)
        if code:
            return code
    elif name == "status":
        from errata import s06_checksheet
        s06_checksheet.status(job)
    log(f"✓ {name} ({time.time() - t0:.0f}초)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="errata", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=STAGES)
    ap.add_argument("--job", default="")
    ap.add_argument("--only", default="", help="배치 id 접두어(쉼표로 여럿). 예: Q:m01-,L:L01:")
    ap.add_argument("--force", action="store_true", help="이미 있는 결과도 다시 만든다(review 는 배치 파일을 덮는다)")
    ap.add_argument("--pdf", default="", help="proof: 교정지 PDF 경로")
    ap.add_argument("--pages", default="", help="proof: 쪽 범위. 예: 1-10,15")
    a = ap.parse_args(argv)
    job = load_job(a.job)
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    if a.stage in ("all", "trial"):
        if a.stage == "trial":
            only = only or [s.strip() for s in (job.get("trial_only") or "").split(",") if s.strip()]
            log(f"1회차 세로 관통 시험 — 범위 {', '.join(only)}")
        for st in ["ingest", "panel", "machine", "review", "verify", "merge", "errata", "check"]:
            code = _stage(job, st, only, a.force if st not in ("panel",) else False)
            if code:
                warn(f"{st} 단계가 멈췄습니다(코드 {code}) — 같은 명령을 다시 돌리면 남은 것만 이어 합니다")
                # 멈춰도 지금까지의 정오표는 뽑아 둔다
                for tail in ["merge", "errata", "check"]:
                    _stage(job, tail, only, False)
                return code
        return 0
    return _stage(job, a.stage, only, a.force, pdf=a.pdf, pages=a.pages)


if __name__ == "__main__":
    raise SystemExit(main())
