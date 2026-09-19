"""workspace_mailer: lớp gửi email dùng chung của HangOS qua Google Workspace.

BẢN GỐC: cohangai/shared/workspace_mailer.py. Mỗi service giữ một bản sao Y HỆT
(Railway build từng thư mục service riêng nên không import chéo được). Kiểm bản sao
bằng: python3 cohangai/shared/check_vendored_mailer.py

Vì sao có file này (2026-09-19): bỏ hẳn Brevo, Google Workspace
(support@daothihang.com) là kênh gửi. Trước đây mỗi service tự viết smtplib/Brevo
riêng, không chống gửi trùng, không retry, không giữ hạn mức cho mã đăng nhập.

Cách gửi: smtp.gmail.com + app password (Google hỗ trợ chính thức cho ứng dụng,
hạn mức 2.000 thư/ngày/tài khoản, 3.000 người nhận khác nhau/ngày, trong đó
2.000 ngoài tổ chức). Nguồn: knowledge.workspace.google.com, "Gmail sending
limits in Google Workspace" và "Send email from a printer, scanner, or app".

Bảo đảm:
- Ưu tiên: otp > transactional > alert > marketing. Marketing không được dùng
  phần hạn mức giữ lại (HANGOS_MAIL_PRIORITY_RESERVE) cho mã đăng nhập/giao dịch.
- Chống gửi trùng bằng idempotency_key (lưu dạng băm, không lưu nội dung).
- Timeout, retry có giới hạn chỉ cho lỗi tạm thời (4xx, rớt kết nối, timeout).
  535 (sai/thu hồi app password) KHÔNG retry. Hết hạn mức Google (5.4.5) thì
  ngừng gửi 24 giờ thay vì đập tiếp.
- Log không bao giờ chứa nội dung, tiêu đề, mật khẩu; địa chỉ nhận bị che.
- Marketing bắt buộc có link hủy đăng ký (List-Unsubscribe, RFC 8058) và kiểm
  suppression do caller cung cấp (nguồn chuẩn là CDP, không phải Google).

Chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import hashlib
import logging
import os
import smtplib
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Callable, Optional

VERSION = "2026-09-19.1"

PRIORITIES = ("otp", "transactional", "alert", "marketing")

# Trạng thái trả về (SendResult.status)
SENT = "sent"
DUPLICATE = "duplicate"
SUPPRESSED = "suppressed"
QUOTA_RESERVED = "quota_reserved"      # marketing chạm phần giữ cho otp/giao dịch
QUOTA_EXHAUSTED = "quota_exhausted"    # hết ngân sách trong ngày hoặc Google chặn 5.4.5
AUTH_FAILED = "auth_failed"            # 535, app password sai hoặc bị thu hồi
REJECTED = "rejected"                  # 5xx khác: địa chỉ không tồn tại, nội dung bị từ chối
TRANSIENT_FAILED = "transient_failed"  # hết lượt retry
NOT_CONFIGURED = "not_configured"
INVALID = "invalid"

_log = logging.getLogger("hangos.mail")


@dataclass
class SendResult:
    ok: bool
    status: str
    error: str = ""
    attempts: int = 0
    message_id: str = ""


def mask_email(addr: str) -> str:
    """a***@gmail.com. Dùng cho log, không bao giờ log địa chỉ đầy đủ."""
    addr = (addr or "").strip()
    if "@" not in addr:
        return "***"
    local, domain = addr.rsplit("@", 1)
    return (local[:1] or "*") + "***@" + domain


def _env(name: str, *fallbacks: str, default: str = "") -> str:
    for n in (name,) + fallbacks:
        v = os.environ.get(n, "")
        if v and v.strip():
            return v.strip()
    return default


def _is_quota_error(code: int, text: str) -> bool:
    t = (text or "").lower()
    return "5.4.5" in t or "sending limit" in t or "daily user sending" in t


class WorkspaceMailer:
    def __init__(
        self,
        user: str,
        app_password: str,
        *,
        host: str = "smtp.gmail.com",
        port: int = 465,
        from_name: str = "Đào Thị Hằng",
        reply_to: str = "",
        daily_budget: int = 500,
        priority_reserve: int = 150,
        state_path: str = "",
        timeout: float = 15.0,
        max_attempts: int = 3,
        smtp_factory: Optional[Callable] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        logger: Optional[logging.Logger] = None,
    ):
        self.user = (user or "").strip()
        self._password = (app_password or "").replace(" ", "").strip()
        self.host = host
        self.port = int(port)
        self.from_name = from_name
        self.reply_to = reply_to
        self.daily_budget = max(0, int(daily_budget))
        self.priority_reserve = max(0, min(int(priority_reserve), self.daily_budget))
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self._smtp_factory = smtp_factory
        self._sleep = sleep
        self._clock = clock
        self._log = logger or _log
        self._lock = threading.Lock()
        self._db_path = state_path or ":memory:"
        self._mem_conn = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_db()

    # ------------------------------------------------------------ cấu hình
    @classmethod
    def from_env(cls, **overrides) -> "WorkspaceMailer":
        """Đọc HANGOS_MAIL_*; GMAIL_USER / GMAIL_APP_PASSWORD là tên cũ, vẫn nhận."""
        data_dir = _env("DATA_DIR", default="")
        default_state = os.path.join(data_dir, "hangos_mail_state.sqlite3") if data_dir else ""
        kw = dict(
            user=_env("HANGOS_MAIL_USER", "GMAIL_USER"),
            app_password=_env("HANGOS_MAIL_APP_PASSWORD", "GMAIL_APP_PASSWORD"),
            host=_env("HANGOS_MAIL_SMTP_HOST", default="smtp.gmail.com"),
            port=int(_env("HANGOS_MAIL_SMTP_PORT", default="465")),
            from_name=_env("HANGOS_MAIL_FROM_NAME", default="Đào Thị Hằng"),
            reply_to=_env("HANGOS_MAIL_REPLY_TO", default=""),
            daily_budget=int(_env("HANGOS_MAIL_DAILY_BUDGET", default="500")),
            priority_reserve=int(_env("HANGOS_MAIL_PRIORITY_RESERVE", default="150")),
            state_path=_env("HANGOS_MAIL_STATE_PATH", default=default_state),
            timeout=float(_env("HANGOS_MAIL_TIMEOUT_SEC", default="15")),
            max_attempts=int(_env("HANGOS_MAIL_MAX_ATTEMPTS", default="3")),
        )
        kw.update(overrides)
        return cls(**kw)

    def configured(self) -> bool:
        return bool(self.user and "@" in self.user and self._password)

    # ------------------------------------------------------------ trạng thái
    def _conn(self):
        if self._mem_conn is not None:
            return self._mem_conn
        d = os.path.dirname(self._db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        c = sqlite3.connect(self._db_path, timeout=10)
        return c

    def _init_db(self):
        c = self._conn()
        try:
            c.execute("CREATE TABLE IF NOT EXISTS sends (ts REAL NOT NULL, priority TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS sends_ts ON sends(ts)")
            c.execute("CREATE TABLE IF NOT EXISTS dedupe (k TEXT PRIMARY KEY, ts REAL NOT NULL, status TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v REAL NOT NULL)")
            c.commit()
        finally:
            if c is not self._mem_conn:
                c.close()

    def _sent_last_24h(self, c) -> int:
        now = self._clock()
        c.execute("DELETE FROM sends WHERE ts < ?", (now - 86400,))
        return c.execute("SELECT COUNT(*) FROM sends WHERE ts >= ?", (now - 86400,)).fetchone()[0]

    def quota_status(self) -> dict:
        with self._lock:
            c = self._conn()
            try:
                used = self._sent_last_24h(c)
                row = c.execute("SELECT v FROM kv WHERE k='blocked_until'").fetchone()
                c.commit()
            finally:
                if c is not self._mem_conn:
                    c.close()
        blocked_until = row[0] if row and row[0] > self._clock() else 0
        return {"used_24h": used, "daily_budget": self.daily_budget,
                "priority_reserve": self.priority_reserve, "blocked_until": blocked_until}

    # ------------------------------------------------------------ gửi
    def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: Optional[str] = None,
        *,
        priority: str = "transactional",
        idempotency_key: Optional[str] = None,
        dedupe_ttl: int = 86400,
        list_unsubscribe: Optional[str] = None,
        is_suppressed: Optional[Callable[[str], bool]] = None,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> SendResult:
        to = (to or "").strip()
        if priority not in PRIORITIES:
            raise ValueError(f"priority phải là một trong {PRIORITIES}")
        if not to or "@" not in to or "\n" in to or "\r" in to:
            return self._done(to, priority, SendResult(False, INVALID, "invalid recipient"))
        if not self.configured():
            return self._done(to, priority, SendResult(False, NOT_CONFIGURED, "mailer chua cau hinh"))
        if priority == "marketing":
            if not list_unsubscribe:
                raise ValueError("email marketing bắt buộc có list_unsubscribe")
            if is_suppressed is None:
                raise ValueError("email marketing bắt buộc có is_suppressed (nguồn CDP)")
        if is_suppressed is not None and priority in ("marketing", "alert"):
            try:
                if is_suppressed(to):
                    return self._done(to, priority, SendResult(False, SUPPRESSED, "suppressed"))
            except Exception:
                # Không đọc được suppression thì KHÔNG gửi marketing (fail-closed).
                if priority == "marketing":
                    return self._done(to, priority, SendResult(False, SUPPRESSED, "suppression_check_failed"))

        dkey = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() if idempotency_key else None
        gate = self._reserve(dkey, priority, dedupe_ttl)
        if gate is not None:
            return self._done(to, priority, gate)

        msg = self._build(to, subject, text, html, from_name, reply_to, list_unsubscribe, headers)
        result = self._deliver(msg)
        self._finish(dkey, priority, result)
        return self._done(to, priority, result)

    def _reserve(self, dkey, priority, dedupe_ttl) -> Optional[SendResult]:
        now = self._clock()
        with self._lock:
            c = self._conn()
            try:
                row = c.execute("SELECT v FROM kv WHERE k='blocked_until'").fetchone()
                if row and row[0] > now:
                    return SendResult(False, QUOTA_EXHAUSTED, "google_quota_block")
                used = self._sent_last_24h(c)
                limit = self.daily_budget - (self.priority_reserve if priority == "marketing" else 0)
                if used >= limit:
                    status = QUOTA_RESERVED if (priority == "marketing" and used < self.daily_budget) else QUOTA_EXHAUSTED
                    c.commit()
                    return SendResult(False, status, f"used_24h={used}")
                if dkey:
                    r = c.execute("SELECT ts, status FROM dedupe WHERE k=?", (dkey,)).fetchone()
                    if r:
                        ts, st = r
                        if st == SENT and ts >= now - dedupe_ttl:
                            c.commit()
                            return SendResult(False, DUPLICATE, "already_sent")
                        if st == "pending" and ts >= now - 300:
                            c.commit()
                            return SendResult(False, DUPLICATE, "in_flight")
                    c.execute("INSERT OR REPLACE INTO dedupe(k, ts, status) VALUES (?,?,?)", (dkey, now, "pending"))
                c.commit()
            finally:
                if c is not self._mem_conn:
                    c.close()
        return None

    def _finish(self, dkey, priority, result: SendResult):
        now = self._clock()
        with self._lock:
            c = self._conn()
            try:
                if result.ok:
                    c.execute("INSERT INTO sends(ts, priority) VALUES (?,?)", (now, priority))
                if dkey:
                    if result.ok:
                        c.execute("UPDATE dedupe SET status=?, ts=? WHERE k=?", (SENT, now, dkey))
                    else:
                        c.execute("DELETE FROM dedupe WHERE k=?", (dkey,))
                if result.status == QUOTA_EXHAUSTED and result.error.startswith("google"):
                    c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES ('blocked_until', ?)", (now + 86400,))
                c.commit()
            finally:
                if c is not self._mem_conn:
                    c.close()

    def _build(self, to, subject, text, html, from_name, reply_to, list_unsubscribe, headers) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((from_name or self.from_name, self.user))
        msg["To"] = to
        msg["Subject"] = (subject or "").replace("\r", " ").replace("\n", " ")
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self.user.rsplit("@", 1)[-1])
        rt = reply_to if reply_to is not None else self.reply_to
        if rt:
            msg["Reply-To"] = rt
        if list_unsubscribe:
            msg["List-Unsubscribe"] = f"<{list_unsubscribe}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        for k, v in (headers or {}).items():
            if "\n" in str(v) or "\r" in str(v):
                continue
            msg[k] = v
        msg.set_content(text or "")
        if html:
            msg.add_alternative(html, subtype="html")
        return msg

    def _open(self):
        if self._smtp_factory:
            return self._smtp_factory(self.host, self.port, self.timeout)
        if self.port == 465:
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        s = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        s.starttls()
        return s

    def _deliver(self, msg: EmailMessage) -> SendResult:
        attempt = 0
        last_err = ""
        while attempt < self.max_attempts:
            attempt += 1
            try:
                srv = self._open()
                try:
                    srv.login(self.user, self._password)
                    srv.send_message(msg)
                finally:
                    try:
                        srv.quit()
                    except Exception:
                        pass
                return SendResult(True, SENT, "", attempt, msg["Message-ID"])
            except smtplib.SMTPAuthenticationError as e:
                return SendResult(False, AUTH_FAILED, f"smtp_{e.smtp_code}", attempt)
            except smtplib.SMTPRecipientsRefused as e:
                codes = [v[0] for v in (e.recipients or {}).values()]
                texts = " ".join(str(v[1]) for v in (e.recipients or {}).values())
                code = codes[0] if codes else 550
                if _is_quota_error(code, texts):
                    return SendResult(False, QUOTA_EXHAUSTED, "google_quota_5.4.5", attempt)
                if 400 <= code < 500:
                    last_err = f"smtp_{code}"
                else:
                    return SendResult(False, REJECTED, f"smtp_{code}", attempt)
            except smtplib.SMTPResponseException as e:
                text = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
                if _is_quota_error(e.smtp_code, text):
                    return SendResult(False, QUOTA_EXHAUSTED, "google_quota_5.4.5", attempt)
                if 400 <= e.smtp_code < 500:
                    last_err = f"smtp_{e.smtp_code}"
                else:
                    return SendResult(False, REJECTED, f"smtp_{e.smtp_code}", attempt)
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    socket.timeout, TimeoutError, ConnectionError, OSError) as e:
                last_err = type(e).__name__
            if attempt < self.max_attempts:
                self._sleep(min(2 ** attempt, 10))
        return SendResult(False, TRANSIENT_FAILED, last_err, attempt)

    def _done(self, to, priority, result: SendResult) -> SendResult:
        level = logging.INFO if result.ok or result.status in (DUPLICATE, SUPPRESSED) else logging.WARNING
        self._log.log(level, "hangos_mail status=%s priority=%s to=%s attempts=%s err=%s",
                      result.status, priority, mask_email(to), result.attempts, result.error)
        return result
