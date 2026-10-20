"""CloudFront signing and player rendering, using only an in-memory test key."""
import base64
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from routes import _video_sign
from routes.academy_routes import _video_html


@pytest.fixture
def signing_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    monkeypatch.setenv("CF_SIGNING_KEY_ID", "TESTKEY")
    monkeypatch.setenv("CF_SIGNING_PRIVATE_KEY", pem.decode())
    return key


def decode_cf(value):
    return base64.b64decode(value.translate(str.maketrans("-_~", "+=/")))


def test_sign_policy_and_rsa_signature(signing_key):
    before = int(time.time())
    signed = _video_sign.ky_thu_muc("https://cdn.example/hls/bai-1/master.m3u8")
    assert _video_sign.co_khoa()
    assert "Policy=" in signed and "&Signature=" in signed
    assert "&Key-Pair-Id=TESTKEY" in signed
    params = parse_qs(signed)
    policy = decode_cf(params["Policy"][0])
    statement = json.loads(policy)["Statement"][0]
    assert statement["Resource"] == "https://cdn.example/hls/bai-1/*"
    expiry = statement["Condition"]["DateLessThan"]["AWS:EpochTime"]
    assert before + _video_sign.HAN_MAC_DINH <= expiry
    assert expiry <= int(time.time()) + _video_sign.HAN_MAC_DINH
    assert expiry > time.time()
    signing_key.public_key().verify(decode_cf(params["Signature"][0]), policy,
                                    padding.PKCS1v15(), hashes.SHA1())


@pytest.mark.parametrize("missing", [("CF_SIGNING_KEY_ID",),
                                    ("CF_SIGNING_PRIVATE_KEY",),
                                    ("CF_SIGNING_KEY_ID", "CF_SIGNING_PRIVATE_KEY")])
def test_missing_keys(monkeypatch, signing_key, missing):
    for name in missing:
        monkeypatch.delenv(name, raising=False)
    assert _video_sign.co_khoa() is False
    assert _video_sign.ky_thu_muc("https://cdn.example/hls/bai-1/master.m3u8") == ""


@pytest.mark.parametrize("suffix", ["", "?lang=vi&track=1", "?next=/other/file#start"])
def test_player_signed_url_and_fallback(signing_key, suffix):
    markup = _video_html("https://cdn.example/hls/bai-1/master.m3u8" + suffix)
    script = markup.split('<script>(function()', 1)[1]
    assert "amp;Policy" not in markup
    assert "&Policy=" in script and "&Signature=" in script
    assert "&Key-Pair-Id=TESTKEY" in script
    match = re.search(r',u=("(?:\\.|[^"\\])*"),k=', script)
    url = json.loads(match.group(1))
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    assert params["v"] == ["2"]
    assert params["Key-Pair-Id"] == ["TESTKEY"]
    assert json.loads(decode_cf(params["Policy"][0]))["Statement"][0]["Resource"] == (
        "https://cdn.example/hls/bai-1/*")
    signing_key.public_key().verify(decode_cf(params["Signature"][0]),
                                    decode_cf(params["Policy"][0]),
                                    padding.PKCS1v15(), hashes.SHA1())
    if "lang=" in suffix:
        assert params["lang"] == ["vi"] and params["track"] == ["1"]
    if "#start" in suffix:
        assert parts.fragment == "start" and params["next"] == ["/other/file"]
    assert 'xhrSetup:function' in script
    assert 'v.src=u' not in script
    assert 'v.outerHTML="<p>Mở bằng Chrome để xem video</p>"' in script
    assert 'Mở bằng Chrome để xem video</video>' in markup


def test_script_escaping(monkeypatch):
    monkeypatch.setattr(_video_sign, "ky_thu_muc", lambda url: "")
    url = 'https://cdn.example/master.m3u8?text=</script>"&x=1'
    markup = _video_html(url)
    assert '<\\/script>' in markup
    assert markup.count('</script>') == 2
    assert '&x=1&v=2' in markup
    assert '\\"' in markup


def test_non_hls_html_escaping():
    assert _video_html(None) == ""
    assert _video_html("") == ""
    markup = _video_html('https://example/video?x="&y=1')
    assert 'src="https://example/video?x=&quot;&amp;y=1"' in markup
    assert '<script' not in markup
