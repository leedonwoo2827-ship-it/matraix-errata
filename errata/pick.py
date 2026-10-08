"""이 PC 의 폴더 고르기 창 — 웹앱 「고르기」 가 부른다. (longform/pick.py 와 같은 방식)

    python -m errata.pick   → stdout 마지막 줄에 {"folder": "..."} (취소면 빈 값)
    종료 코드 3 = 창을 띄울 수 없음(tkinter 없음) → 화면이 경로를 붙여 넣으라고 묻는다.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:  # noqa: BLE001
        return 3
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001
        return 3
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    folder = filedialog.askdirectory(parent=root, title="산물 폴더 고르기") or ""
    root.destroy()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"folder": folder.replace("/", "\\")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
