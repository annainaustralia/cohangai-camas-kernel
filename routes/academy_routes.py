"""Academy trong BreakoutOS: hoc vien xem bai giang ngay tai day.

Anna chot 04/09/2026: dua noi dung ba phan (Foundation, Customer, Growth) ve mot
cho thay vi de ben academy.daothihang.com. Quyen xem lay tu chinh nguon entitlement
da co (config/entitlements.json -> parts cua goi hoc vien mua), khong dung bang
quyen rieng, de khong lap lai loi cu: moi noi giu mot ban mapping roi lech nhau.

Duong dan:
  GET  /hoc?student=&sig=                       thu vien, ba phan
  GET  /hoc/{course_key}?student=&sig=          muc luc mot phan
  GET  /hoc/{course_key}/{lesson_key}?...       trang bai hoc
  POST /sdl/lessons/{lesson_id}/xong?student=&sig=   danh dau da hoc xong
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from routes.sdl_routes import get_pool, require_student_active
from routes._auth import _verify_hmac

log = logging.getLogger("camas.academy")
router = APIRouter(tags=["academy"])

_ENTITLEMENTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "entitlements.json"
)


def _parts_for_program(program_id: str) -> set[str]:
    """Cac phan hoc vien duoc xem, lay tu entitlements.json theo goi da mua."""
    key = (program_id or "").strip().lower().replace("_", "-")
    try:
        with open(_ENTITLEMENTS_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:  # thieu file: mo Foundation, khong chan het hoc vien
        return {"foundation"}
    products = cfg.get("products") or {}
    prod = products.get(key)
    if not prod:
        for p in products.values():
            if (p.get("program_id") or "").lower() == key:
                prod = p
                break
    return set(prod.get("parts") or []) if prod else {"foundation"}


_CSS = """
:root{--red:#d63031;--ink:#0a0a0a;--paper:#fafaf7;--line:#e5dfd0;--muted:#5a5453}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Be Vietnam Pro',-apple-system,system-ui,sans-serif;background:var(--paper);
     color:var(--ink);line-height:1.7;font-size:17px;padding:36px 20px}
.wrap{max-width:860px;margin:0 auto}
a{color:var(--red)}
h1{font-size:30px;font-weight:800;margin-bottom:6px;line-height:1.25}
h2{font-size:21px;font-weight:700;margin:28px 0 10px}
h3{font-size:18px;font-weight:700;margin:22px 0 8px}
p{margin-bottom:14px}
ul,ol{margin:0 0 14px 22px}
li{margin-bottom:6px}
.sub{color:var(--muted);margin-bottom:26px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:14px}
.card h2{margin-top:0}
.lesson{display:flex;justify-content:space-between;gap:14px;align-items:center;
        padding:11px 0;border-bottom:1px solid var(--line)}
.lesson:last-child{border-bottom:none}
.lesson .meta{color:var(--muted);font-size:14px;white-space:nowrap}
.done{color:#1e7d4f;font-weight:700}
.back{display:inline-block;margin-bottom:18px;font-size:15px}
.btn{display:inline-block;background:var(--red);color:#fff;text-decoration:none;font-weight:700;
     padding:13px 24px;border-radius:10px;margin-top:10px;border:none;cursor:pointer;font-size:16px;
     font-family:inherit}
.pill{display:inline-block;background:rgba(214,48,49,.12);color:var(--red);padding:5px 14px;
      border-radius:999px;font-size:12px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;
      margin-bottom:12px}
.body{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px 26px;margin-top:18px}
.body pre{background:#f5f3ec;padding:14px;border-radius:8px;overflow-x:auto;font-size:14px}
.body code{background:#f5f3ec;padding:2px 6px;border-radius:4px;font-size:15px}
.body blockquote{border-left:3px solid var(--line);padding-left:14px;color:var(--muted);margin-bottom:14px}
.video{aspect-ratio:16/9;width:100%;border:0;border-radius:12px;margin-bottom:18px}
.locked{color:var(--muted);font-size:15px}
"""


def _page(title: str, inner: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>{_CSS}</style></head><body><div class="wrap">{inner}</div></body></html>""")


def _deny(msg: str = "Đường link không hợp lệ. Bạn nhắn Hằng qua Zalo 0932 093 593.") -> HTMLResponse:
    return HTMLResponse(
        _page("Không mở được", f'<h1>Không mở được trang</h1><p class="sub">{html.escape(msg)}</p>').body,
        status_code=403,
    )


def _md(src: str) -> str:
    """Markdown toi thieu, du cho giao trinh: tieu de, danh sach, dam, code, trich dan."""
    out: list[str] = []
    in_code = in_ul = in_ol = False
    for raw in (src or "").splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        esc = html.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="noreferrer">\1</a>', esc)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", esc)
        m_ol = re.match(r"^\s*(\d+)\.\s+(.*)$", esc)
        if m_ul:
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{m_ul.group(1)}</li>")
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if m_ol:
            if not in_ol:
                out.append("<ol>"); in_ol = True
            out.append(f"<li>{m_ol.group(2)}</li>")
            continue
        if in_ol:
            out.append("</ol>"); in_ol = False
        if not esc.strip():
            continue
        if esc.startswith("### "):
            out.append(f"<h3>{esc[4:]}</h3>")
        elif esc.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif esc.startswith("# "):
            out.append(f"<h2>{esc[2:]}</h2>")
        elif esc.startswith("&gt; "):
            out.append(f"<blockquote>{esc[5:]}</blockquote>")
        else:
            out.append(f"<p>{esc}</p>")
    for tag, flag in (("</ul>", in_ul), ("</ol>", in_ol), ("</pre>", in_code)):
        if flag:
            out.append(tag)
    return "\n".join(out)


async def _student(pool: asyncpg.Pool, student: str, sig: str):
    if not student or not _verify_hmac(student, sig):
        return None
    try:
        sid = UUID(student)
    except ValueError:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, full_name, program_id FROM breakoutos.students WHERE id=$1", sid
        )
    return row


def _q(student: str, sig: str) -> str:
    return f"?student={student}&sig={sig}"


@router.get("/hoc", response_class=HTMLResponse)
async def library(student: str = "", sig: str = "", pool=Depends(get_pool)):
    row = await _student(pool, student, sig)
    if row is None:
        return _deny()
    await require_student_active(pool, row["id"])
    parts = _parts_for_program(row["program_id"])
    async with pool.acquire() as conn:
        courses = await conn.fetch(
            "SELECT key, title, subtitle, part_key FROM breakoutos.courses "
            "WHERE status='published' ORDER BY position"
        )
        counts = await conn.fetch(
            """SELECT c.key, count(l.id) AS n,
                      count(p.lesson_id) FILTER (WHERE p.status='completed') AS done
               FROM breakoutos.courses c
               JOIN breakoutos.course_modules m ON m.course_key=c.key
               JOIN breakoutos.course_lessons l ON l.module_id=m.id AND l.status='published'
               LEFT JOIN breakoutos.lesson_progress p ON p.lesson_id=l.id AND p.student_id=$1
               GROUP BY c.key""",
            row["id"],
        )
    stat = {r["key"]: (r["n"], r["done"]) for r in counts}
    cards = []
    for c in courses:
        n, done = stat.get(c["key"], (0, 0))
        if c["part_key"] in parts:
            cards.append(
                f'<div class="card"><h2><a href="/hoc/{c["key"]}{_q(student, sig)}">'
                f'{html.escape(c["title"])}</a></h2>'
                f'<p class="sub" style="margin-bottom:6px">{html.escape(c["subtitle"])}</p>'
                f'<p class="meta" style="color:#5a5453;font-size:14px">{n} bài · đã xong {done}</p></div>'
            )
        else:
            cards.append(
                f'<div class="card"><h2>{html.escape(c["title"])}</h2>'
                f'<p class="locked">Phần này chưa nằm trong gói của bạn. '
                f'Bạn nhắn Hằng qua Zalo 0932 093 593 nếu muốn mở thêm.</p></div>'
            )
    name = html.escape(row["full_name"] or "")
    return _page("Thư viện học", (
        f'<h1>Chào {name or "bạn"}</h1>'
        f'<p class="sub">Toàn bộ bài giảng của bạn nằm ở đây. Học tới đâu hệ thống nhớ tới đó.</p>'
        + "".join(cards)
    ))


@router.get("/hoc/{course_key}", response_class=HTMLResponse)
async def course_outline(course_key: str, student: str = "", sig: str = "", pool=Depends(get_pool)):
    row = await _student(pool, student, sig)
    if row is None:
        return _deny()
    await require_student_active(pool, row["id"])
    async with pool.acquire() as conn:
        course = await conn.fetchrow(
            "SELECT key, title, subtitle, part_key FROM breakoutos.courses WHERE key=$1 AND status='published'",
            course_key,
        )
        if course is None:
            return _deny("Không tìm thấy phần học này.")
        if course["part_key"] not in _parts_for_program(row["program_id"]):
            return _deny("Phần này chưa nằm trong gói của bạn. Bạn nhắn Hằng qua Zalo 0932 093 593.")
        rows = await conn.fetch(
            """SELECT m.key AS mkey, m.title AS mtitle, m.position AS mpos,
                      l.key AS lkey, l.title AS ltitle, l.duration, l.format, l.position AS lpos,
                      (p.status='completed') AS done
               FROM breakoutos.course_modules m
               JOIN breakoutos.course_lessons l ON l.module_id=m.id AND l.status='published'
               LEFT JOIN breakoutos.lesson_progress p ON p.lesson_id=l.id AND p.student_id=$2
               WHERE m.course_key=$1
               ORDER BY m.position, l.position""",
            course_key, row["id"],
        )
    blocks: list[str] = []
    current = None
    for r in rows:
        if r["mkey"] != current:
            if current is not None:
                blocks.append("</div>")
            blocks.append(f'<div class="card"><h2>{html.escape(r["mtitle"])}</h2>')
            current = r["mkey"]
        meta = " · ".join(x for x in (r["format"], r["duration"]) if x)
        tick = '<span class="done">đã xong</span>' if r["done"] else html.escape(meta)
        blocks.append(
            f'<div class="lesson"><a href="/hoc/{course_key}/{r["lkey"]}{_q(student, sig)}">'
            f'{html.escape(r["ltitle"])}</a><span class="meta">{tick}</span></div>'
        )
    if current is not None:
        blocks.append("</div>")
    if not blocks:
        blocks.append('<div class="card"><p class="locked">Phần này đang được Hằng hoàn thiện.</p></div>')
    return _page(course["title"], (
        f'<a class="back" href="/hoc{_q(student, sig)}">← Thư viện</a>'
        f'<h1>{html.escape(course["title"])}</h1>'
        f'<p class="sub">{html.escape(course["subtitle"])}</p>' + "".join(blocks)
    ))


@router.get("/hoc/{course_key}/{lesson_key}", response_class=HTMLResponse)
async def lesson_page(course_key: str, lesson_key: str, student: str = "", sig: str = "",
                      pool=Depends(get_pool)):
    row = await _student(pool, student, sig)
    if row is None:
        return _deny()
    await require_student_active(pool, row["id"])
    async with pool.acquire() as conn:
        course = await conn.fetchrow(
            "SELECT key, title, part_key FROM breakoutos.courses WHERE key=$1 AND status='published'",
            course_key,
        )
        if course is None:
            return _deny("Không tìm thấy phần học này.")
        if course["part_key"] not in _parts_for_program(row["program_id"]):
            return _deny("Phần này chưa nằm trong gói của bạn. Bạn nhắn Hằng qua Zalo 0932 093 593.")
        lesson = await conn.fetchrow(
            """SELECT l.id, l.title, l.duration, l.format, l.video_url, l.workbook_url,
                      l.markdown_content, m.title AS mtitle, l.position, m.position AS mpos
               FROM breakoutos.course_lessons l
               JOIN breakoutos.course_modules m ON m.id=l.module_id
               WHERE m.course_key=$1 AND l.key=$2 AND l.status='published'""",
            course_key, lesson_key,
        )
        if lesson is None:
            return _deny("Không tìm thấy bài học này.")
        done = await conn.fetchval(
            "SELECT status='completed' FROM breakoutos.lesson_progress WHERE student_id=$1 AND lesson_id=$2",
            row["id"], lesson["id"],
        )
        nxt = await conn.fetchrow(
            """SELECT l.key FROM breakoutos.course_lessons l
               JOIN breakoutos.course_modules m ON m.id=l.module_id
               WHERE m.course_key=$1 AND l.status='published'
                 AND (m.position, l.position) > ($2, $3)
               ORDER BY m.position, l.position LIMIT 1""",
            course_key, lesson["mpos"], lesson["position"],
        )
        await conn.execute(
            """INSERT INTO breakoutos.lesson_progress (student_id, lesson_id, status)
               VALUES ($1, $2, 'started')
               ON CONFLICT (student_id, lesson_id) DO UPDATE SET updated_at=now()""",
            row["id"], lesson["id"],
        )
    video = ""
    if lesson["video_url"]:
        video = (f'<iframe class="video" src="{html.escape(lesson["video_url"])}" '
                 f'allow="accelerometer;gyroscope;encrypted-media;picture-in-picture;fullscreen" '
                 f'allowfullscreen></iframe>')
    workbook = ""
    if lesson["workbook_url"]:
        workbook = (f'<p><a href="{html.escape(lesson["workbook_url"])}" rel="noreferrer">'
                    f'Tải workbook của bài này</a></p>')
    meta = " · ".join(x for x in (lesson["mtitle"], lesson["format"], lesson["duration"]) if x)
    done_block = ('<p class="done" style="margin-top:22px">Bạn đã học xong bài này.</p>' if done else
                  f'<form method="post" action="/sdl/lessons/{lesson["id"]}/xong{_q(student, sig)}">'
                  f'<button class="btn" type="submit">Đánh dấu đã học xong</button></form>')
    next_block = ""
    if nxt:
        next_block = (f'<p style="margin-top:18px"><a href="/hoc/{course_key}/{nxt["key"]}'
                      f'{_q(student, sig)}">Bài tiếp theo →</a></p>')
    return _page(lesson["title"], (
        f'<a class="back" href="/hoc/{course_key}{_q(student, sig)}">← {html.escape(course["title"])}</a>'
        f'<div class="pill">{html.escape(meta)}</div>'
        f'<h1>{html.escape(lesson["title"])}</h1>'
        f'{video}{workbook}'
        f'<div class="body">{_md(lesson["markdown_content"])}</div>'
        f'{done_block}{next_block}'
    ))


@router.post("/sdl/lessons/{lesson_id}/xong")
async def mark_done(lesson_id: int, student: str = "", sig: str = "", pool=Depends(get_pool)):
    row = await _student(pool, student, sig)
    if row is None:
        return _deny()
    async with pool.acquire() as conn:
        course_key = await conn.fetchval(
            """SELECT m.course_key FROM breakoutos.course_lessons l
               JOIN breakoutos.course_modules m ON m.id=l.module_id WHERE l.id=$1""",
            lesson_id,
        )
        await conn.execute(
            """INSERT INTO breakoutos.lesson_progress (student_id, lesson_id, status, completed_at)
               VALUES ($1, $2, 'completed', now())
               ON CONFLICT (student_id, lesson_id) DO UPDATE
                 SET status='completed', completed_at=now(), updated_at=now()""",
            row["id"], lesson_id,
        )
    target = f"/hoc/{course_key}{_q(student, sig)}" if course_key else f"/hoc{_q(student, sig)}"
    return RedirectResponse(url=target, status_code=303)
