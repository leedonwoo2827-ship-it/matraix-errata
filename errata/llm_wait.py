"""(longform/llm_wait.py 에서 옮김 — probe 만 엔진 provider 로 바꿨다)
Claude 사용 한도(5시간 창·주간 한도 등)에 걸리면 **기다렸다가 이어서** 한다 — 퇴근 후 무인 제작용.

    is_limit(text)       응답·오류가 한도 메시지인가
    reset_at(text)       메시지에서 재설정 시각(epoch) 읽기 — 「limit reached|1759…」, 「resets 3pm」, 「reset at 15:30」
    wait_for_claude(job) 짧은 확인 질문을 던져 한도가 풀릴 때까지 기다린다
    keep_awake(on)       기다리는 동안 Windows 가 절전에 들어가지 않게 한다

설정(job.json llm.*, 웹앱 「설정」 탭)
    limit_wait_hours  한도에 걸리면 최대 기다릴 시간(시간, 기본 24, 0 = 바로 멈춤)
    limit_poll_min    재설정 시각을 모를 때 다시 확인하는 간격(분, 기본 30 — 60 이면 1시간마다)

★ 재설정 시각을 알아도 한 번에 최대 60분씩만 자고 확인한다 — 주간 한도가 일찍 풀리는 경우를 잡는다.
★ 로그 문구 「⏸ …」 로 시작하는 줄은 웹앱 로그 막대가 「한도 대기」 로 보여 준다(index.html).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import time
from typing import Optional

from errata.util import log, warn

LIMIT_RE = re.compile(r"usage limit|limit reached|hour limit|weekly limit|hit your limit|rate limit|rate_limit|"
                      r"overloaded|too many requests|resets? (at )?\d|\b429\b|\b529\b|한도", re.I)
CHUNK_MIN = 60          # 재설정 시각을 알아도 이만큼씩 끊어 자고 확인
AFTER_RESET_SEC = 90    # 재설정 시각 뒤 여유


def is_limit(text: str) -> bool:
    return bool(LIMIT_RE.search(text or ""))


def reset_at(text: str) -> Optional[float]:
    t = text or ""
    m = re.search(r"\|(\d{10})\b", t)                        # 「Claude AI usage limit reached|1759380000」
    if m:
        return float(m.group(1))
    m = re.search(r"reset[s]?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t, re.I)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        now = dt.datetime.now()
        r = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if r <= now:
            r += dt.timedelta(days=1)
        return r.timestamp()
    return None


_AWAKE = 0


def keep_awake(on: bool = True) -> None:
    """Windows 절전 막기(ES_CONTINUOUS | ES_SYSTEM_REQUIRED). 다른 OS 에서는 아무것도 안 한다.
    ★ 겹쳐 부를 수 있게 세어 둔다 — 검토 전체가 켜 둔 것을 한도 대기의 finally 가 꺼 버리면
      밤사이 PC 가 잠든다(검수엔진에서 덧붙임)."""
    global _AWAKE
    _AWAKE = _AWAKE + 1 if on else max(0, _AWAKE - 1)
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if _AWAKE > 0 else 0))
    except Exception:  # noqa: BLE001
        pass


def _probe(job) -> str:
    """한도가 풀렸으면 "" — 아니면 그 메시지."""
    from errata._engine import find_cli, scrubbed_env
    cli = find_cli()
    if cli is None:
        return "claude 실행 파일을 찾지 못했습니다"
    try:
        p = subprocess.run([str(cli), "-p", "--output-format", "text"], input="ok 라고만 답하십시오.",
                           text=True, encoding="utf-8", errors="replace", capture_output=True,
                           env=scrubbed_env(), timeout=180)
        out = (p.stdout or "") + (p.stderr or "")
        return "" if (p.returncode == 0 and not is_limit(out)) else (out or f"exit {p.returncode}")
    except Exception as e:  # noqa: BLE001
        return str(e)


def _settings(job):
    try:
        hours = float(job.get("llm.limit_wait_hours", 24))
    except (TypeError, ValueError):
        hours = 24.0
    try:
        poll = max(5.0, float(job.get("llm.limit_poll_min", 30)))
    except (TypeError, ValueError):
        poll = 30.0
    return hours, poll


def wait_for_claude(job, first_text: str = "", probe=None, sleep=time.sleep) -> bool:
    """한도가 풀릴 때까지 기다린다. 풀리면 True, 최대 시간을 넘기거나 0 으로 꺼 두었으면 False.
    probe·sleep 은 시험용으로 바꿔 끼울 수 있다."""
    probe = probe or (lambda: _probe(job))
    max_h, poll = _settings(job)
    text = first_text or probe()
    if not text:
        return True
    if max_h <= 0:
        warn("Claude 사용 한도 — 기다리지 않도록 설정돼 있어 멈춥니다(job.json llm.limit_wait_hours)")
        return False
    deadline = time.time() + max_h * 3600
    keep_awake(True)
    try:
        while time.time() < deadline:
            ts = reset_at(text)
            now = time.time()
            if ts and ts > now:
                wake = min(ts + AFTER_RESET_SEC, now + CHUNK_MIN * 60, deadline)
                log(f"⏸ Claude 사용 한도 — {dt.datetime.fromtimestamp(ts):%m월 %d일 %H:%M} 재설정 예정"
                    f"(약 {(ts - now) / 60:.0f}분). {dt.datetime.fromtimestamp(wake):%H:%M} 에 다시 확인해요. 창은 켜 두세요.")
            else:
                wake = min(now + poll * 60, deadline)
                log(f"⏸ Claude 사용 한도(또는 과부하) — {poll:.0f}분 뒤 {dt.datetime.fromtimestamp(wake):%H:%M} 에"
                    f" 다시 확인해요. 창은 켜 두세요.")
            sleep(max(1.0, wake - time.time()))
            text = probe()
            if not text:
                log("▶ Claude 한도가 풀렸어요 — 이어서 해요")
                return True
        warn(f"✗ Claude 한도가 {max_h:g}시간 안에 풀리지 않았어요 — 같은 버튼을 다시 누르면 남은 것만 이어서 해요")
        return False
    finally:
        keep_awake(False)
