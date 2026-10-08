"""s02 기계검사 — 셀 수 있는 것은 모델보다 스크립트가 정확하고 싸고 재현된다. 모델을 부르지 않는다.

규칙(rule) 이름은 지적마다 남는다. 같은 규칙이 수백 건 나오면 원인은 하나다(예: 쇼츠 해시태그 프롬프트).

    Q-choices      보기 4개·중복·정답 번호 범위
    Q-speech       해설 낭독 「정답은 N번」 ↔ answer_index
    Q-explain      해설 첫 문단이 정답이 아닌 보기와 같다
    Q-dist         회차 정답 위치 쏠림
    Q-md           02 문항 MD(정답 기호·보기) ↔ 원천
    V-answer       문제풀이 영상 정답 장면(제목·낭독) ↔ 원천 정답
    V-drift        영상 낭독 ↔ 원천 해설 낭독 / 문제문
    V-srt          영상 자막(SRT)에 낭독 문장이 빠짐
    L-example      롱폼 예제 장(보기·정답) ↔ 원천
    L-rounds       롱폼이 복사해 둔 회차 파일 ↔ 원천(낡은 사본)
    D-pptx         문제 덱 pptx 에 원천 보기가 없음
    D-bp           요약 강의 덱 낭독 ↔ 롱폼 원고 낭독
    *-script       한국어 본문에 섞인 이질 문자(키릴·아랍·태국·가나·�) — 모델 출력 오염
    *-banned       금지어(「기출」·미치환 자리표 …)
    T-label        쇼츠 시험 라벨(해시태그·태그·설명)
    T-book         쇼츠 book 라벨 ↔ 폴더
    T-source       쇼츠 근거 인용이 원고에 없음
    T-srt          쇼츠 자막 파일 ↔ 대본
    B-hwpx/B-pdf   교재 절이 hwpx·pdf 판에 없음
"""
from __future__ import annotations

import collections
import re
import zipfile
from pathlib import Path
from typing import Dict, List

from errata.config import Job
from errata.findings import make
from errata.util import CIRCLED, log, norm, read_json, read_jsonl, strip_tags, warn, write_jsonl

ANS_RE = re.compile(r"정답은\s*(\d)\s*번")


def _first_para(s: str) -> str:
    return (s or "").strip().split("\n\n", 1)[0].strip()


def _srt_text(p: Path) -> str:
    if not p or not p.exists():
        return ""
    t = p.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in t.splitlines() if ln.strip() and not ln.strip().isdigit() and "-->" not in ln]
    return "\n".join(lines)


def _sentences(s: str) -> List[str]:
    parts = re.split(r"(?<=[.?!다요])\s+|\n+", s or "")
    return [p.strip() for p in parts if len(norm(p)) >= 8]


def check_q(units: List[dict], book: Path | None, n_choices: int = 4, exam: str = "") -> List[dict]:
    F: List[dict] = []
    by_round: Dict[int, List[dict]] = collections.defaultdict(list)
    for u in units:
        if u["product"] != "Q":
            continue
        x = u["x"]
        ch, ai = x["choices"], x["answer_index"]
        by_round[x["round"]].append(u)
        if len(ch) != n_choices:
            F.append(make(u, as_is=f"보기 {len(ch)}개", to_be=f"보기 {n_choices}개", type="누락", severity="상",
                          reason=f"{exam} 는 {n_choices}지선다다 (pack.yaml choices)", rule="Q-choices"))
        dup = [c for c, n in collections.Counter(norm(c) for c in ch).items() if n > 1]
        if dup:
            F.append(make(u, as_is="같은 보기가 두 번 나온다", to_be="보기를 서로 다르게", type="표기", severity="상",
                          reason="보기 중복", rule="Q-choices"))
        if not isinstance(ai, int) or not 0 <= ai < len(ch):
            F.append(make(u, as_is=f"answer_index={ai}", to_be="0~3", type="정답오류", severity="상",
                          reason="정답 번호가 보기 범위 밖", rule="Q-choices"))
            continue
        sp = x.get("explanation_speech") or ""
        m = ANS_RE.search(sp[:60])
        if not m:
            F.append(make(u, as_is=sp[:40], to_be=f"정답은 {ai + 1}번입니다. …", type="누락", severity="하",
                          reason="해설 낭독이 「정답은 N번」으로 시작하지 않는다", rule="Q-speech"))
        elif int(m.group(1)) != ai + 1:
            F.append(make(u, as_is=m.group(0), to_be=f"정답은 {ai + 1}번", type="정답오류", severity="상",
                          reason=f"해설 낭독은 {m.group(1)}번, 정답 표기는 {CIRCLED[ai]}", rule="Q-speech"))
        fp = norm(_first_para(x.get("explanation") or ""))
        if fp:
            # ★ 완전 일치가 아니라 닮음으로 본다 — 해설 첫 문단은 정답 보기를 조금 고쳐 쓰는 경우가 많다(실측)
            from difflib import SequenceMatcher
            sims = [SequenceMatcher(None, norm(c), fp[: len(norm(c)) + 20]).ratio() if norm(c) else 0 for c in ch]
            best = max(range(len(ch)), key=lambda i: sims[i]) if ch else -1
            hit = [best] if best >= 0 and sims[best] >= 0.75 and sims[best] - sims[ai] >= 0.15 else []
            if hit and ai not in hit:
                F.append(make(u, as_is=_first_para(x["explanation"])[:120], to_be=f"{CIRCLED[ai]} {ch[ai][:80]}",
                              type="정답오류", severity="상",
                              reason=f"해설 첫 문단이 {CIRCLED[hit[0]]} 보기와 같은데 정답은 {CIRCLED[ai]}", rule="Q-explain"))
    for rnd, us in sorted(by_round.items()):
        cnt = collections.Counter(u["x"]["answer_index"] for u in us)
        top, n = cnt.most_common(1)[0] if cnt else (0, 0)
        if us and n / len(us) > 0.4:
            F.append(make(None, uid=f"Q:m{rnd:02d}", product="Q", loc=f"모의 {rnd}회 전체", file=us[0]["file"],
                          as_is=f"정답 {CIRCLED[top]} 이 {n}/{len(us)}", to_be="한 위치 40% 이하",
                          type="기타", severity="하", reason="정답 위치 쏠림", rule="Q-dist"))
    # 02 문항 MD
    if book and (book / "02").is_dir():
        for u in units:
            if u["product"] != "Q":
                continue
            md = book / "02" / f"{u['src_key']}.md"
            if not md.exists():
                F.append(make(u, as_is="(파일 없음)", to_be=md.name, type="누락", severity="중",
                              reason="02 문항 MD 가 없다", rule="Q-md", file=str(md)))
                continue
            t = md.read_text(encoding="utf-8", errors="replace")
            ai = u["x"]["answer_index"]
            m = re.search(r"^answer:\s*(\S+)", t, re.M)
            if m and isinstance(ai, int) and 0 <= ai < 10 and m.group(1).strip("'\"") != CIRCLED[ai]:
                F.append(make(u, as_is=f"answer: {m.group(1)}", to_be=f"answer: {CIRCLED[ai]}", type="정답오류",
                              severity="상", reason="02 MD 정답 ≠ 원천 정답", rule="Q-md", file=str(md)))
            body = norm(t)
            for i, c in enumerate(u["x"]["choices"]):
                if norm(c) and norm(c) not in body:
                    F.append(make(u, as_is=f"02 MD 의 {CIRCLED[i]}", to_be=c[:100], type="불일치", severity="중",
                                  reason="02 MD 보기가 원천과 다르다", rule="Q-md", file=str(md)))
    return F


def check_v(units: List[dict], Q: Dict[str, dict]) -> List[dict]:
    F: List[dict] = []
    srt_cache: Dict[str, str] = {}
    for u in units:
        if u["product"] != "V" or not u.get("src_key"):
            continue
        q = Q.get(u["src_key"])
        if not q:
            continue
        ai = q["x"]["answer_index"]
        kind = u["x"]["kind"]
        if kind == "answer":
            hm = re.search(r"정답\s*([①②③④])", u["x"]["heading"])
            if hm and isinstance(ai, int) and hm.group(1) != CIRCLED[ai]:
                F.append(make(u, as_is=u["x"]["heading"], to_be=re.sub(r"[①②③④]", CIRCLED[ai], u["x"]["heading"]),
                              type="정답오류", severity="상", reason="영상 정답 장면 제목 ≠ 원천 정답", rule="V-answer"))
            m = ANS_RE.search(u["say"][:60])
            if m and isinstance(ai, int) and int(m.group(1)) != ai + 1:
                F.append(make(u, as_is=m.group(0), to_be=f"정답은 {ai + 1}번", type="정답오류", severity="상",
                              reason="영상 낭독 정답 ≠ 원천 정답", rule="V-answer"))
            if norm(u["say"]) != norm(q["x"]["explanation_speech"]):
                F.append(make(u, as_is=u["say"][:120], to_be=q["x"]["explanation_speech"][:120], type="불일치",
                              severity="중", reason="영상 해설 낭독이 원천(_rounds) 해설 낭독과 다르다 — 원천을 고친 뒤 영상을 다시 뽑지 않았다",
                              rule="V-drift"))
        elif kind == "problem":
            if norm(q["x"]["question"]) not in norm(u["say"]) and norm(u["say"]) not in norm(q["x"]["question"]):
                F.append(make(u, as_is=u["say"][:120], to_be=q["x"]["question"][:120], type="불일치", severity="중",
                              reason="영상 문제 낭독 ≠ 원천 문제문", rule="V-drift"))
        srt = u["x"]["srt"]
        if srt not in srt_cache:
            srt_cache[srt] = norm(_srt_text(Path(srt)))
        if srt_cache[srt]:
            miss = [s for s in _sentences(u["say"]) if norm(s) not in srt_cache[srt]]
            if miss:
                F.append(make(u, as_is=f"자막에 없음: {miss[0][:80]}", to_be=miss[0][:80], type="누락", severity="하",
                              reason=f"낭독 문장 {len(miss)}개가 SRT 에 없다", rule="V-srt", file=srt))
    return F


def check_l(units: List[dict], Q: Dict[str, dict], lf: Path | None, book: Path | None) -> List[dict]:
    F: List[dict] = []
    for u in units:
        if u["product"] != "L" or not u.get("src_key"):
            continue
        q = Q.get(u["src_key"])
        if not q:
            F.append(make(u, as_is=u["title"], to_be="(원천에 없는 문항)", type="불일치", severity="중",
                          reason=f"예제가 가리키는 {u['src_key']} 가 원천에 없다", rule="L-example"))
            continue
        ai = q["x"]["answer_index"]
        if u["x"]["kind"] == "example-q":
            body = norm(u["text"])
            for i, c in enumerate(q["x"]["choices"]):
                if norm(c)[:40] and norm(c)[:40] not in body:
                    F.append(make(u, as_is=f"화면 {CIRCLED[i]}", to_be=c[:100], type="불일치", severity="중",
                                  reason="롱폼 예제 보기 ≠ 원천 보기", rule="L-example"))
        elif u["x"]["kind"] == "example-a":
            hm = re.search(r"정답\s*([①②③④])", u["title"])
            if hm and isinstance(ai, int) and hm.group(1) != CIRCLED[ai]:
                F.append(make(u, as_is=u["title"], to_be=re.sub(r"[①②③④]", CIRCLED[ai], u["title"]),
                              type="정답오류", severity="상", reason="롱폼 예제 정답 ≠ 원천 정답", rule="L-example"))
            m = ANS_RE.search((u["say"] or "")[:60])
            if m and isinstance(ai, int) and int(m.group(1)) != ai + 1:
                F.append(make(u, as_is=m.group(0), to_be=f"정답은 {ai + 1}번", type="정답오류", severity="상",
                              reason="롱폼 예제 낭독 정답 ≠ 원천 정답", rule="L-example"))
    if lf and book:
        for f in sorted((lf / "input" / "rounds").glob("m*.json")):
            src = book / "_rounds" / f.name
            if src.exists() and src.read_bytes() != f.read_bytes():
                a, b = read_json(f, {}), read_json(src, {})
                qa = {q["question_no"]: q for q in a.get("questions") or []}
                for qb in b.get("questions") or []:
                    qo = qa.get(qb["question_no"])
                    if qo and (qo.get("answer_index") != qb.get("answer_index") or qo.get("choices") != qb.get("choices")
                               or qo.get("explanation_speech") != qb.get("explanation_speech")):
                        rnd = int(re.sub(r"\D", "", f.stem))
                        F.append(make(None, uid=f"L:rounds:{f.stem}-{qb['question_no']:02d}", product="L",
                                      loc=f"롱폼 원천 사본 {f.name} {qb['question_no']}번", file=str(f),
                                      as_is="롱폼이 가진 사본", to_be="책 폴더 _rounds 최신본", type="불일치", severity="중",
                                      reason=f"모의 {rnd}회 {qb['question_no']}번이 원천과 다른 낡은 사본 — 예제 장이 옛 내용일 수 있다",
                                      rule="L-rounds"))
    return F


def check_d(units: List[dict], Q: Dict[str, dict], Lmap: Dict[str, dict]) -> List[dict]:
    F: List[dict] = []
    decks: Dict[str, List[dict]] = collections.defaultdict(list)
    bp: Dict[str, List[dict]] = collections.defaultdict(list)
    for u in units:
        if u["product"] != "D":
            continue
        if u["x"].get("pptx"):
            decks[u["x"]["pptx"]].append(u)
        else:
            bp[u["x"]["slug"]].append(u)
    # 요약 강의 덱(backplate) — ★ 덱 장 번호와 원고 장이 한 칸씩 어긋나 있어(표지·여는 말이 붙는다, 실측)
    #   장끼리 대조하지 않고 **섹션 전체 문장**으로 대조한다: 덱 낭독 문장이 원고 섹션에 없으면 덱이 다르다.
    by_sec: Dict[str, str] = collections.defaultdict(str)
    for lu in Lmap.values():
        by_sec[(lu["x"]["lecture"], lu["x"]["section"])] += " " + norm(lu["say"])
    for slug, us in bp.items():
        ids = [u["x"]["data_id"] for u in us if u["x"].get("data_id")]
        lu = Lmap.get("L:" + ids[0]) if ids else None
        if not lu:
            continue
        ms = by_sec.get((lu["x"]["lecture"], lu["x"]["section"]), "")
        for u in us:
            miss = [x for x in _sentences(u["say"]) if norm(x) not in ms]
            if miss:
                F.append(make(u, as_is=miss[0][:140], to_be="(롱폼 원고의 해당 문장)", type="불일치", severity="하",
                              reason=f"요약 강의 영상 낭독 문장 {len(miss)}개가 롱폼 원고({lu['x']['lecture']} {lu['x']['section']})에 없다",
                              rule="D-bp"))
    for name, us in decks.items():
        x = us[0]["x"]
        allt = norm("\n".join(u["text"] for u in us))
        for no in range(x["q_from"], x["q_to"] + 1):
            q = Q.get(f"m{x['round']:02d}-{no:02d}")
            if not q:
                continue
            for i, c in enumerate(q["x"]["choices"]):
                if norm(c)[:30] and norm(c)[:30] not in allt:
                    F.append(make({"uid": f"D:{Path(name).stem}", "product": "D", "loc": f"문제 덱 {name} ({no}번)",
                                   "file": us[0]["file"], "src_key": f"m{x['round']:02d}-{no:02d}"},
                                  as_is=f"{no}번 {CIRCLED[i]} (덱에 없음)", to_be=c[:100], type="불일치", severity="중",
                                  reason="문제 덱 pptx 에 원천 보기가 없다 — 덱이 옛 판", rule="D-pptx"))
    return F


def check_banned(units: List[dict], banned: Dict[str, str]) -> List[dict]:
    F: List[dict] = []
    if not banned:
        return F
    for u in units:
        if u["product"] in ("Q", "V"):
            continue
        for field in ("title", "text", "say"):
            s = u.get(field) or ""
            for w, rep in banned.items():
                if w and w in s:
                    i = s.find(w)
                    seg = s[max(0, i - 15): i + len(w) + 15].replace("\n", " ")
                    F.append(make(u, as_is=seg, to_be=seg.replace(w, rep or ""), type="표기" if rep else "누락",
                                  severity="중", reason=f"금지어 「{w}」 ({'화면' if field != 'say' else '낭독'})",
                                  rule=f"{u['product']}-banned"))
                    break
    return F


# ★ 그리스 문자는 넣지 않는다 — 통계 기호(α·β·σ·χ²·μ)가 정상이다
ALIEN_RE = re.compile(r"[Ѐ-ԯ؀-ۿ฀-๿ऀ-ॿ぀-ヿ�]+")


def check_script(units: List[dict]) -> List[dict]:
    F: List[dict] = []
    for u in units:
        for field in ("title", "text", "say", "read", "source"):
            s = (u.get(field) if field != "source" else (u.get("x") or {}).get("source")) or ""
            m = ALIEN_RE.search(s)
            if m:
                i = m.start()
                seg = s[max(0, i - 12): m.end() + 12].replace("\n", " ")
                F.append(make(u, as_is=seg, to_be=seg.replace(m.group(0), ""), type="표기", severity="상",
                              reason=f"한국어 문장에 이질 문자 「{m.group(0)}」(U+{ord(m.group(0)[0]):04X}) — "
                                     f"{({'read': '발음 대본(음성)', 'source': '근거 인용(내부 기록)'}).get(field, '자막·화면')}",
                              rule=f"{u['product']}-script"))
                break
    return F


def check_t(units: List[dict], pack: dict) -> List[dict]:
    F: List[dict] = []
    wrong = pack.get("wrong_labels") or []
    right = pack.get("right_labels") or []
    man_cache: Dict[str, str] = {}
    for u in units:
        if u["product"] != "T":
            continue
        x = u["x"]
        if x.get("kind") == "meta":
            tags = [t.lstrip("#") for t in (x.get("hashtags") or []) + (x.get("tags") or [])]
            bad = sorted({t for t in tags for w in wrong if w.replace(" ", "") == t.replace(" ", "")})
            if bad:
                F.append(make(u, as_is=" ".join("#" + b for b in bad), to_be="#" + (right[0] if right else "시험명"),
                              type="라벨", severity="중", reason="다른 시험의 해시태그·태그 — 검색 노출이 엉뚱한 시험으로 간다",
                              rule="T-label"))
            elif right and not any(r.replace(" ", "") in [t.replace(" ", "") for t in tags] for r in right):
                F.append(make(u, as_is=" ".join("#" + t for t in tags[:5]), to_be="#" + right[0], type="라벨",
                              severity="하", reason="시험 이름 태그가 없다", rule="T-label"))
            if x.get("book") and x.get("folder_book") and x["book"] != x["folder_book"]:
                F.append(make(u, as_is=f"book: {x['book']}", to_be=f"book: {x['folder_book']}", type="라벨",
                              severity="하", reason="대본 book 라벨이 과목 폴더 이름과 다르다", rule="T-book"))
            continue
        src = x.get("source") or ""
        man = x.get("manuscript") or ""
        if src and man:
            if man not in man_cache:
                p = Path(man)
                man_cache[man] = norm(strip_tags(p.read_text(encoding="utf-8", errors="replace"))) if p.exists() else ""
                if p.exists():
                    import html as _h
                    raw = p.read_text(encoding="utf-8", errors="replace")
                    man_cache[man] += norm(" ".join(_h.unescape(v) for v in re.findall(r'data-(?:say|read)="([^"]*)"', raw)))
            parts = [q for q in re.split(r"\s+/\s+", src) if len(norm(q)) >= 8]
            if man_cache[man] and parts and any(norm(q) not in man_cache[man] for q in parts):
                F.append(make(u, as_is=src[:120], to_be="(원고에 있는 문장으로)", type="인용불일치", severity="중",
                              reason="근거 인용(source)이 원고에 없다 — 지어낸 문장일 수 있다", rule="T-source"))
    return F


def _hwpx_text(p: Path) -> str:
    try:
        z = zipfile.ZipFile(p)
    except Exception:  # noqa: BLE001
        return ""
    out = []
    for n in z.namelist():
        if re.match(r"Contents/section\d+\.xml$", n):
            x = z.read(n).decode("utf-8", errors="replace")
            out += re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", x, re.S)
    import html as _h
    return _h.unescape(" ".join(out))


def _pdf_text(p: Path) -> str:
    try:
        import pymupdf
        with pymupdf.open(str(p)) as d:
            return "\n".join(pg.get_text() for pg in d)
    except Exception as e:  # noqa: BLE001
        warn(f"PDF 를 읽지 못했습니다: {p.name} ({e})")
        return ""


def check_b(units: List[dict]) -> List[dict]:
    F: List[dict] = []
    vols = collections.defaultdict(list)
    for u in units:
        if u["product"] == "B":
            vols[u["x"]["vol"]].append(u)
    for vol, us in sorted(vols.items()):
        x = us[0]["x"]
        for kind, path, reader in (("hwpx", x.get("hwpx"), _hwpx_text), ("pdf", x.get("pdf"), _pdf_text)):
            if not path:
                F.append(make(None, uid=f"B:{vol}", product="B", loc=f"교재 {int(vol)}권", file=x.get("md", ""),
                              as_is=f"{kind} 판 없음", to_be=f"{kind} 판", type="누락", severity="하",
                              reason=f"{kind} 파일이 없다", rule=f"B-{kind}"))
                continue
            t = norm(reader(Path(path)))
            if not t:
                continue
            miss = 0
            for u in us:
                body = norm(u["text"])
                probe = body[len(body) // 2: len(body) // 2 + 24]
                if probe and probe not in t:
                    miss += 1
                    F.append(make(u, as_is=u["text"][:80], to_be=f"{kind} 판에도 같은 본문", type="누락", severity="중",
                                  reason=f"md 원고의 이 절 본문이 {kind} 판에서 찾아지지 않는다 — 변환 중 빠졌거나 판이 다르다",
                                  rule=f"B-{kind}", file=path))
            log(f"    교재 {vol} {kind}: {len(us)}절 중 {miss}절 불일치")
    return F


def run(job: Job) -> List[dict]:
    units = read_jsonl(job.p("00_대상", "units.jsonl"))
    if not units:
        raise SystemExit("00_대상/units.jsonl 이 없습니다 — ingest 를 먼저 돌리십시오")
    pack = job.pack()
    Q = {u["src_key"]: u for u in units if u["product"] == "Q"}
    Lmap = {u["uid"]: u for u in units if u["product"] == "L"}
    F: List[dict] = []
    steps = [
        ("문항", lambda: check_q(units, job.src("book"), int(job.pack().get("choices") or 4), job.pack().get("exam", ""))),
        ("문제풀이 영상", lambda: check_v(units, Q)),
        ("롱폼", lambda: check_l(units, Q, job.src("longform"), job.src("book"))),
        ("덱", lambda: check_d(units, Q, Lmap)),
        ("금지어", lambda: check_banned(units, pack.get("banned_words") or {})),
        ("이질 문자", lambda: check_script(units)),
        ("쇼츠", lambda: check_t(units, pack)),
        ("교재 판 대조", lambda: check_b(units)),
    ]
    for name, fn in steps:
        try:
            got = fn()
        except Exception as e:  # noqa: BLE001  한 검사가 터져도 나머지는 돈다
            warn(f"  {name} 검사 실패: {e}")
            got = []
        F += got
        log(f"  {name:<8} 지적 {len(got)}")
    seen, out = set(), []
    for f in F:
        if f["fid"] not in seen:
            seen.add(f["fid"])
            out.append(f)
    write_jsonl(job.p("02_기계검사", "findings.jsonl"), out)
    by = collections.Counter(f["rule"] for f in out)
    log("  규칙별: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    return out
