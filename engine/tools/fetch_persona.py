# -*- coding: utf-8 -*-
"""페르소나 데이터셋 내려받기. **이미 있는 것은 건너뛴다.**

무엇을 받는가
────────────────────────────────────────────────────────────────────────────
`packs/<pack>/persona.yaml` 의 `source.shards` 에 적힌 조각과 코드표만 받는다.
전체는 6.34GB 이고 우리가 쓰는 것은 그 일부다(현재 설정 = 약 374MB).

★ 전체를 받지 않는 이유. 조각 0000~0002 는 하나가 1GB 씩이고, 우리는 16명을
  뽑는다. 조각 0004+0005 만으로 StackOverflow 개발자 113,120명 전원이 들어온다
  (실측). 더 받아도 후보가 늘 뿐 표본은 씨앗이 정한다.

★ 합성 조각(0006~0009)은 받지 않는다. 1,290차원이 다 채워져 있어 걸러 내기는
  쉽지만, "실제 개발자 설문에 접지" 라는 말을 할 수 없게 된다. 어느 쪽을 쓰는지는
  `persona.yaml` 이 정하고 이 스크립트는 그것을 따른다.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

try:
    import yaml
    from huggingface_hub import hf_hub_download
except ImportError as e:
    sys.exit("의존성이 없습니다(%s):  pip install -r requirements.txt" % e.name)


def main(argv):
    from unitdir import resolve_pack
    pack = resolve_pack(argv, "tools/fetch_persona.py")
    cfg_path = Path("packs") / pack / "persona.yaml"
    if not cfg_path.is_file():
        print("조건 파일이 없습니다: %s" % cfg_path)
        return 1

    cfg = yaml.safe_load(io.open(cfg_path, encoding="utf-8"))
    src = cfg["source"]
    repo = src["dataset"]
    local = Path(src["local"])
    wanted = [src.get("codes_schema", "persona_codes.schema.json"),
              "manifest.json"] + list(src.get("shards") or [])

    print("데이터셋: %s" % repo)
    print("받는 곳 : %s" % local)
    total_new = 0
    for rel in wanted:
        dest = local / rel
        if dest.is_file():
            print("  건너뜀 (이미 있음) %s  %.1f MB"
                  % (rel, dest.stat().st_size / 1048576))
            continue
        print("  받는 중 %s ..." % rel)
        try:
            hf_hub_download(repo, rel, repo_type="dataset", local_dir=str(local))
        except Exception as e:
            print("  ★ 실패: %s — %s: %s" % (rel, type(e).__name__, str(e)[:160]))
            print("     인터넷 연결을 확인하고 다시 돌리십시오. 받은 것은 남아 있습니다.")
            return 1
        got = dest.stat().st_size / 1048576 if dest.is_file() else 0
        print("     받음 %.1f MB" % got)
        total_new += 1

    size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
    print()
    print("새로 받은 파일 %d개 · 데이터셋 폴더 합계 %.0f MB" % (total_new, size / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
