# -*- coding: utf-8 -*-
"""페르소나 1M → 검토 패널 16명. **같은 씨앗이면 같은 16명이 나온다.**

무엇을 하는가
────────────────────────────────────────────────────────────────────────────
`packs/<pack>/persona.yaml` 의 조건대로 **저자진 4명 + 연구진 12명**을 뽑아
`data/<pack>/<unit>/01_선정/01_<unit>_패널.json` 에 적고, 사람이 읽는 판(`.md`)을 함께 낸다.
그 두 파일이 검토의 명부다 — "왜 이 사람들인가" 에 답하는 것이 01번 칸의 일이다.

왜 `must` 를 조금만 쓰는가
────────────────────────────────────────────────────────────────────────────
★ 페르소나마다 1,290차원 중 **채워진 것이 다르다**(개발용 표본 200명 실측:
  `skill_coding` 이 빈 사람이 119명). 조건을 여럿 AND 로 걸면 조용히 0명이 뽑힌다.
  그래서 자리를 정하는 조건만 `must` 로 걸고 나머지는 `prefer` 로 두어 점수로 고른다.

왜 이진 blob 을 직접 푸는가
────────────────────────────────────────────────────────────────────────────
저장소의 `AttributeCodec.decode_row()` 는 한 사람마다 1,290칸을 돌며 값 이름을
거꾸로 찾는다(칸마다 선형 탐색). 10만 명에 쓰면 끝나지 않는다.
그래서 **코드표는 그 코덱에서 그대로 받고**(field 순서·값 이름의 소유자는 저장소다),
니블을 꺼내는 부분만 numpy 로 벡터화했다. 규칙은 `schema.py` 의 것을 옮긴 것이다:

    codes[0::2] = packed[:645] & 0x0F        # 짝수 칸 = 낮은 니블
    codes[1::2] = (packed[:645] >> 4) & 0x0F # 홀수 칸 = 높은 니블
    null bit i  = (bitmap[i//8] >> (i%8)) & 1

★ 조건에 적힌 차원·값이 스키마에 없으면 **멈춘다.** 오타 하나로 조용히 0명이
  뽑히는 것이 이 도구에서 가장 위험한 실패다.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import numpy as np

try:
    import pyarrow.parquet as pq
    import yaml
except ImportError as e:
    sys.exit("의존성이 없습니다(%s):  pip install pyarrow pyyaml" % e.name)

ATTRIBUTE_COUNT = 1290
ATTRIBUTE_BYTES = (ATTRIBUTE_COUNT + 1) // 2      # 645
NULL_BITMAP_BYTES = (ATTRIBUTE_COUNT + 7) // 8    # 162

# 명부에 사람을 알아볼 수 있게 적어 두는 차원 (뽑는 조건과 무관하게 항상 기록)
PROFILE_DIMS = [
    "age_bracket", "region", "lang_korean", "english_proficiency",
    "domain", "domain_characteristics", "role_function", "dev_role_archetype",
    "skill_coding", "skill_debugging", "highest_education", "years_experience",
    "ind_technology", "tech_savviness", "fam_data_science",
    "coding_ai_usage_frequency", "coding_ai_complexity_comfort",
    "dominant_trait", "primary_language",
]


def load_codec_tables(release: Path):
    """저장소 코덱에서 field 순서와 값 이름을 받는다. 우리가 다시 만들지 않는다."""
    sys.path.insert(0, str(Path("MatrAIx-Persona-8B").resolve()))
    from persona.post_process.unified_dataset.schema import AttributeCodec

    codec = AttributeCodec.from_codes_schema(release / "persona_codes.schema.json")
    index_of = {fid: i for i, fid in enumerate(codec.field_ids)}
    # 값코드 → 이름
    labels = [
        {code: name for name, code in vmap.items()}
        for vmap in codec.value_codes
    ]
    return codec.field_ids, index_of, labels


def collect_conditions(cfg):
    """persona.yaml 에서 쓰인 (차원, 값) 전부. 검증용."""
    used = {}
    def eat(block):
        for key in ("must", "prefer"):
            for dim, vals in (block.get(key) or {}).items():
                used.setdefault(dim, set()).update(vals)
        for cond in (block.get("must_any") or []):
            for dim, vals in cond.items():
                used.setdefault(dim, set()).update(vals)
    eat(cfg.get("common") or {})
    for group in ("author", "reviewer"):
        g = cfg.get(group) or {}
        eat(g.get("common") or {})
    for seat in (cfg.get("author") or {}).get("seats") or []:
        eat(seat)
    for st in (cfg.get("reviewer") or {}).get("strata") or []:
        eat(st)
    return used


def validate(used, index_of, labels):
    """차원 이름·값이 스키마에 있는지 확인한다. 없으면 멈춘다."""
    problems = []
    for dim, vals in sorted(used.items()):
        if dim not in index_of:
            problems.append("차원이 없습니다: %s" % dim)
            continue
        known = set(labels[index_of[dim]].values())
        for v in sorted(vals):
            if v not in known:
                problems.append(
                    "값이 없습니다: %s = %r\n      쓸 수 있는 값: %s"
                    % (dim, v, ", ".join(sorted(known))))
    if problems:
        print("★ 조건이 스키마와 맞지 않습니다 — 이대로 두면 조용히 0명이 뽑힙니다.\n")
        for p in problems:
            print("   " + p)
        sys.exit(1)


def read_shard(path: Path, want_dims, index_of, labels):
    """조각 하나를 읽어 필요한 차원만 벡터로 푼다."""
    pf = pq.ParquetFile(path)
    cols = ["source", "source_row_index", "attributes", "null_bitmap",
            "populated_attribute_count"]
    have = set(pf.schema_arrow.names)
    cols = [c for c in cols if c in have]

    out_vals = {d: [] for d in want_dims}
    sources, row_ids, pops = [], [], []

    for batch in pf.iter_batches(batch_size=20000, columns=cols):
        n = batch.num_rows
        sources += batch.column("source").to_pylist()
        row_ids += batch.column("source_row_index").to_pylist()
        pops += (batch.column("populated_attribute_count").to_pylist()
                 if "populated_attribute_count" in cols else [None] * n)

        attrs = batch.column("attributes").to_pylist()
        data = np.frombuffer(b"".join(a[:ATTRIBUTE_BYTES] for a in attrs),
                             dtype=np.uint8).reshape(n, ATTRIBUTE_BYTES)

        nb_list = (batch.column("null_bitmap").to_pylist()
                   if "null_bitmap" in cols else [None] * n)
        blank = b"\x00" * NULL_BITMAP_BYTES
        nulls = np.frombuffer(
            b"".join((x or blank)[:NULL_BITMAP_BYTES].ljust(NULL_BITMAP_BYTES, b"\x00")
                     for x in nb_list),
            dtype=np.uint8).reshape(n, NULL_BITMAP_BYTES)

        for dim in want_dims:
            i = index_of[dim]
            byte = data[:, i // 2]
            code = (byte & 0x0F) if i % 2 == 0 else ((byte >> 4) & 0x0F)
            is_null = (nulls[:, i // 8] >> (i % 8)) & 1
            lab = labels[i]
            vals = [None if is_null[k] else lab.get(int(code[k]))
                    for k in range(n)]
            out_vals[dim] += vals

    return sources, row_ids, pops, out_vals


def match(block, key, row):
    """must / prefer 판정. 값이 비어 있으면 맞지 않은 것으로 본다."""
    hits = 0
    for dim, allowed in (block.get(key) or {}).items():
        if row.get(dim) in allowed:
            hits += 1
        elif key == "must":
            return None          # must 하나라도 어긋나면 탈락
    return hits


def match_any(block, row):
    """`must_any` — 여러 조건 중 **하나라도** 맞으면 통과.

    ★ 왜 필요한가(실측 2026-09-03): 인간 접지 조각 0005 에서 `lang_korean` 이
      채워진 사람은 99,847명 중 19명뿐이고 전원이 `real_human_survey`(355명) 출신이다.
      StackOverflow·GSS 페르소나는 그 칸이 비어 있다. 반면 `region` 은 채워져 있다
      (East Asia 752명, 그중 633명은 skill_coding 까지 있음).
      한 차원만 고집하면 "국내 IT 직군" 이라는 같은 뜻을 데이터가 다르게 적어 둔 것을
      놓치고 조용히 0명이 뽑힌다. 그래서 **채워져 있는 쪽으로 걸리게** 한다.
    """
    alts = block.get("must_any")
    if not alts:
        return True, None
    for cond in alts:
        for dim, allowed in cond.items():
            if row.get(dim) in allowed:
                return True, "%s=%s" % (dim, row.get(dim))
    return False, None


def pick(rows, layers, n, seed, prefer_sources, taken):
    """`layers` = 겹쳐 거는 조건들 — 전체 공통 → 그룹 공통 → 자리.

    ★ 겹으로 받는 이유: 전문가 4자리에 같은 경력 하한을 네 번 적어 두면
      한 곳만 고치고 나머지를 잊는다. 그룹 공통을 한 곳에 두는 것이 맞다.
    """
    cands = []
    for r in rows:
        if r["_key"] in taken:
            continue
        gates, dropped = [], False
        for blk in layers:
            ok, via = match_any(blk, r)
            if not ok:
                dropped = True
                break
            if via:
                gates.append(via)
            if match(blk, "must", r) is None:
                dropped = True
                break
        if dropped:
            continue
        r["_gate"] = " · ".join(gates) or None   # 어느 차원으로 통과했는지 명부에 남긴다
        score = sum((match(blk, "prefer", r) or 0) for blk in layers)
        src_rank = 0 if r["source"] in prefer_sources else 1
        # ★ 마지막 갈림은 씨앗 해시다. 파일 순서에 기대면 조각을 바꿀 때 표본이 흔들린다.
        tie = hashlib.sha1(("%s|%s" % (seed, r["_key"])).encode()).hexdigest()
        cands.append((-score, src_rank, tie, r))
    cands.sort(key=lambda x: (x[0], x[1], x[2]))
    chosen = [c[3] for c in cands[:n]]
    for c in chosen:
        taken.add(c["_key"])
    return chosen, len(cands)


def main(argv):
    if len(argv) < 2:
        print("사용법: python tools/sample_panel.py <팩이름> <유닛코드>")
        print("  예:   python tools/sample_panel.py mypack AB-01")
        return 2
    pack, unit = argv[0], argv[1]

    cfg_path = Path("packs") / pack / "persona.yaml"
    if not cfg_path.is_file():
        print("조건 파일이 없습니다: %s" % cfg_path)
        return 1
    cfg = yaml.safe_load(io.open(cfg_path, encoding="utf-8"))

    release = Path(cfg["source"]["local"])
    field_ids, index_of, labels = load_codec_tables(release)

    used = collect_conditions(cfg)
    validate(used, index_of, labels)
    print("조건 검증 통과 — 차원 %d개, 값 %d개" % (len(used), sum(len(v) for v in used.values())))

    want = sorted(set(list(used) + PROFILE_DIMS) & set(index_of))
    rows = []
    for rel in cfg["source"]["shards"]:
        path = release / rel
        if not path.is_file():
            print("조각이 없습니다: %s" % path)
            return 1
        srcs, ids, pops, vals = read_shard(path, want, index_of, labels)
        print("  %s — %d명 읽음" % (rel, len(srcs)))
        for k in range(len(srcs)):
            r = {d: vals[d][k] for d in want}
            r["source"] = srcs[k]
            r["source_row_index"] = ids[k]
            r["populated"] = pops[k]
            r["_key"] = "%s:%s" % (srcs[k], ids[k])
            rows.append(r)

    seed = cfg.get("seed", 0)
    common = cfg.get("common") or {}
    prefer_sources = set(cfg["source"].get("prefer_sources") or [])
    taken, panel, shortfall = set(), [], []

    author_common = (cfg.get("author") or {}).get("common") or {}
    reviewer_common = (cfg.get("reviewer") or {}).get("common") or {}

    for seat in (cfg["author"] or {}).get("seats") or []:
        got, pool = pick(rows, [common, author_common, seat], 1,
                         seed, prefer_sources, taken)
        if not got:
            shortfall.append("저자 %s (%s) — 후보 0명" % (seat["id"], seat["view"]))
            continue
        p = got[0]
        panel.append({
            "role": "author", "seat": seat["id"], "view": seat["view"],
            "asks": seat.get("asks", ""),
            "persona": _card(p, want),
        })
        print("  저자 %s %-8s 후보 %d명 → 1명" % (seat["id"], seat["view"], pool))

    for st in (cfg["reviewer"] or {}).get("strata") or []:
        got, pool = pick(rows, [common, reviewer_common, st], st["n"],
                         seed, prefer_sources, taken)
        if len(got) < st["n"]:
            shortfall.append("연구진 %s — %d명 필요, %d명 확보(후보 %d명)"
                             % (st["id"], st["n"], len(got), pool))
        for p in got:
            panel.append({
                "role": "reviewer", "seat": st["id"], "desc": st.get("desc", ""),
                "persona": _card(p, want),
            })
        print("  연구진 %-5s 후보 %d명 → %d명" % (st["id"], pool, len(got)))

    # ★ 과목 폴더 아래 01번 칸. 폴더 하나가 책 한 권이다.
    #   폴더 이름에 작업순서 번호가 붙어 있으므로 코드로 찾는다(unitdir.find).
    from unitdir import ensure
    out_dir = ensure(pack, unit) / "01_선정"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("01_%s_패널.json" % unit)
    doc = {
        "_읽는법": {
            "무엇": "저자진 4명(쓰는 사람) + 연구진 12명(검토하는 사람) 명부. 같은 씨앗·같은 조각이면 같은 사람이 다시 나온다.",
            "재현": "python tools/sample_panel.py %s %s" % (pack, unit),
            "주의": "가상의 응답자다. 실제 수강생 데이터가 아니다.",
        },
        "pack": pack,
        "unit": unit,
        "seed": seed,
        "dataset": cfg["source"]["dataset"],
        "shards": cfg["source"]["shards"],
        "counts": {
            "author": sum(1 for x in panel if x["role"] == "author"),
            "reviewer": sum(1 for x in panel if x["role"] == "reviewer"),
            "total": len(panel),
        },
        "shortfall": shortfall,
        "panel": panel,
    }
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print()
    print("%s" % out)
    print("  저자진 %d명 · 연구진 %d명 · 합계 %d명"
          % (doc["counts"]["author"], doc["counts"]["reviewer"], doc["counts"]["total"]))
    if shortfall:
        print("  ★ 자리를 못 채운 곳:")
        for s in shortfall:
            print("     " + s)
        print("     → persona.yaml 의 must 를 줄이거나 조각을 더 받으십시오.")
    return 0


def _card(row, want):
    """명부에 적는 한 사람. 비어 있는 차원은 적지 않는다(없는 것과 모르는 것을 섞지 않는다)."""
    dims = {d: row[d] for d in want if row.get(d) is not None}
    return {
        "id": "%s:%s" % (row["source"], row["source_row_index"]),
        "source": row["source"],
        "grounding": ("사람 자료에 접지" if row["source"] != "synthetic" else "합성"),
        "gate": row.get("_gate"),          # 어느 차원으로 대상자 판정을 통과했는지
        "populated_dimensions": row.get("populated"),
        "dimensions": dims,
    }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
