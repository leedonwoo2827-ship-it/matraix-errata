"""공통 도구 — 로그·JSON·정규화·시각 표기."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def warn(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + "! " + msg, flush=True)


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def write_json(p: Path, data: Any) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def read_jsonl(p: Path) -> List[dict]:
    p = Path(p)
    if not p.exists():
        return []
    out = []
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(p: Path, rows: Iterable[dict]) -> int:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = p.with_suffix(p.suffix + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(p)
    return n


# ★ 진짜 태그만 — 「x <- c(4, TRUE)」 같은 R 코드의 < 를 태그로 잡아 뒤의 > 까지 지우면 안 된다(실측)
TAG = re.compile(r"</?[A-Za-z!][^<>]*>")


def norm(s: str) -> str:
    """대조용 정규화 — 공백·구두점 흔들림을 지운다(의미는 건드리지 않는다)."""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(TAG, " ", s)
    s = s.replace("·", "").replace("ㆍ", "")
    s = re.sub(r"[\s\"'“”‘’「」『』《》〈〉()\[\],.;:!?…~\-–—*`#|>]+", "", s)
    return s


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|li|tr|h\d|div)>", "\n", s, flags=re.I)
    s = re.sub(TAG, " ", s)
    import html
    s = html.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec or 0)))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def short_hash(*parts: str, n: int = 10) -> str:
    h = hashlib.sha1("\u241f".join(p or "" for p in parts).encode("utf-8")).hexdigest()
    return h[:n]


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
