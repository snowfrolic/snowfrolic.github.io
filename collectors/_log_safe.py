"""로그 안전 헬퍼 — API 키 자동 마스킹.

외부 API 호출 시 예외 메시지나 URL에 API 키가 포함되어 평문 로그에 노출되는 것을 방지.

사용 패턴:
    from collectors._log_safe import safe_error_msg
    try:
        ...
    except Exception as exc:
        log.warning(f"FRED 실패: {safe_error_msg(exc)}")
"""
from __future__ import annotations

import re

# 마스킹 대상 패턴
# 1) api_key=XXX 형태 (FRED 등 query param)
# 2) /StatisticSearch/XXX/json 형태 (ECOS path)
# 3) Bearer/AppKey 헤더는 logger에 직접 안 들어가므로 제외
_PATTERNS = [
    # query param: api_key=, key=, apikey=, access_token=
    (re.compile(r"(api[_-]?key|access[_-]?token|appkey|appsecret)=[^&\s]+", re.IGNORECASE),
     r"\1=***"),
    # ECOS path: /api/StatisticSearch/{key}/json/...
    (re.compile(r"(/StatisticSearch/)[^/\s]+(/)"),
     r"\1***\2"),
    # KIS path 패턴 (혹시 모를 노출)
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_.\-]+"),
     r"\1***"),
]


def mask_secrets(text: str) -> str:
    """문자열에서 알려진 API 키 패턴을 *** 로 치환."""
    if not text:
        return text
    out = str(text)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def safe_error_msg(exc: BaseException) -> str:
    """예외 객체의 메시지를 마스킹해서 반환."""
    return mask_secrets(str(exc))


def safe_url(url: str | None) -> str:
    """안전한 URL만 통과 — http/https 스키마만 허용.

    javascript:, data:, file:, vbscript: 등 위험 스키마 차단 → XSS 방어.
    """
    if not url:
        return ""
    url = str(url).strip()
    if not url:
        return ""
    # 소문자 변환 후 검사
    lower = url.lower()
    if lower.startswith(("https://", "http://")):
        return url
    return ""
