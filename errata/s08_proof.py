"""s08 교정지 — 조판실에서 넘어온 PDF 를 검수단이 읽고, 그 PDF 위에 교정 메모를 단 사본 + 쪽지 엑셀을 낸다.

    python -m errata proof --pdf <교정지.pdf> [--pages 1-10] [--force]

★ 원본 PDF 는 읽기만 한다. 결과는 data/jobs/<잡>/08_교정지/<PDF 이름>/ 안에만.
    검토/<p001-005>.json        묶음(쪽 5개 안팎)마다 검수단 1회 호출 — 있으면 건너뛴다(덮지 않음, 다시 하려면 --force)
    _prompts/                   프롬프트·응답 원문
    <이름>_교정.pdf             형광 표시 + 메모. 빨강 = 수정 예정 · 파랑 = 저자 문의
    <이름>_쪽지.xlsx            「정오표(수정 예정)」 쪽·잘못→바름 / 「저자 문의」 문의·저자 답변 칸 — 메모가 길면 여기에 전문
★ 두 갈래: 수정(틀린 것이 분명) → 정오표 · 문의(확인 필요·의심) → 저자에게. 판정·답변은 사람이 엑셀에 적는다.
★ 글자만 읽는다 — 쪽 넘김·그림 위치 같은 조판 모양은 보지 않는다.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from errata.config import Job
from errata.util import log, read_json, warn, write_json

PER_BATCH_PAGES = 5
PER_BATCH_CHARS = 9000
NOTE_MAX = 260                       # 메모가 이보다 길면 줄이고 쪽지로 보낸다
COLOR = {"수정": (1.0, 0.45, 0.45), "문의": (0.45, 0.65, 1.0)}
LABEL = {"수정": "수정 예정", "문의": "저자 문의"}


def _range(spec: str, n: int) -> List[int]:
    if not spec:
        return list(range(1, n + 1))
    out = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), min(int(b or a), n) + 1))
    return [p for p in out if 1 <= p <= n]


def _batches(texts: Dict[int, str], pages: List[int]) -> List[Tuple[str, List[int]]]:
    out, cur, size = [], [], 0
    for p in pages:
        t = texts.get(p, "")
        if cur and (len(cur) >= PER_BATCH_PAGES or size + len(t) > PER_BATCH_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(t)
    if cur:
        out.append(cur)
    return [(f"p{b[0]:03d}-{b[-1]:03d}", b) for b in out]


def _prompt(job: Job, doc: str, pages: List[int], texts: Dict[int, str]) -> str:
    import yaml
    from errata.s03_review import _cards, _fill
    pack = job.pack()
    pcfg = yaml.safe_load((job.pack_dir / "persona.yaml").read_text(encoding="utf-8")) or {}
    tpl = (Path(__file__).resolve().parent / "prompts" / "08_교정지.md").read_text(encoding="utf-8")
    body = "\n\n".join(f"== {p}쪽 ==\n{texts[p].strip()}" for p in pages)
    return _fill(tpl, exam_label=pack.get("label", ""), scope=(pack.get("scope") or "").strip(),
                 authors=_cards(job, "author"), students=_cards(job, "reviewer"),
                 require="\n".join("- " + r for r in pcfg.get("require") or []),
                 doc=doc, pages=f"{pages[0]}~{pages[-1]}", pages_text=body)


def _locate(page, as_is: str):
    """as_is 를 쪽 위에서 찾는다. 통째 → 앞 20자 → 뒤 20자 → 가장 긴 낱말 조각. 못 찾으면 None."""
    s = re.sub(r"\s+", " ", (as_is or "").strip())
    if not s:
        return None
    tries = [s, s[:20], s[-20:]] + sorted((w for w in re.split(r"[\s,.·()「」\"']+", s) if len(w) >= 4), key=len, reverse=True)[:3]
    for t in tries:
        if len(t) < 2:
            continue
        hits = page.search_for(t, quads=True)
        if hits:
            return hits[0], t != s
    return None


def run(job: Job, pdf: str, pages: str = "", force: bool = False) -> int:
    import pymupdf
    from errata.s03_review import _ask, _safe, extract_json
    src = Path(pdf.strip().strip('"'))
    if not src.is_file():
        warn(f"교정지 PDF 가 없습니다: {src}")
        return 2
    out_dir = job.p("08_교정지", src.stem)
    (out_dir / "검토").mkdir(parents=True, exist_ok=True)
    (out_dir / "_prompts").mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src)
    texts = {i + 1: doc[i].get_text("text") for i in range(doc.page_count)}
    sel = _range(pages, doc.page_count)
    bs = _batches(texts, [p for p in sel if texts[p].strip()])
    log(f"  교정지 {src.name} · {doc.page_count}쪽 중 {len(sel)}쪽 → 묶음 {len(bs)}개")
    for i, (bid, ps) in enumerate(bs, 1):
        rp = out_dir / "검토" / f"{bid}.json"
        if rp.exists() and not force:
            log(f"  [{i}/{len(bs)}] {bid} — 있음, 건너뜀")
            continue
        prompt = _prompt(job, src.name, ps, texts)
        (out_dir / "_prompts" / f"{_safe(bid)}.프롬프트.txt").write_text(prompt, encoding="utf-8")
        log(f"  [{i}/{len(bs)}] {bid} — {len(ps)}쪽 · 프롬프트 {len(prompt):,}자")
        t0 = time.time()
        try:
            raw = _ask(job, prompt)
            (out_dir / "_prompts" / f"{_safe(bid)}.응답.txt").write_text(raw, encoding="utf-8")
            res = extract_json(raw)
        except Exception as e:  # noqa: BLE001 — 한 묶음 실패로 전체를 멈추지 않는다
            warn(f"  {bid} 실패: {e}")
            continue
        fs = [f for f in res.get("findings") or [] if isinstance(f.get("page"), int) and f["page"] in ps]
        write_json(rp, {"pdf": str(src), "batch": bid, "pages": ps, "at": time.time(), "sec": round(time.time() - t0),
                        "findings": fs, "clean": res.get("clean") or []})
        log(f"    ✓ {time.time() - t0:.0f}초 · 수정 {sum(f.get('kind') == '수정' for f in fs)} · 문의 {sum(f.get('kind') != '수정' for f in fs)}")
    return annotate(job, src)


def _kind(f: dict) -> str:
    return "수정" if f.get("kind") == "수정" and f.get("confidence", "확신") == "확신" else "문의"


def annotate(job: Job, src: Path) -> int:
    """검토 결과 전부 → 메모 단 PDF 사본 + 쪽지 엑셀. 모델을 부르지 않는다."""
    import openpyxl
    import pymupdf
    from openpyxl.styles import Alignment, Font, PatternFill
    out_dir = job.p("08_교정지", src.stem)
    fs: List[dict] = []
    reviewed: List[int] = []
    for p in sorted((out_dir / "검토").glob("*.json")):
        d = read_json(p, {}) or {}
        reviewed += d.get("pages") or []
        fs += d.get("findings") or []
    doc = pymupdf.open(src)
    rows = []
    for f in fs:
        page = doc[f["page"] - 1]
        hit = _locate(page, f.get("as_is", ""))
        y = hit[0].rect.y0 if hit else -1
        rows.append((f["page"], y, f, page, hit))
    rows.sort(key=lambda r: (r[0], r[1]))
    num = {"수정": 0, "문의": 0}
    sheet: List[list] = []
    for i, (pno, y, f, page, hit) in enumerate(rows, 1):
        k = _kind(f)
        num[k] += 1
        no = f"{i:02d}"
        head = f"[{LABEL[k]} #{no} · {f.get('severity', '')} · {f.get('type', '')}]"
        body = (f"문의: {f['question']}\n" if k == "문의" and f.get("question") else "") \
            + f"잘못: {f.get('as_is', '')}\n바름: {f.get('to_be', '')}\n근거: {f.get('reason', '')}"
        note = f"{head}\n{body}"
        if len(note) > NOTE_MAX:
            note = note[:NOTE_MAX - 30].rstrip() + f"…\n→ 쪽지 #{no} 에 전문"
        if not hit:
            note += "\n(본문에서 위치를 찾지 못해 쪽 위쪽에 붙였습니다)"
        if hit:
            a = page.add_highlight_annot(hit[0])
            a.set_colors(stroke=COLOR[k])
            a.set_info(title=f"검수단 {f.get('reviewer', '')}", content=note, subject=LABEL[k])
            a.update()
            pt = pymupdf.Point(page.rect.width - 22, hit[0].rect.y0)       # 메모 아이콘은 오른쪽 여백에 — 본문을 가리지 않게
        else:
            pt = pymupdf.Point(page.rect.width - 24, 24 + 18 * (i % 6))
        t = page.add_text_annot(pt, note, icon="Comment" if k == "문의" else "Note")
        t.set_colors(stroke=COLOR[k])
        t.set_info(title=f"검수단 {f.get('reviewer', '')} · {LABEL[k]} #{no}", subject=LABEL[k])
        t.update()
        sheet.append([no, pno, f.get("as_is", ""), f.get("to_be", ""),
                      LABEL[k], f.get("question", "") if k == "문의" else "", "", "",
                      f.get("type", ""), f.get("severity", ""), f.get("reason", ""), f.get("reviewer", ""),
                      "찾음" if hit and not hit[1] else ("일부로 찾음" if hit else "못 찾음")])
    pdf_out = out_dir / f"{src.stem}_교정.pdf"
    try:
        doc.save(pdf_out, garbage=3, deflate=True)
    except Exception:  # noqa: BLE001 — 열려 있으면 새 이름
        pdf_out = pdf_out.with_name(f"{src.stem}_교정_{dt.datetime.now():%m%d-%H%M}.pdf")
        doc.save(pdf_out, garbage=3, deflate=True)

    # 쪽지 — 한 시트. 왼쪽 4열(번호·쪽·잘못·바름)만 조판자에게 간다: 확정 뒤 오른쪽 열을 지우고, 반영이 아닌 행을 지우면 끝.
    from openpyxl.comments import Comment
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "쪽지"
    give, keep = PatternFill("solid", fgColor="1E5631"), PatternFill("solid", fgColor="1F3A5F")
    font = Font(color="FFFFFF", bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    cols = [("번호", 6, "조판자에게 넘기는 열"), ("쪽", 5, ""), ("잘못 (AS IS)", 42, "PDF 에서 그대로 옮긴 문구"),
            ("바름 (TO BE)", 42, "바꿔 넣을 문구. 저자 문의는 제안 — 저자 답에 따라 고친다"),
            ("구분", 10, "수정 예정 = 틀린 것이 분명 · 저자 문의 = 저자 확인이 필요\n── 여기부터 오른쪽은 내부용(조판자에게 넘기기 전에 지운다)"),
            ("저자에게 묻는 말", 50, "저자 문의일 때만"), ("저자 답변", 30, "저자가 적는다"),
            ("편집자 판정", 11, "반영 · 보류 · 거절 — 반영이 아닌 행은 조판자에게 넘기기 전에 지운다"),
            ("유형", 9, ""), ("심각도", 7, "상 = 틀린 것을 가르친다 · 중 = 혼동·불일치 · 하 = 오탈자·표기"),
            ("근거", 55, ""), ("검수자", 7, "검수단 번호(01_선정/패널명부)"),
            ("PDF 위치", 10, "찾음 = 형광 표시 · 일부로 찾음 = 문구 일부에 표시 · 못 찾음 = 쪽 위쪽 여백에 메모만")]
    ws.append([c for c, _, _ in cols])
    for i, (_, w, tip) in enumerate(cols, 1):
        c = ws.cell(1, i)
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        c.fill, c.font = (give if i <= 4 else keep), font
        if tip:
            c.comment = Comment(tip, "검수엔진")
    blue = PatternFill("solid", fgColor="DDEBF7")
    for r in sheet:
        ws.append(r)
        for c in range(1, len(cols) + 1):
            ws.cell(ws.max_row, c).alignment = wrap
        if r[4] == LABEL["문의"]:
            ws.cell(ws.max_row, 5).fill = blue
    dv = DataValidation(type="list", formula1='"반영,보류,거절"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"H2:H{max(ws.max_row, 2) + 200}")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    xl = out_dir / f"{src.stem}_쪽지.xlsx"
    try:
        wb.save(xl)
    except PermissionError:
        xl = xl.with_name(f"{src.stem}_쪽지_{dt.datetime.now():%m%d-%H%M}.xlsx")
        wb.save(xl)
    log(f"  → {pdf_out.name} · {xl.name} — 수정 예정 {num['수정']} · 저자 문의 {num['문의']} (검토 {len(set(reviewed))}쪽)")
    return 0
