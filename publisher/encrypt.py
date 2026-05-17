"""정적 HTML 비밀번호 보호.

PBKDF2(SHA-256, 250,000 iter) + AES-256-GCM. 클라이언트에서 SubtleCrypto API로
복호화. Node.js·npm 의존성 없음.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITER = 250_000
SALT_LEN = 16
IV_LEN = 12
MIN_PASSWORD_LEN = 12
CACHE_TTL_MS = 5 * 60 * 1000  # 5분


def validate_password_strength(password: str) -> None:
    """약한 비번을 빌드 시점에 거부. brute force 방어용."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"STATICRYPT_PASSWORD는 최소 {MIN_PASSWORD_LEN}자 이상이어야 합니다 "
            f"(현재 {len(password)}자)."
        )


def encrypt_json_bytes(data: object, password: str) -> bytes:
    """임의 JSON-serializable 객체를 AES-256-GCM으로 암호화. salt|iv|ct 바이트 반환."""
    plain = json.dumps(data, ensure_ascii=False).encode("utf-8")
    salt = secrets.token_bytes(SALT_LEN)
    iv = secrets.token_bytes(IV_LEN)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(iv, plain, None)
    return salt + iv + ct


def decrypt_json_bytes(blob: bytes, password: str) -> object:
    """encrypt_json_bytes의 역. 실패 시 예외."""
    salt = blob[:SALT_LEN]
    iv = blob[SALT_LEN:SALT_LEN + IV_LEN]
    ct = blob[SALT_LEN + IV_LEN:]
    key = _derive_key(password, salt)
    plain = AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
    return json.loads(plain)

LOCK_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';">
<meta http-equiv="X-Content-Type-Options" content="nosniff">
<meta name="referrer" content="no-referrer">
<title>{title}</title>
<style>
  body {{ font-family: 'Segoe UI', 'Apple SD Gothic Neo', sans-serif;
    background: #0f172a; color: #e2e8f0; margin: 0; padding: 0;
    display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
  .lock {{ background: #1e293b; border-radius: 14px; padding: 36px 32px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4); max-width: 380px; width: 90%; }}
  .lock h1 {{ margin: 0 0 6px; font-size: 18px; }}
  .lock p {{ font-size: 13px; color: #94a3b8; margin: 0 0 18px; }}
  .lock input {{ width: 100%; padding: 12px 14px; font-size: 14px; border-radius: 8px;
    border: 1px solid #334155; background: #0f172a; color: #e2e8f0;
    margin-bottom: 12px; box-sizing: border-box; }}
  .lock button {{ width: 100%; padding: 12px; font-size: 14px; font-weight: 600;
    background: #60a5fa; color: #0f172a; border: none; border-radius: 8px; cursor: pointer; }}
  .lock button:hover {{ background: #93c5fd; }}
  .err {{ color: #f87171; font-size: 12px; margin-top: 10px; min-height: 16px; }}
  .lock .footer {{ font-size: 11px; color: #64748b; text-align: center; margin-top: 16px; }}
</style></head><body>
<div id="lock" class="lock">
  <h1>🔒 Portfolio Risk Advisor</h1>
  <p>비밀번호를 입력하세요.</p>
  <form id="form">
    <input type="password" id="pw" autocomplete="current-password" autofocus>
    <button type="submit">잠금 해제</button>
    <div class="err" id="err"></div>
  </form>
  <div class="footer">개인 자산 정보 — 무단 접근 금지</div>
</div>
<script>
const ENC = "{encrypted_b64}";
const PW_KEY = "pw_v2";
const TTL_MS = {ttl_ms};

function b64ToBuf(s) {{
  const bin = atob(s);
  const u8 = new Uint8Array(bin.length);
  for (let i=0; i<bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8.buffer;
}}

async function deriveKey(password, salt) {{
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    {{ name: "PBKDF2", salt: salt, iterations: {iterations}, hash: "SHA-256" }},
    baseKey,
    {{ name: "AES-GCM", length: 256 }},
    false,
    ["decrypt"]
  );
}}

// 비번 캐시 — 5분 TTL, 만료 시 자동 삭제
function cacheSet(pw) {{
  try {{ sessionStorage.setItem(PW_KEY, JSON.stringify({{p: pw, t: Date.now()}})); }} catch(e) {{}}
}}
function cacheGet() {{
  try {{
    const raw = sessionStorage.getItem(PW_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw);
    if (!o || typeof o.p !== "string" || typeof o.t !== "number") return null;
    if (Date.now() - o.t > TTL_MS) {{
      sessionStorage.removeItem(PW_KEY);
      return null;
    }}
    return o.p;
  }} catch(e) {{ return null; }}
}}
function cacheClear() {{ try {{ sessionStorage.removeItem(PW_KEY); }} catch(e) {{}} }}

// 페이지 가시성 잃을 때 비번 즉시 삭제 (탭 전환·창 최소화 시 보안 강화)
document.addEventListener("visibilitychange", () => {{
  if (document.visibilityState === "hidden") cacheClear();
}});

async function decryptAndShow(password) {{
  try {{
    const data = new Uint8Array(b64ToBuf(ENC));
    const salt = data.slice(0, {salt_len});
    const iv = data.slice({salt_len}, {salt_len} + {iv_len});
    const ct = data.slice({salt_len} + {iv_len});
    const key = await deriveKey(password, salt);
    const ptBuf = await crypto.subtle.decrypt({{ name: "AES-GCM", iv: iv }}, key, ct);
    const html = new TextDecoder().decode(ptBuf);

    cacheSet(password);

    document.open();
    document.write(html);
    document.close();
  }} catch(e) {{
    cacheClear();
    document.getElementById("err").textContent = "비밀번호가 올바르지 않습니다.";
  }}
}}

document.getElementById("form").addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  document.getElementById("err").textContent = "";
  const pw = document.getElementById("pw").value;
  if (!pw) return;
  await decryptAndShow(pw);
}});

// 페이지 로드 시 유효한 캐시가 있으면 자동 시도
(async () => {{
  const cached = cacheGet();
  if (cached) await decryptAndShow(cached);
}})();
</script>
</body></html>
"""


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITER,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_html(html: str, password: str, title: str = "🔒 Portfolio Risk Advisor") -> str:
    """HTML을 password로 암호화하고 lock 페이지(스스로 복호화하는 HTML)를 반환."""
    salt = secrets.token_bytes(SALT_LEN)
    iv = secrets.token_bytes(IV_LEN)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(iv, html.encode("utf-8"), None)
    payload = base64.b64encode(salt + iv + ct).decode("ascii")
    return LOCK_HTML_TEMPLATE.format(
        title=title,
        encrypted_b64=payload,
        iterations=PBKDF2_ITER,
        salt_len=SALT_LEN,
        iv_len=IV_LEN,
        ttl_ms=CACHE_TTL_MS,
    )


def encrypt_file(in_path: Path, out_path: Path, password: str) -> None:
    plain = in_path.read_text(encoding="utf-8")
    locked = encrypt_html(plain, password)
    out_path.write_text(locked, encoding="utf-8")


def encrypt_dist(dist_dir: Path, password: str) -> int:
    """dist/ 안의 모든 *.html을 in-place로 암호화. history.json은 그대로 둠."""
    count = 0
    for html_path in dist_dir.rglob("*.html"):
        # 이미 lock 페이지인지 헤더로 감지
        try:
            head = html_path.read_text(encoding="utf-8")[:200]
        except Exception:
            continue
        if "id=\"lock\"" in head and "ENC =" in html_path.read_text(encoding="utf-8")[:2000]:
            continue
        encrypt_file(html_path, html_path, password)
        count += 1
    return count
