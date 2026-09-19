"""camas-kernel gui email hoc vien qua GHL roi Google Workspace, khong con Brevo (2026-09-19)."""
import asyncio

import pytest

from routes import _email as E


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)


def _run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch, ghl_ok, ws_ok):
    calls = []

    async def ghl(*a, **k):
        calls.append("ghl"); return ghl_ok

    async def ws(*a, **k):
        calls.append("ws"); return ws_ok

    monkeypatch.setattr(E, "send_ghl_email", ghl)
    monkeypatch.setattr(E, "send_workspace_email", ws)
    return calls


def test_mac_dinh_ghl_truoc_workspace_lui(monkeypatch):
    calls = _patch(monkeypatch, True, True)
    assert _run(E.send_email("a@gmail.com", "A", "s", "<p>x</p>")) is True
    assert calls == ["ghl"]


def test_ghl_that_bai_lui_ve_workspace(monkeypatch):
    calls = _patch(monkeypatch, False, True)
    assert _run(E.send_email("a@gmail.com", "A", "s", "<p>x</p>")) is True
    assert calls == ["ghl", "ws"]


def test_provider_workspace_uu_tien_workspace(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "workspace")
    calls = _patch(monkeypatch, True, True)
    assert _run(E.send_email("a@gmail.com", "A", "s", "<p>x</p>")) is True
    assert calls == ["ws"]


def test_ca_hai_that_bai_tra_false_khong_raise(monkeypatch):
    calls = _patch(monkeypatch, False, False)
    assert _run(E.send_email("a@gmail.com", "A", "s", "<p>x</p>")) is False
    assert calls == ["ghl", "ws"]


def test_workspace_chua_cau_hinh_thi_bo_qua(monkeypatch):
    for k in ("HANGOS_MAIL_USER", "HANGOS_MAIL_APP_PASSWORD", "GMAIL_USER", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert _run(E.send_workspace_email("a@gmail.com", "A", "s", "<p>x</p>")) is False


def test_ten_cu_van_dung_duoc_nhung_khong_con_brevo():
    assert hasattr(E, "send_student_email")
    src = open(E.__file__, encoding="utf-8").read()
    assert "api.brevo.com" not in src and "BREVO_API_KEY" not in src
    assert not hasattr(E, "_send_brevo_raw")


def test_ban_sao_mailer_khop_ban_goc():
    import pathlib
    here = pathlib.Path(__file__).resolve().parents[1] / "kernel" / "workspace_mailer.py"
    assert "WorkspaceMailer" in here.read_text(encoding="utf-8")
