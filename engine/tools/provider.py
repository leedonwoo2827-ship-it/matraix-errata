# -*- coding: utf-8 -*-
"""집필 프로바이더 — Claude Code **구독 OAuth**. API 키를 쓰지 않는다.

★ 출처: 같은 팀의 다른 프로젝트에 있던 같은 이름의 파일(2026-08-10 판).
  `find_cli` · `scrubbed_env` · `_classify_error` 는 **동작을 바꾸지 않고** 옮겼다.
  두 프로젝트가 같은 PC·같은 구독을 쓰므로 여기가 갈리면 같은 증상에 서로 다른
  안내가 나간다.

왜 API 키를 쓰지 않는가
────────────────────────────────────────────────────────────────────────────
키를 하나 두고 공유하면 누가 얼마를 썼는지 가를 수 없고, 유출되면 전원이 멈춘다.
각자의 로그인을 그대로 빌려 쓰면 사용량·한도가 자연히 갈린다.

한도 규약
────────────────────────────────────────────────────────────────────────────
★ 비용은 **기록·분류용**이다. 중단 조건으로 쓰지 않는다.
  쓰이는 곳은 하나 — 실패 원인 판별: 비용이 0 인 실패는 모델을 못 부른 것이고,
  그것은 프롬프트 문제가 아니라 한도/인증 문제다.
★ 한 번에 한 단계만 부른다. 같은 구독을 다른 창이 함께 쓰고 있다고 전제한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class ProviderError(RuntimeError):
    """호출이 실패했다. 다시 해도 같은 결과일 가능성이 높다."""


class NotAuthenticated(ProviderError):
    """Claude Code 로그인이 없거나 만료됐다.

    ★ 도구가 해결할 수 없다. API 키를 받아 우회하지 **않는다** —
      각자의 구독으로 사용량을 가르는 것이 이 방식의 목적이다.
    """


class QuotaExceeded(ProviderError):
    """구독 사용량 한도에 걸렸다. 시간이 지나면 풀린다. 재시도가 의미 있다."""


def find_cli() -> Path | None:
    """`claude` 실행 파일. **VSCode 확장에 번들된 것까지** 찾는다.

    ★ 마지막 경로가 없으면 안 된다. 이 PC 에는 `claude` 가 PATH 에도, npm 전역에도
      없다 — 실행 파일은 VSCode 확장 안에만 있다(실측 2026-09-03:
      `anthropic.claude-code-2.1.251-win32-x64/resources/native-binary/claude.exe`).
    """
    env = (os.environ.get("CLAUDE_CLI") or "").strip().strip('"')
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    w = shutil.which("claude")
    if w:
        return Path(w)
    for pat in ("anthropic.claude-code-*/resources/native-binary/claude.exe",
                "anthropic.claude-code-*/resources/native-binary/claude"):
        hits = sorted((Path.home() / ".vscode" / "extensions").glob(pat))
        if hits:
            return hits[-1]      # 확장 버전이 올라가면 경로가 바뀐다 → 최신
    return None


def scrubbed_env() -> dict:
    """낡은 export 가 OAuth 를 조용히 가로채 **다른 계정에 과금**하는 것을 막는다.

    ★ 빈 문자열로 **덮어야** 한다. 지우는 것이 아니라 빈 값을 자식 환경에 넣어
      무력화하는 것이다 — 삭제만 하면 부모 환경의 값이 그대로 상속된다.
    ★ 이것이 없으면 증상이 최악이다: 집필은 정상 동작하고 요금만 엉뚱한 곳에 찍힌다.
      몇 달 뒤 청구서를 보기 전까지 아무도 모른다.
    """
    e = dict(os.environ)
    e.update({"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",
              "ANTHROPIC_BASE_URL": ""})
    return e


def _classify(msg: str) -> ProviderError:
    """문자열 매칭으로 타입화 — 같은 팀의 다른 프로젝트와 같은 규칙."""
    low = (msg or "").lower()
    if any(k in low for k in ("not authenticated", "sign in", "401",
                              "unauthorized", "login")):
        return NotAuthenticated(msg)
    if any(k in low for k in ("quota", "rate limit", "429", "usage limit",
                              "overloaded")):
        return QuotaExceeded(msg)
    return ProviderError(msg)


def ask(prompt: str, *, cwd: str = ".", timeout: int = 1800,
        allowed_tools: str = "Read,Glob,Grep") -> str:
    """프롬프트 하나를 보내고 글을 받는다. 파일은 이 함수가 쓰지 않는다.

    ★ 기본 허용 도구를 **읽기만**으로 둔다(Read·Glob·Grep). 쓰기는 부른 쪽이
      받은 글을 파일로 저장한다 — 그러면 무엇이 저장됐는지가 코드에 보인다.
      모델에게 Write 를 주면 어느 파일이 언제 바뀌었는지 추적이 흐려진다.

    ★ `--bare` 를 쓰지 않는다. 프롬프트를 우리가 보낸 것만으로 두고 싶어서 처음에
      붙였는데, **키체인 읽기를 건너뛰어 로그인이 풀린다**(실측 2026-09-03:
      `Not logged in · Please run /login`, 종료 코드 1). 도움말에 "skip … keychain
      reads" 라고 적혀 있고 인증이 거기 있다. 재현 가능성은 프롬프트를 파일로
      남기는 것으로 확보한다 — 인증을 끊어서 얻을 것이 아니다.
    """
    cli = find_cli()
    if cli is None:
        raise NotAuthenticated(
            "claude 실행 파일을 찾지 못했습니다.\n"
            "  VSCode 의 Claude Code 확장을 깔거나, 경로를 알려 주십시오:\n"
            "    set CLAUDE_CLI=C:\\경로\\claude.exe")

    # ★ 프롬프트를 **표준입력으로** 넘긴다. 인자로 붙이면 안 된다 —
    #   `--allowedTools` 가 가변 인자(<tools...>)라서 뒤에 오는 프롬프트를 삼킨다
    #   (실측 2026-09-03: "Input must be provided either through stdin or as a
    #   prompt argument" 가 그 증상이다). 프롬프트가 길어지면 명령줄 길이 제한에도
    #   걸리므로 표준입력이 두 번 맞다.
    cmd = [str(cli), "-p", "--allowedTools", allowed_tools]
    try:
        r = subprocess.run(cmd, cwd=cwd, env=scrubbed_env(), timeout=timeout,
                           input=prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise ProviderError("%d초 안에 끝나지 않았습니다. 단계를 더 잘게 나누십시오."
                            % timeout)

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        raise _classify(err or out or "종료 코드 %d" % r.returncode)
    if not out:
        # ★ 빈 응답은 인증·한도 쪽이 많다. "모델이 답을 안 했다" 로 넘기지 않는다.
        raise _classify(err or "빈 응답을 받았습니다 (한도나 인증 문제일 수 있습니다)")
    return out


def strip_fence(text: str) -> str:
    """모델이 답 전체를 ``` 으로 감쌌으면 벗긴다.

    ★ 벗기지 않으면 md 파일 첫 줄이 ```markdown 이 되고, HTML 로 뽑을 때
      문서 전체가 코드 블록 하나로 나온다.
    """
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if len(lines) < 3:
        return t
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


if __name__ == "__main__":
    cli = find_cli()
    print("claude 실행 파일: %s" % (cli or "찾지 못했습니다"))
    if cli and "--ask" in sys.argv:
        print(ask("한 문장으로 답하십시오: 준비됐습니까?"))
