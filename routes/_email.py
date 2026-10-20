"""Gửi email cho học viên BreakoutOS. Đường chính GHL, đường lùi Google Workspace.

Lịch sử: Brevo hết credit từ 19/07/2026 nên mọi email cho học viên thất bại trong
im lặng; 13/08 chuyển sang GHL. 19/09/2026 bỏ hẳn Brevo, thêm Google Workspace
(support@daothihang.com) làm đường lùi vì GHL chặn contact DND.

Đổi đường bằng biến EMAIL_PROVIDER: ghl (mặc định: GHL rồi Workspace) |
workspace (Workspace rồi GHL). Workspace cần HANGOS_MAIL_USER +
HANGOS_MAIL_APP_PASSWORD (mật khẩu ứng dụng riêng của service camas-kernel).
Chưa cấu hình thì Workspace tự bỏ qua, hành vi giữ nguyên như trước.

Sender + replyTo theo quy ước Anna: "Đào Thị Hằng" <support@daothihang.com>.
Best-effort: mọi lỗi gửi đều nuốt + log, KHÔNG raise (caller không được fail vì email).
"""
from __future__ import annotations

import logging
import os

from kernel.workspace_mailer import mask_email

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

log = logging.getLogger("camas.email")

SENDER = {"name": "Đào Thị Hằng", "email": "support@daothihang.com"}
REPLY_TO = {"email": "support@daothihang.com"}


async def send_workspace_email(
    to_email: str, to_name: str | None, subject: str, html: str,
    idempotency_key: str | None = None,
) -> bool:
    """Gửi 1 email giao dịch qua Google Workspace (lớp gửi dùng chung)."""
    import asyncio
    from kernel.workspace_mailer import WorkspaceMailer

    mailer = WorkspaceMailer.from_env(from_name=SENDER["name"], reply_to=REPLY_TO["email"])
    if not mailer.configured():
        log.info("Workspace mailer chua cau hinh, bo qua duong Workspace")
        return False
    res = await asyncio.to_thread(
        mailer.send, to_email, subject, "Vui lòng mở email ở chế độ HTML.", html,
        priority="transactional", idempotency_key=idempotency_key,
    )
    if not res.ok and res.status != "duplicate":
        log.warning("Workspace send fail status=%s", res.status)
    return res.ok


GHL_SEARCH_URL = "https://services.leadconnectorhq.com/contacts/search"
GHL_MESSAGE_URL = "https://services.leadconnectorhq.com/conversations/messages"


async def send_ghl_email(
    to_email: str, to_name: str | None, subject: str, html: str,
) -> bool:
    """Gửi email qua GHL Conversations API.

    GHL gửi theo contact chứ không theo địa chỉ, nên phải tìm contact trước.
    Không tìm thấy thì trả False, KHÔNG tự tạo contact mới: tạo contact là
    đụng vào dữ liệu CRM của Anna, không phải việc của lớp gửi email.
    """
    if httpx is None:
        log.warning("httpx unavailable, skip email to %s", to_email)
        return False
    tok = os.environ.get("GHL_API_KEY", "").strip()
    loc = os.environ.get("GHL_LOCATION_ID", "").strip()
    if not tok or not loc:
        log.warning("GHL_API_KEY/GHL_LOCATION_ID chua set, skip email")
        return False
    if not to_email or "@" not in to_email:
        log.warning("invalid to_email %r, skip", to_email)
        return False
    headers = {
        "Authorization": f"Bearer {tok}",
        "Version": "2021-07-28",
        "Content-Type": "application/json",
        # GHL chan request thieu User-Agent (loi 403 "1010").
        "User-Agent": "camas-kernel/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(GHL_SEARCH_URL, headers=headers, json={
                "locationId": loc, "pageLimit": 1,
                "filters": [{"field": "email", "operator": "eq", "value": to_email}],
            })
            if r.status_code >= 300:
                log.warning("GHL tim contact loi %s: %s", r.status_code, r.text[:160])
                return False
            contacts = (r.json() or {}).get("contacts", [])
            if not contacts:
                log.warning("GHL khong co contact cho %s, khong gui duoc", to_email)
                return False
            r2 = await client.post(GHL_MESSAGE_URL, headers=headers, json={
                "type": "Email", "contactId": contacts[0]["id"], "locationId": loc,
                "subject": subject, "html": html,
                "emailFrom": SENDER["email"],
            })
            if r2.status_code >= 300:
                log.warning("GHL gui email loi %s: %s", r2.status_code, r2.text[:160])
                return False
            log.info("da gui email qua GHL toi %s", to_email)
            return True
    except Exception as exc:  # noqa: BLE001 - email hong khong duoc lam gay caller
        log.warning("GHL gui email that bai: %r", exc)
        return False


async def send_email(
    to_email: str, to_name: str | None, subject: str, html: str,
) -> bool:
    """Cua duy nhat de gui email cho hoc vien. Tu chon duong theo EMAIL_PROVIDER."""
    provider = os.environ.get("EMAIL_PROVIDER", "ghl").strip().lower()
    if provider == "workspace":
        if await send_workspace_email(to_email, to_name, subject, html):
            return True
        log.warning("Workspace that bai, thu lui ve GHL cho %s", mask_email(to_email))
        return await send_ghl_email(to_email, to_name, subject, html)
    if await send_ghl_email(to_email, to_name, subject, html):
        return True
    log.warning("GHL that bai, thu lui ve Workspace cho %s", mask_email(to_email))
    return await send_workspace_email(to_email, to_name, subject, html)


async def send_student_email(
    to_email: str, to_name: str | None, subject: str, html: str,
) -> bool:
    """Ten goi cho email hoc vien (truoc day send_brevo_email). Thuc te di qua send_email()."""
    return await send_email(to_email, to_name, subject, html)
