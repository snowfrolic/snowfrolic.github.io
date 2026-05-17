"""점수 히스토리 관리 — data/history.enc (AES-256-GCM 암호화).

GitHub Pages root에 평문 history.json을 절대 노출하지 않기 위해 항상
암호화된 파일로 보관. 빌드 시 복호화 → 갱신 → 재암호화.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from publisher.encrypt import decrypt_json_bytes, encrypt_json_bytes


def load_history(history_path: Path, password: str) -> list[dict]:
    """암호화 파일에서 history 복원. 없거나 복호화 실패 시 빈 리스트."""
    if not history_path.exists():
        return []
    try:
        blob = history_path.read_bytes()
        data = decrypt_json_bytes(blob, password)
        if isinstance(data, list):
            return data
    except Exception:
        # 비번 변경·파일 손상 시 — history 초기화
        pass
    return []


def update_history(
    history_path: Path,
    today_score: float,
    today_action: str,
    total_value: float,
    password: str,
) -> list[dict]:
    """오늘 점수를 추가하고 시계열 반환. 같은 날짜는 덮어쓰기.

    파일은 password로 암호화해 저장. 평문 JSON은 디스크에 남지 않음.
    """
    history_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_history(history_path, password)

    today = datetime.now().strftime("%Y-%m-%d")
    data = [d for d in data if d.get("date") != today]
    data.append({
        "date": today,
        "score": round(today_score, 2),
        "action": today_action,
        "value": int(total_value),
    })
    data.sort(key=lambda d: d["date"])
    data = data[-365:]  # 최대 1년치

    blob = encrypt_json_bytes(data, password)
    history_path.write_bytes(blob)
    return data


def get_archive_links(dist_dir: Path, limit: int = 30) -> list[dict]:
    """archive/*.html 파일 목록 (최신부터). 파일명 = YYYY-MM-DD."""
    archive_dir = dist_dir / "archive"
    if not archive_dir.exists():
        return []
    files = sorted(archive_dir.glob("*.html"), reverse=True)[:limit]
    return [{"date": f.stem, "url": f"archive/{f.name}"} for f in files]
