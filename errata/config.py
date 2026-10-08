"""잡(job) 설정 — data/jobs/<잡>/job.json(잡 설정) + job.local.json(이 PC 경로). data/ 는 깃에 올리지 않는다.

job.local.json 의 sources 네 칸이 산물 폴더다. 모두 **읽기만** 한다.
    book      문항 앱의 책 폴더  → Q(_rounds·02) · V(05·_videos) · S(03 요약) · D 문제덱(04-2 pptx)
    longform  롱폼 잡 폴더       → L(manuscripts·out_wb) · D 요약강의(bp 덱)
    textbook  교재 폴더          → B(*.md · *.pdf · *.hwpx)
    shorts    쇼츠 산물 폴더     → T(<pack.yaml shorts_glob>/q*/...)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from errata.util import read_json, write_json

REPO = Path(__file__).resolve().parents[1]
# ★ 데이터는 전부 data/ 한 곳에(깃 제외) — 잡·산출물·분야 팩·엔진 작업 폴더·로그. 저장소에는 도구와 pack/_template 만.
DATA = Path(os.environ.get("ERRATA_DATA") or REPO / "data")
JOBS = DATA / "jobs"
PACKS = DATA / "pack"
TEMPLATE = REPO / "pack" / "_template"
SOURCE_KEYS = ["book", "longform", "textbook", "shorts"]
SOURCE_LABELS = {
    "book": "문항 책 폴더 (문항·문제풀이 영상·요약노트·문제덱)",
    "longform": "롱폼 잡 폴더 (화이트보드 20강·요약 과목별 강의)",
    "textbook": "과목별 교재 폴더 (md·pdf·hwpx)",
    "shorts": "용어 쇼츠 산물 폴더",
}


class Job:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dir = JOBS / name
        if not (self.dir / "job.json").exists():
            raise SystemExit(f"잡이 없습니다: {self.dir / 'job.json'} (CLAUDE.md 「처음 시작」)")
        self.cfg: Dict[str, Any] = read_json(self.dir / "job.json", {}) or {}
        self.local: Dict[str, Any] = read_json(self.dir / "job.local.json", {}) or {}

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.cfg
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def sources(self) -> Dict[str, str]:
        s = dict(self.local.get("sources") or {})
        return {k: s.get(k, "") for k in SOURCE_KEYS}

    def src(self, key: str) -> Path | None:
        v = (self.sources.get(key) or "").strip().strip('"')
        if not v:
            return None
        p = Path(v)
        return p if p.exists() else None

    def set_sources(self, src: Dict[str, str]) -> None:
        self.local["sources"] = {k: (src.get(k) or "").strip().strip('"') for k in SOURCE_KEYS}
        write_json(self.dir / "job.local.json", self.local)

    @property
    def pack_dir(self) -> Path:
        return PACKS / (self.cfg.get("pack") or self.name)

    def pack(self) -> Dict[str, Any]:
        import yaml
        return yaml.safe_load((self.pack_dir / "pack.yaml").read_text(encoding="utf-8")) or {}

    def p(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)


def default_job() -> str:
    names = sorted(d.name for d in JOBS.iterdir() if (d / "job.json").exists()) if JOBS.exists() else []
    if not names:
        raise SystemExit(f"잡이 하나도 없습니다 — {JOBS}\\<이름>\\job.json 을 만드십시오 (CLAUDE.md 「처음 시작」)")
    return names[0]


def load_job(name: str = "") -> Job:
    return Job(name or default_job())
