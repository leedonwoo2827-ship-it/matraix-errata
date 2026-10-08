# -*- coding: utf-8 -*-
"""과목 폴더 찾기·번호 매기기 — 도구들이 함께 쓴다.

왜 이 파일이 있는가
────────────────────────────────────────────────────────────────────────────
폴더 이름이 `01_AB-01` 이다. 번호는 **작업 순서**이고 `AB-01` 은 **과목 식별자**다.
도구들은 `AB-01` 로 불리고 폴더는 번호가 붙어 있으니, 그 둘을 잇는 곳이 한 군데
있어야 한다. 도구마다 따로 찾으면 한 곳만 고치고 나머지를 잊는다.

★ 번호의 소유자는 `packs/<pack>/order.txt` 다. 폴더 이름이 아니다.
  줄을 옮기고 `renumber.py` 를 돌리면 폴더가 따라온다 — 그 반대는 없다.
"""
from __future__ import annotations

import io
from pathlib import Path


def load_order(pack: str) -> list[str]:
    """`order.txt` 의 과목 코드 순서. 없으면 빈 목록."""
    p = Path("packs") / pack / "order.txt"
    if not p.is_file():
        return []
    out = []
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def number_of(pack: str, unit: str) -> int | None:
    """작업 순서 번호(1부터). 순서 목록에 없으면 None."""
    order = load_order(pack)
    return order.index(unit) + 1 if unit in order else None


def dir_name(pack: str, unit: str) -> str:
    """이 과목이 가져야 할 폴더 이름. 순서에 없으면 번호 없이 코드만."""
    n = number_of(pack, unit)
    return ("%02d_%s" % (n, unit)) if n else unit


def find(pack: str, unit: str) -> Path | None:
    """지금 디스크에 있는 그 과목의 폴더. 번호가 붙었든 안 붙었든 찾는다.

    ★ 번호로 찾지 않는다. 번호는 바뀌고 코드는 안 바뀐다 —
      바뀌는 쪽을 열쇠로 쓰면 순서를 한 번 바꿀 때마다 도구가 전부 멈춘다.
    """
    base = Path("data") / pack
    if not base.is_dir():
        return None
    exact = base / unit
    if exact.is_dir():
        return exact
    hits = [d for d in base.iterdir()
            if d.is_dir() and d.name.endswith("_" + unit)]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def ensure(pack: str, unit: str) -> Path:
    """폴더를 찾고, 없으면 어떻게 만드는지 알려 주고 멈춘다."""
    d = find(pack, unit)
    if d is not None:
        return d
    raise SystemExit(
        "과목 폴더가 없습니다: data/%s/%s\n"
        "먼저 돌리십시오: python tools/new_unit.py %s %s"
        % (pack, dir_name(pack, unit), pack, unit))


def list_packs() -> list[str]:
    """`packs/` 에 있는 팩. `_` 로 시작하는 것(_template)은 팩이 아니다."""
    base = Path("packs")
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and not d.name.startswith("_")
                  and (d / "pack.yaml").is_file())


def resolve_pack(argv, tool: str) -> str:
    """인자에서 팩 이름을 정한다. 없으면 팩이 하나일 때만 그것을 쓴다.

    ★ 기본값으로 특정 팩 이름을 박지 않는다. 특정 팩 이름을 기본값으로 두면,
      간호학 팩만 있는 사람이 인자 없이 돌렸을 때 "그 팩이 없습니다" 라는
      쓸모없는 말을 듣는다. 팩이 여럿이면 **고르라고 말하는 것**이 맞다.
    """
    packs = list_packs()
    if argv:
        name = argv[0]
        if name in packs:
            return name
        raise SystemExit(
            "그런 팩이 없습니다: %s\n"
            "있는 팩: %s\n"
            "새로 만들기: python tools/new_pack.py <팩이름>"
            % (name, ", ".join(packs) if packs else "없음"))
    if len(packs) == 1:
        return packs[0]
    if not packs:
        raise SystemExit(
            "팩이 없습니다. 먼저 만드십시오: python tools/new_pack.py <팩이름>")
    raise SystemExit(
        "팩이 여럿입니다. 어느 것인지 적어 주십시오:\n"
        "  python %s <팩이름>\n"
        "있는 팩: %s" % (tool, ", ".join(packs)))
