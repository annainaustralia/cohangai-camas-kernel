"""Ky link video CloudFront cho hoc vien da xac thuc.

Truoc day ai co duong dan la xem duoc. Nay CloudFront chi nhan yeu cau co chu ky
con han, va chu ky chi cap khi hoc vien vao bai hoc bang link co chu ky cua minh.

Ky theo "custom policy" voi pham vi la ca thu muc video, nen mot chu ky dung cho
danh muc lan moi manh .ts ben trong. Trinh phat noi them chu ky vao tung yeu cau.
"""
from __future__ import annotations

import base64
import json
import os
import time

_KEY_ID_ENV = "CF_SIGNING_KEY_ID"
_KEY_ENV = "CF_SIGNING_PRIVATE_KEY"
HAN_MAC_DINH = 6 * 3600      # 6 tieng, du cho mot buoi hoc, het thi tai lai trang


def _b64(data: bytes) -> str:
    """CloudFront dung bien the base64 rieng cho URL."""
    return base64.b64encode(data).decode().replace("+", "-").replace("=", "_").replace("/", "~")


def co_khoa() -> bool:
    return bool(os.environ.get(_KEY_ID_ENV) and os.environ.get(_KEY_ENV))


def ky_thu_muc(url_master: str, han_giay: int = HAN_MAC_DINH) -> str:
    """Tra ve chuoi query da ky, dung cho ca thu muc chua video nay."""
    if not co_khoa():
        return ""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        return ""
    goc = url_master.rsplit("/", 1)[0] + "/*"
    het_han = int(time.time()) + han_giay
    policy = json.dumps({"Statement": [{"Resource": goc,
                                        "Condition": {"DateLessThan": {"AWS:EpochTime": het_han}}}]},
                        separators=(",", ":"))
    khoa = serialization.load_pem_private_key(os.environ[_KEY_ENV].encode(), password=None)
    chu_ky = khoa.sign(policy.encode(), padding.PKCS1v15(), hashes.SHA1())
    return (f"Policy={_b64(policy.encode())}"
            f"&Signature={_b64(chu_ky)}"
            f"&Key-Pair-Id={os.environ[_KEY_ID_ENV]}")
