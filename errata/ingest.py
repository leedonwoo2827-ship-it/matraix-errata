"""s00 대상 — 산물 7종을 공통 단위 레코드로 편다. 원천 폴더에는 아무것도 쓰지 않는다.

단위 레코드
    uid       산물 안에서 유일한 열쇠        Q:m01-01 · V:m01-1#03 · S:understanding:1.3 · D:01_adsp-26-l01-s1-01#5
                                           L:L01-s1-03-001 · B:01:1.3 · T:q1-01-01#1
    product   Q V S D L B T
    batch     검토단에게 한 번에 읽히는 묶음(없으면 기계검사만)
    file      원천 파일 경로(정오표가 가리키는 곳)
    loc       사람이 읽는 위치 — "모의 1회 1번", "롱폼 L01 23:41", "교재 1권 1.3" …
    title     짧은 제목
    text      검토단이 읽는 본문(화면·자막 기준)
    say/read  낭독(자막)·발음 대본이 따로 있으면
    src_key   원천 문항 열쇠(m01-01) — 파급 연결에 쓴다
    x         기계검사용 원자료(choices·answer_index 등)
"""
from __future__ import annotations

import html as _html
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from errata.config import Job
from errata.util import CIRCLED, fmt_ts, log, read_json, strip_tags, warn

PRODUCTS = {
    "Q": "문항·해설",
    "V": "문제풀이 영상",
    "S": "요약노트",
    "D": "요약·문제 강의 덱",
    "L": "롱폼 화이트보드 강의",
    "B": "과목별 교재",
    "T": "용어 쇼츠",
}
SUBJECT_FILES = [("understanding", 1), ("planning", 2), ("analysis", 3)]


def _u(**kw) -> dict:
    kw.setdefault("batch", "")
    kw.setdefault("say", "")
    kw.setdefault("read", "")
    kw.setdefault("src_key", "")
    kw.setdefault("x", {})
    return kw


def qkey(rnd: int, no: int) -> str:
    return f"m{int(rnd):02d}-{int(no):02d}"


# ── Q 문항 ──────────────────────────────────────────────────────────────────
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def svg_text(svg: str) -> str:
    """SVG 안의 글자(aria-label·title·text)를 읽는다 — 검수단은 그림을 못 본다(실측: 「그림은 볼 수 없다」)."""
    bits = re.findall(r'aria-label="([^"]*)"', svg or "")
    bits += [strip_tags(t) for t in re.findall(r"<title[^>]*>(.*?)</title>", svg or "", re.S)]
    # ★ 글자마다 높이(y)를 붙인다 — 피라미드·표에서 「( ? )」 가 어느 층에 있는지가 정답을 가른다
    for attrs, t in re.findall(r"<text\b([^>]*)>(.*?)</text>", svg or "", re.S):
        y = re.search(r'\by="([\d.]+)"', attrs)
        bits.append(strip_tags(t) + (f"(y{int(float(y.group(1)))})" if y else ""))
    return " / ".join(b.strip() for b in bits if b.strip())


def _figs(s: str, assets: Dict[str, str], book: Path) -> str:
    def rep(m):
        name = Path(m.group(2)).stem
        svg = assets.get(name)
        if svg is None:
            f = book / "02" / "assets" / (name + ".svg")
            svg = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        return f"[그림 「{m.group(1)}」 — 그림 속 글자: {svg_text(svg) or '(없음)'}]"
    return IMG_RE.sub(rep, s or "")


def ingest_q(book: Path, pack: dict) -> List[dict]:
    out = []
    bsize = int((pack.get("batch") or {}).get("Q", 10))
    for f in sorted((book / "_rounds").glob("m*.json")):
        d = read_json(f, {}) or {}
        rnd = int(d.get("round") or re.sub(r"\D", "", f.stem) or 0)
        for q in d.get("questions") or []:
            no = int(q["question_no"])
            k = (no - 1) // bsize + 1
            ch = q.get("choices") or []
            ai = q.get("answer_index")
            assets = {Path(a.get("name", "")).stem: a.get("svg", "") for a in q.get("assets") or [] if isinstance(a, dict)}
            lines = [f"[문제] {_figs(q.get('question', ''), assets, book)}"]
            if q.get("passage"):
                lines.append(f"[지문] {_figs(strip_tags(str(q['passage'])), assets, book)}")
            if q.get("table"):
                lines.append(f"[표] {strip_tags(str(q['table']))}")
            for i, c in enumerate(ch):
                lines.append(f"{CIRCLED[i]} {c}")
            out.append(_u(
                uid=f"Q:{qkey(rnd, no)}", product="Q", batch=f"Q:m{rnd:02d}-{k}",
                file=str(f), loc=f"모의 {rnd}회 {no}번", title=f"모의 {rnd}회 {no}번 · {q.get('subject', '')}",
                text="\n".join(lines), say=q.get("explanation_speech", ""), src_key=qkey(rnd, no),
                x={"round": rnd, "no": no, "subject_no": q.get("subject_no"), "choices": ch, "answer_index": ai,
                   "explanation": q.get("explanation", ""), "explanation_speech": q.get("explanation_speech", ""),
                   "question": q.get("question", ""), "passage": q.get("passage", ""), "tags": q.get("tags") or [],
                   "difficulty": q.get("difficulty", "")},
            ))
    return out


# ── V 문제풀이 영상 ─────────────────────────────────────────────────────────
def ingest_v(book: Path) -> List[dict]:
    out = []
    for d in sorted((book / "05").glob("m*-*")):
        if not d.is_dir():
            continue
        bid = d.name                                   # m01-1
        m = re.match(r"m(\d+)-(\d+)", bid)
        if not m:
            continue
        rnd = int(m.group(1))
        sj = read_json(d / "script" / f"{bid}_script.json", {}) or {}
        tm = read_json(d / "source" / f"{bid}.timing.json", {}) or {}
        start = {s.get("scene"): s.get("startSec", 0) for s in tm.get("scenes") or []}
        video = book / "_videos" / f"{bid}.static.mp4"
        srt = d / "subtitles" / "subtitles.srt"
        for s in sj.get("scenes") or []:
            if not (s.get("narration") or "").strip():
                continue
            no = s.get("number")
            t = start.get(s.get("scene"), 0)
            out.append(_u(
                uid=f"V:{bid}#{int(s.get('scene', 0)):02d}", product="V", file=str(video if video.exists() else d),
                loc=f"문제풀이 영상 {bid} {fmt_ts(t)} ({s.get('heading', '')})", title=s.get("heading", ""),
                text=s.get("narration", ""), say=s.get("narration", ""), read=s.get("narration_text", ""),
                src_key=qkey(rnd, no) if no else "",
                x={"bundle": bid, "scene": s.get("scene"), "kind": s.get("kind"), "heading": s.get("heading", ""),
                   "t": t, "srt": str(srt), "lesson": str(d / "source" / f"lesson_{bid}.json")},
            ))
    return out


# ── S 요약노트 ──────────────────────────────────────────────────────────────
H_RE = re.compile(r"<h([1-4])\b[^>]*>(.*?)</h\1>", re.S | re.I)
QN_RE = re.compile(r"<span[^>]*class=\"[^\"]*\bqn\b[^\"]*\"[^>]*>(.*?)</span>", re.S | re.I)
SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S | re.I)


def _body(t: str) -> str:
    # ★ 마지막 </style> 앞을 통째로 버린다 — CSS 주석 안에 「<script>」「<h3>」 같은 글자가 들어 있어
    #   태그 정규식이 주석 속 글자를 진짜 태그로 잡는다(실측 2026-10-08).
    i = t.lower().rfind("</style>")
    if i >= 0:
        t = t[i + 8:]
    t = re.sub(r"<script\b.*?</script>", " ", t, flags=re.S | re.I)
    m = re.search(r"<body\b[^>]*>", t, re.I)
    return t[m.end():] if m else t


def _refs(fragment: str) -> List[str]:
    """배지 「모의 1회 1번 · 4회 1번 · …」 → ['m01-01','m04-01', …]"""
    keys = []
    for qn in QN_RE.findall(fragment):
        txt = strip_tags(qn)
        for r, n in re.findall(r"(\d+)\s*회\s*(\d+)\s*번", txt):
            keys.append(qkey(int(r), int(n)))
    return keys


def ingest_s(book: Path) -> List[dict]:
    out = []
    for name, sno in SUBJECT_FILES:
        f = book / "03" / f"summary_{name}.html"
        if not f.exists():
            continue
        body = _body(f.read_text(encoding="utf-8", errors="replace"))
        heads = list(H_RE.finditer(body))
        h2i = 0
        h2title = ""
        for i, h in enumerate(heads):
            lvl = int(h.group(1))
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            content = body[h.end():end]
            head_txt = strip_tags(QN_RE.sub("", h.group(2)))
            head_txt = re.sub(r"\s*\d+\s*$", "", head_txt).strip()       # 배지 숫자(문항 수) 떼기
            if lvl == 2:
                h2i += 1
                h2title = head_txt
            if lvl not in (2, 3):
                continue
            text = strip_tags(SVG_RE.sub(" [그림] ", content))
            if lvl == 2 and len(text) < 20:
                continue
            num = (re.match(r"([\d.]+\d)", head_txt) or re.match(r"(\d+)", head_txt))
            sec = num.group(1) if num else f"h{i}"
            out.append(_u(
                uid=f"S:{name}:{sec}", product="S", batch=f"S:{name}:{h2i:02d}", file=str(f),
                loc=f"요약노트 {sno}과목 {head_txt}", title=head_txt, text=text,
                x={"subject": sno, "h2": h2title, "refs": _refs(h.group(2) + content), "level": lvl},
            ))
    return out


# ── L 롱폼 화이트보드 ───────────────────────────────────────────────────────
def ingest_l(lf: Path) -> List[dict]:
    out = []
    cur = read_json(lf / "curriculum.json", {}) or {}
    for lec in cur.get("lectures") or []:
        lid = lec["id"]
        mp4 = lf / "out_wb" / lid / f"{lid}.mp4"
        offset = 0.0
        for sec in lec.get("sections") or []:
            sid = sec["id"]
            m = read_json(lf / "manuscripts" / lid / f"{sid}.json", None)
            sc = read_json(lf / "out_wb" / lid / "scenes" / f"{sid}.json", {}) or {}
            t0 = {s.get("id"): s.get("t0", 0) for s in sc.get("slides") or [] if s.get("id")}
            if m is None:
                warn(f"롱폼 원고가 없습니다: {lid}/{sid}")
                offset += float(sc.get("dur") or 0)
                continue
            batch = f"L:{lid}:{sid}"
            last_ex = ""
            for g in m.get("groups") or []:
                for s in g.get("slides") or []:
                    t = offset + float(t0.get(s.get("id"), 0))
                    src = ""
                    mm = re.search(r"모의\s*(\d+)\s*회\s*(\d+)\s*번", s.get("title", "") + " " + (s.get("say") or "")[:80])
                    if s.get("kind") in ("example-q", "example-a") and mm:
                        src = qkey(int(mm.group(1)), int(mm.group(2)))
                    if s.get("kind") == "example-q":
                        last_ex = src
                    elif s.get("kind") == "example-a" and not src:
                        src = last_ex                       # 풀이 장은 바로 앞 문제 장의 문항
                    text = s.get("title", "") + "\n" + "\n".join("- " + b for b in s.get("bullets") or [])
                    out.append(_u(
                        uid=f"L:{s.get('id')}", product="L", batch=batch, file=str(mp4 if mp4.exists() else lf / "manuscripts" / lid / f"{sid}.json"),
                        loc=f"롱폼 {lid} {fmt_ts(t)} ({sid} 「{s.get('title', '')}」)", title=s.get("title", ""),
                        text=text, say=s.get("say", ""), read=s.get("read", ""), src_key=src,
                        x={"lecture": lid, "section": sid, "kind": s.get("kind"), "t": t, "group": g.get("title", ""),
                           "manuscript": str(lf / "manuscripts" / lid / f"{sid}.json"), "src": s.get("src", "")},
                    ))
            for part in ("cover", "outro"):
                c = m.get(part) or {}
                if c.get("say"):
                    out.append(_u(
                        uid=f"L:{lid}-{sid}-{part}", product="L", batch=batch, file=str(mp4),
                        loc=f"롱폼 {lid} ({sid} {'여는 말' if part == 'cover' else '맺는 말'})", title=part,
                        text="", say=c.get("say", ""), read=c.get("read", ""),
                        x={"lecture": lid, "section": sid, "kind": part, "t": offset,
                           "manuscript": str(lf / "manuscripts" / lid / f"{sid}.json")},
                    ))
            offset += float(sc.get("dur") or 0)
    return out


# ── D 덱 (요약 과목별 강의 backplate 덱 + 문제 덱 pptx) ─────────────────────
def _pptx_slides(f: Path) -> List[str]:
    out = []
    try:
        z = zipfile.ZipFile(f)
    except Exception:  # noqa: BLE001
        return out
    names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                   key=lambda n: int(re.sub(r"\D", "", n)))
    for n in names:
        x = z.read(n).decode("utf-8", errors="replace")
        paras = []
        for p in re.findall(r"<a:p>.*?</a:p>", x, re.S):
            t = "".join(re.findall(r"<a:t>(.*?)</a:t>", p, re.S))
            if t.strip():
                paras.append(_html.unescape(t))
        out.append("\n".join(paras))
    return out


def ingest_d(lf: Optional[Path], book: Optional[Path]) -> List[dict]:
    out = []
    if lf:
        for pj in sorted((lf / "bp").glob("*/10_덱/deck.json")):
            slug = pj.parent.parent.name
            d = read_json(pj, {}) or {}
            ov = (read_json(pj.parent / "deck.overrides.json", {}) or {}).get("slides") or {}
            for s in d.get("slides") or []:
                no = s.get("no")
                o = ov.get(str(no)) or {}
                nar = (o.get("narration") or {}) if isinstance(o.get("narration"), dict) else {}
                say = nar.get("srt_text") or s.get("say") or ""
                out.append(_u(
                    uid=f"D:{slug}#{no}", product="D", file=str(pj), loc=f"요약 강의 덱 {slug} {no}장",
                    title=o.get("title") or s.get("title", ""), text=strip_tags(s.get("body", "")),
                    say=say, read=nar.get("text", ""),
                    x={"slug": slug, "no": no, "data_id": s.get("data_id", ""), "kind": s.get("kind")},
                ))
    if book and (book / "04-2").is_dir():
        for f in sorted((book / "04-2").glob("*.pptx")):
            m = re.match(r"(\d+)회_강의_(\d+)-(\d+)", f.stem)
            for i, t in enumerate(_pptx_slides(f), 1):
                out.append(_u(
                    uid=f"D:{f.stem}#{i}", product="D", file=str(f), loc=f"문제 덱 {f.name} {i}장",
                    title=t.split("\n", 1)[0][:60], text=t,
                    x={"pptx": f.name, "no": i, "round": int(m.group(1)) if m else 0,
                       "q_from": int(m.group(2)) if m else 0, "q_to": int(m.group(3)) if m else 0},
                ))
    return out


# ── B 과목별 교재 ───────────────────────────────────────────────────────────
def ingest_b(tb: Path) -> List[dict]:
    out = []
    for md in sorted(tb.glob("0[1-9]-*.md")):
        vol = md.name[:2]
        pdf = md.with_suffix(".pdf")
        text = md.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        sec_i, sec_title, sub_title, lec = 0, "", "", ""
        buf: List[str] = []
        seen: Dict[str, int] = {}

        def flush():
            nonlocal buf
            body = strip_tags("\n".join(buf))
            body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[그림]", body).strip()
            if len(body) >= 30:
                num = re.match(r"([\d.]+\d)", sub_title or sec_title)
                key = num.group(1) if num else f"{sec_i}.0"
                n = seen.get(key, 0)
                seen[key] = n + 1
                uid = f"B:{vol}:{key}" + (f"~{n}" if n else "")
                out.append(_u(
                    uid=uid, product="B", batch=f"B:{vol}:{sec_i:02d}", file=str(pdf if pdf.exists() else md),
                    loc=f"교재 {int(vol)}권 {sub_title or sec_title}", title=sub_title or sec_title, text=body,
                    x={"vol": vol, "md": str(md), "pdf": str(pdf) if pdf.exists() else "",
                       "hwpx": str(md.with_suffix(".hwpx")) if md.with_suffix(".hwpx").exists() else "",
                       "lecture": lec, "section": sec_title},
                ))
            buf = []

        for ln in lines:
            if ln.startswith("#### "):
                flush()
                sec_i += 1
                sec_title, sub_title = ln[5:].strip(), ""
            elif ln.startswith("##### "):
                flush()
                sub_title = ln[6:].strip()
            elif re.match(r"<h2[^>]*class=\"lec\"", ln):
                flush()
                lec = strip_tags(ln)
                sub_title = ""
                sec_title = lec
            else:
                buf.append(ln)
        flush()
    return out


# ── T 용어 쇼츠 ─────────────────────────────────────────────────────────────
def ingest_t(sh: Path, glob: str = "*") -> List[dict]:
    out = []
    for book in sorted(d for d in sh.glob(glob) if d.is_dir()):   # 과목 폴더 — pack.yaml shorts_glob
        for d in sorted(p for p in book.iterdir() if p.is_dir() and re.match(r"q\d", p.name)):
            s = read_json(d / "00_대본" / "script.json", None)
            if not s:
                continue
            tid = s.get("term_id") or d.name.split("_")[0]
            ch = re.match(r"(q\d+-\d+)", tid)
            batch = f"T:{ch.group(1) if ch else tid}"
            ver = ""
            lt = d / "05_완성" / "최신.txt"
            if lt.exists():
                ver = lt.read_text(encoding="utf-8", errors="replace").strip()
            vdir = d / "05_완성" / ver if ver else None
            mp4 = next(iter(sorted(vdir.glob("*.mp4"))), None) if vdir and vdir.is_dir() else None
            for sc in s.get("scenes") or []:
                t = float(sc.get("start_sec") or 0)
                out.append(_u(
                    uid=f"T:{d.name.split('_')[0]}#{sc.get('no')}", product="T", batch=batch,
                    file=str(mp4 or d / "00_대본" / "script.json"),
                    loc=f"쇼츠 {d.name} {fmt_ts(t)} ({sc.get('role', '')})", title=s.get("term", ""),
                    text=f"[칩] {sc.get('chip', '')}\n[주장] {sc.get('claim', '')}\n[자막] {sc.get('srt_text', '')}",
                    say=sc.get("srt_text", ""), read=sc.get("narration_text", ""),
                    x={"term_id": tid, "dir": str(d), "book": book.name, "source": sc.get("source", ""),
                       "t": t, "cues": sc.get("cues") or [], "manuscript": str(book / "_교재" / "원고.html")},
                ))
            yt = read_json(vdir / "유튜브.json", {}) if vdir and vdir.is_dir() else {}
            yt = yt or {}
            out.append(_u(
                uid=f"T:{d.name.split('_')[0]}#meta", product="T", batch=batch,
                file=str(vdir / "유튜브.json") if vdir else str(d / "00_대본" / "script.json"),
                loc=f"쇼츠 {d.name} 유튜브 제목·설명·태그", title=s.get("term", ""),
                text=f"[제목] {yt.get('title', '')}\n[설명] {yt.get('description', '')}\n[태그] {', '.join(yt.get('tags') or [])}"
                     f"\n[고정댓글] {yt.get('pinned_comment', '')}",
                x={"term_id": tid, "dir": str(d), "book": s.get("book", ""), "hashtags": s.get("hashtags") or [],
                   "tags": yt.get("tags") or [], "description": yt.get("description", ""), "kind": "meta",
                   "folder_book": book.name},
            ))
    return out


# ── 파급 연결 ────────────────────────────────────────────────────────────────
def build_links(units: List[dict]) -> Dict[str, List[str]]:
    """원천 문항(Q:mNN-NN) → 그 문항을 담은 파생 단위들."""
    links: Dict[str, List[str]] = {}
    for u in units:
        if u["product"] == "Q":
            continue
        keys = []
        if u.get("src_key"):
            keys.append(u["src_key"])
        if u["product"] == "S":
            keys += u["x"].get("refs") or []
        if u["product"] == "D" and u["x"].get("pptx"):
            m = re.search(r"(\d+)\s*번", u.get("title", ""))
            if m and u["x"]["round"]:
                keys.append(qkey(u["x"]["round"], int(m.group(1))))
        for k in keys:
            links.setdefault(f"Q:{k}", [])
            if u["uid"] not in links[f"Q:{k}"]:
                links[f"Q:{k}"].append(u["uid"])
    return links


def run(job: Job) -> Dict[str, int]:
    from errata.util import write_json, write_jsonl
    pack = job.pack()
    book, lf, tb, sh = job.src("book"), job.src("longform"), job.src("textbook"), job.src("shorts")
    units: List[dict] = []
    if book:
        units += ingest_q(book, pack)
        units += ingest_v(book)
        units += ingest_s(book)
    else:
        warn("문항 책 폴더가 지정되지 않았거나 없습니다 — Q·V·S 를 건너뜁니다")
    if lf:
        units += ingest_l(lf)
    else:
        warn("롱폼 잡 폴더가 없습니다 — L 을 건너뜁니다")
    if lf or book:
        units += ingest_d(lf, book)
    if tb:
        units += ingest_b(tb)
    else:
        warn("교재 폴더가 없습니다 — B 를 건너뜁니다")
    if sh:
        units += ingest_t(sh, job.pack().get("shorts_glob") or "*")
    else:
        warn("쇼츠 폴더가 없습니다 — T 를 건너뜁니다")
    seen = set()
    for u in units:
        if u["uid"] in seen:
            warn(f"uid 중복: {u['uid']}")
        seen.add(u["uid"])
    write_jsonl(job.p("00_대상", "units.jsonl"), units)
    links = build_links(units)
    write_json(job.p("00_대상", "links.json"), links)
    counts: Dict[str, int] = {}
    for u in units:
        counts[u["product"]] = counts.get(u["product"], 0) + 1
    batches: Dict[str, int] = {}
    for u in units:
        if u["batch"]:
            batches[u["product"]] = batches.get(u["product"], 0)
    for p in batches:
        batches[p] = len({u["batch"] for u in units if u["product"] == p and u["batch"]})
    summary = {"counts": counts, "batches": batches,
               "q_items": sum(1 for u in units if u["product"] == "Q"),
               "shorts": len({u["x"].get("term_id") for u in units if u["product"] == "T"}),
               "links": len(links)}
    write_json(job.p("00_대상", "counts.json"), summary)
    for p, n in counts.items():
        log(f"  {p} {PRODUCTS[p]:<12} 단위 {n:>5}  · 검토 배치 {batches.get(p, 0)}")
    log(f"  문항 {summary['q_items']} · 쇼츠 {summary['shorts']}편 · 파급 연결 {len(links)}")
    return counts
