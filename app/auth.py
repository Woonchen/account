"""密碼雜湊、session cookie、輸錯封鎖（做法同 finflow deploy/serve.py）。

- 密碼只存 pbkdf2_sha256 雜湊：pbkdf2_sha256:次數:鹽(hex):雜湊(hex)
- cookie 連同該帳號的密碼雜湊一起簽：改密碼或刪帳號，舊的登入就失效；換 ACCOUNT_SESSION_SECRET＝所有人登出
- 同一個 IP 在 FAIL_WINDOW 秒內輸錯 FAIL_MAX 次，封鎖 BLOCK_HOURS 小時（記在記憶體，重啟容器就解除）
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

ITERATIONS = 300_000
SESSION_DAYS = 7
FAIL_MAX, FAIL_WINDOW = 3, 30
BLOCK_HOURS = 24
COOKIE = "account_session"


def secret():
    s = os.environ.get("ACCOUNT_SESSION_SECRET", "")
    if not s:
        raise RuntimeError("ACCOUNT_SESSION_SECRET 沒有設定")
    return s.encode()


# ---------- 密碼 ----------
def make_hash(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt.hex()}:{dk.hex()}"


def check_password(password, stored):
    try:
        algo, n, salt, want = stored.split(":")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(n))
        return hmac.compare_digest(dk.hex(), want)
    except (AttributeError, ValueError):
        return False


# 帳號不存在時也算一次雜湊，回應時間才不會洩漏帳號是否存在
DUMMY_HASH = make_hash(secrets.token_hex(8), 1000).replace(":1000:", f":{ITERATIONS}:")


# ---------- session cookie ----------
def _sign(msg, fp):
    return hmac.new(secret(), f"{msg}|{fp}".encode(), hashlib.sha256).hexdigest()


def make_session(username, pass_hash, now=None):
    exp = int((time.time() if now is None else now) + SESSION_DAYS * 86400)
    msg = f"{base64.urlsafe_b64encode(username.encode()).decode()}.{exp}"
    return f"{msg}.{_sign(msg, pass_hash)}"


def session_user(value):
    """cookie 裡的帳號名稱（還沒驗證簽章）；格式不對回傳 None。"""
    try:
        u, _exp, _sig = value.split(".")
        return base64.urlsafe_b64decode(u.encode()).decode()
    except (AttributeError, ValueError, UnicodeDecodeError):
        return None


def valid_session(value, pass_hash, now=None):
    try:
        u, exp, sig = value.split(".")
    except (AttributeError, ValueError):
        return False
    msg = f"{u}.{exp}"
    now = time.time() if now is None else now
    return hmac.compare_digest(sig, _sign(msg, pass_hash)) and exp.isdigit() and int(exp) > now


# ---------- 輸錯封鎖 ----------
class Guard:
    def __init__(self):
        self.lock = threading.Lock()
        self.fails, self.blocked = {}, {}

    def is_blocked(self, ip, now=None):
        now = time.time() if now is None else now
        with self.lock:
            until = self.blocked.get(ip)
            if until and until <= now:
                del self.blocked[ip]
                until = None
            return until

    def fail(self, ip, now=None):
        """記一次輸錯；回傳這次是否因此被封鎖。"""
        now = time.time() if now is None else now
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW] + [now]
            if len(recent) >= FAIL_MAX:
                self.fails.pop(ip, None)
                self.blocked[ip] = now + BLOCK_HOURS * 3600
                return True
            self.fails[ip] = recent
            if len(self.fails) > 10000:  # 不讓記錄無限長大
                self.fails = {k: v for k, v in self.fails.items() if now - v[-1] < FAIL_WINDOW}
            return False

    def ok(self, ip):
        with self.lock:
            self.fails.pop(ip, None)
