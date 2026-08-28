"""授权模块单元测试：Ed25519 永久/限时/过期/篡改/旧版停用

测试用自建的临时 Ed25519 密钥对签发（不依赖开发者私钥文件 license_ed25519.key），
通过 monkeypatch license._LICENSE_PUBLIC_KEY_HEX 驱动验签；
授权文件路径替换到临时目录隔离真实 license.dat。
依赖 pytest（或单独运行：python tests/test_license.py）
"""
import os
import sys
import base64
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license as L
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# 测试专用密钥对（固定种子，与真实签发无关）
_TEST_SEED = bytes.fromhex("11" * 32)
_TEST_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
_TEST_PUB_HEX = _TEST_PRIV.public_key().public_bytes_raw().hex().upper()

_ORIG_PUB_KEY = L._LICENSE_PUBLIC_KEY_HEX

FP = "AB12CD34EF56AB78CD90EF12AB34CD56"


def _use_test_key():
    """把验签公钥切换到测试密钥，隔离真实客户端公钥"""
    L._LICENSE_PUBLIC_KEY_HEX = _TEST_PUB_HEX


def _restore_pub_key():
    L._LICENSE_PUBLIC_KEY_HEX = _ORIG_PUB_KEY


def make_code(fingerprint: str, expiry_date: str = "") -> str:
    """用测试私钥签发激活码（模拟 gen_license.py 的 sign_code）"""
    token = "0" if not expiry_date else expiry_date
    sig = _TEST_PRIV.sign(f"{fingerprint}:{token}".encode())
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{sig_b64}-{expiry_date}" if expiry_date else sig_b64


class TestLicenseCodeResolution:
    """激活码解析与验证（不写磁盘）"""

    def setup_method(self):
        _use_test_key()

    def teardown_method(self):
        _restore_pub_key()

    def test_permanent(self):
        code = make_code(FP)
        assert L._resolve_license_expiry(FP, code) == ""
        assert L.verify_license_code(FP, code) is True

    def test_timed(self):
        code = make_code(FP, "2026-12-31")
        assert L._resolve_license_expiry(FP, code) == "2026-12-31"
        assert L.verify_license_code(FP, code) is True

    def test_tampered_date_rejected(self):
        code = make_code(FP, "2026-12-31")
        tampered = code[:-10] + "2099-12-31"
        assert L._resolve_license_expiry(FP, tampered) is None
        assert L.verify_license_code(FP, tampered) is False

    def test_tampered_signature_rejected(self):
        code = make_code(FP)
        last = "A" if code[-1] != "A" else "B"
        assert L._resolve_license_expiry(FP, code[:-1] + last) is None

    def test_wrong_fingerprint_rejected(self):
        code = make_code(FP, "2026-12-31")
        assert L.verify_license_code("00" * 16, code) is False

    def test_copy_paste_whitespace_tolerated(self):
        code = make_code(FP)
        assert L._resolve_license_expiry(FP, f"  {code}\r\n") == ""

    def test_permanent_plus_fake_date_is_not_timed(self):
        """永久码后面硬拼一个日期，不能变成限时码"""
        perm = make_code(FP)
        assert L._resolve_license_expiry(FP, perm + "-2030-01-01") is None
        assert L.verify_license_code(FP, perm + "-2030-01-01") is False

    def test_past_date_timed_code(self):
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        code = make_code(FP, past)
        assert L._resolve_license_expiry(FP, code) == past   # 签名有效，但日期已过
        assert L.verify_license_code(FP, code) is True       # 验签通过（过期判定不在此层）

    def test_malformed_junk_rejected(self):
        for junk in ("", "NOT_A_CODE", "A" * 200, "@@@@", "-", "1" * 43):
            assert L._resolve_license_expiry(FP, junk) is None

    def test_legacy_format_detected(self):
        assert L.is_legacy_code("AB12-CD34-EF56-AB78") is True
        plain32 = "AB12CD34EF56AB78CD90EF12AB34CD56"
        assert L.is_legacy_code(plain32) is True
        assert L.is_legacy_code(f"{plain32}-2026-12-31") is True
        assert L.is_legacy_code(make_code(FP)) is False


class TestLegacyDeactivated:
    """旧版（HMAC）激活码停用：activate 应明确拒绝并提示换码"""

    def test_activate_rejects_legacy_code(self):
        _use_test_key()
        try:
            ok, msg = L.activate("AB12-CD34-EF56-AB78")
            assert ok is False
            assert "旧版" in msg and "换" in msg
        finally:
            _restore_pub_key()


class TestLicenseFileRoundTrip:
    """激活/保存/加载/状态（写临时 LICENSE_FILE，用真实硬件指纹）"""

    @property
    def _fp(self):
        return L.get_hardware_fingerprint()

    def setup_method(self):
        _use_test_key()
        self._tmpdir = tempfile.mkdtemp()
        L.LICENSE_FILE = os.path.join(self._tmpdir, "license.dat")

    def teardown_method(self):
        if os.path.exists(L.LICENSE_FILE):
            os.remove(L.LICENSE_FILE)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _restore_pub_key()

    def test_activate_permanent(self):
        ok, msg = L.activate(make_code(self._fp))
        assert ok
        status = L.get_license_status()
        assert status["activated"] is True
        assert status["permanent"] is True
        assert status["expired"] is False
        assert status["expiry_date"] == ""
        assert L.is_activated() is True
        assert L.is_license_expired() is False

    def test_activate_timed(self):
        future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        ok, msg = L.activate(make_code(self._fp, future))
        assert ok
        status = L.get_license_status()
        assert status["activated"] is True
        assert status["permanent"] is False
        assert status["expiry_date"] == future
        assert 1 <= status["days_remaining"] <= 31

    def test_activate_past_date_rejected(self):
        past = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        ok, msg = L.activate(make_code(self._fp, past))
        assert ok is False
        assert "过期" in msg

    def test_expired_status(self):
        past = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        code = make_code(self._fp, past)
        assert L.save_license(self._fp, code)
        status = L.get_license_status()
        assert status["expired"] is True
        assert status["activated"] is False
        assert L.is_activated() is False
        assert L.is_license_expired() is True

    def test_removed_license_inactive(self):
        assert L.is_activated() is False
        L.save_license(self._fp, make_code(self._fp))
        assert L.is_activated() is True
        assert L.remove_license() is True
        assert L.is_activated() is False

    def test_saved_code_preserved_verbatim(self):
        """新激活码区分大小写，保存/加载不得改大写"""
        code = make_code(self._fp, "2026-12-31")
        assert code.upper() != code  # base64url 含小写/'_'，确保测试前提成立
        assert L.save_license(self._fp, code)
        assert L.load_license()["code"] == code
        assert L.verify_license_code(self._fp, code) is True


if __name__ == "__main__":
    # 支持直接运行：python tests/test_license.py（无需 pytest）
    import traceback

    total = failed = 0

    def _run_class(cls):
        global total, failed
        inst = cls()
        if hasattr(inst, "setup_method"):
            inst.setup_method()
        try:
            for name in sorted(dir(cls)):
                if name.startswith("test_"):
                    total += 1
                    try:
                        getattr(inst, name)()
                        print(f"PASS {cls.__name__}.{name}")
                    except Exception:
                        failed += 1
                        print(f"FAIL {cls.__name__}.{name}")
                        traceback.print_exc()
        finally:
            if hasattr(inst, "teardown_method"):
                inst.teardown_method()

    for cls in (TestLicenseCodeResolution, TestLegacyDeactivated,
                TestLicenseFileRoundTrip):
        _run_class(cls)
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)