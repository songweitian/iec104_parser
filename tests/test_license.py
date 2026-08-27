"""
授权模块单元测试：永久码 / 限时码 / 过期判定 / 篡改检测 / 旧格式兼容
说明：测试通过替换 license.LICENSE_FILE 到临时目录，隔离真实授权文件。
依赖 pytest（或可单独运行：python -m pytest tests/test_license.py）
"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license as L

FP = "AB12CD34EF56AB78CD90EF12AB34CD56"


class TestLicenseCodeResolution:
    """激活码解析与验证（不写磁盘）"""

    def test_permanent_code(self):
        code = L.generate_license_code(FP)
        assert L._resolve_license_expiry(FP, code) == ""
        assert L.verify_license_code(FP, code) is True

    def test_permanent_code_dashed_and_plain(self):
        dashed = L.generate_license_code(FP)
        plain = dashed.replace("-", "").upper()
        assert L._resolve_license_expiry(FP, plain) == ""
        assert L.verify_license_code(FP, plain) is True

    def test_permanent_code_0_and_word(self):
        """'0' / '永久' 哨兵应生成同等的纯永久码"""
        assert L.generate_license_code(FP, "0") == L.generate_license_code(FP)
        assert L.generate_license_code(FP, "永久") == L.generate_license_code(FP)
        assert L.verify_license_code(FP, L.generate_license_code(FP, "0")) is True

    def test_timed_code_resolution(self):
        code = L.generate_license_code(FP, "2026-12-31")
        assert L._resolve_license_expiry(FP, code) == "2026-12-31"
        assert L.verify_license_code(FP, code) is True

    def test_timed_code_hex_without_dashes(self):
        """hex 部分无横线、日期后缀保留横线，同样可解析"""
        timed = L.generate_license_code(FP, "2026-12-31")
        hex_only = timed[:-11].replace("-", "")  # 去掉 hex 内部横线，保留 "-YYYY-MM-DD"
        code = hex_only + "-2026-12-31"
        assert len(hex_only) == 32
        assert L._resolve_license_expiry(FP, code) == "2026-12-31"

    def test_tampered_date_rejected(self):
        code = L.generate_license_code(FP, "2026-12-31")
        # 把日期后缀改为更晚日期（签名不再匹配）
        tampered = code[:-10] + "2099-12-31"
        assert code[-10:] == "2026-12-31"
        assert L._resolve_license_expiry(FP, tampered) is None
        assert L.verify_license_code(FP, tampered) is False

    def test_tampered_hex_rejected(self):
        code = L.generate_license_code(FP)
        tampered = code[:-1] + ("0" if code[-1] != "0" else "1")
        assert L.verify_license_code(FP, tampered) is False

    def test_invalid_date_format_rejected(self):
        code = L.generate_license_code(FP, "2026/12/31")
        assert L._resolve_license_expiry(FP, code) is None
        assert L.verify_license_code(FP, code) is False

    def test_malformed_code_rejected(self):
        assert L.verify_license_code(FP, "NOT_A_CODE") is False
        assert L.verify_license_code(FP, "") is False


class TestLicenseFileRoundTrip:
    """激活/保存/加载/状态（写临时 LICENSE_FILE）

    注意：activate / save_license / load_license / is_activated 一律绑定
    真实硬件指纹（get_hardware_fingerprint），因此本类用真实指纹生成激活码，
    而非合成的 FP 常量。
    """

    @property
    def _fp(self):
        return L.get_hardware_fingerprint()

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        L.LICENSE_FILE = os.path.join(self._tmpdir, "license.dat")

    def teardown_method(self):
        if os.path.exists(L.LICENSE_FILE):
            os.remove(L.LICENSE_FILE)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_activate_permanent(self):
        ok, msg = L.activate(L.generate_license_code(self._fp))
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
        ok, msg = L.activate(L.generate_license_code(self._fp, future))
        assert ok
        status = L.get_license_status()
        assert status["activated"] is True
        assert status["permanent"] is False
        assert status["expiry_date"] == future
        assert 1 <= status["days_remaining"] <= 31

    def test_activate_past_date_rejected(self):
        """已过期日期的激活码应被拒绝激活"""
        past = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        ok, msg = L.activate(L.generate_license_code(self._fp, past))
        assert ok is False

    def test_expired_status(self):
        """写入一个已过期的授权文件，状态应为 expired / 未激活"""
        past = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        code = L.generate_license_code(self._fp, past)
        assert L.save_license(self._fp, code)
        status = L.get_license_status()
        assert status["expired"] is True
        assert status["activated"] is False
        assert L.is_activated() is False
        assert L.is_license_expired() is True

    def test_removed_license_inactive(self):
        assert L.is_activated() is False
        L.save_license(self._fp, L.generate_license_code(self._fp))
        assert L.is_activated() is True
        assert L.remove_license() is True
        assert L.is_activated() is False


class TestLicenseBackwardCompat:
    """旧版存储格式（32hex 无日期字段）仍应作为永久授权识别"""

    def test_legacy_plain_code_is_permanent(self):
        # 旧版保存的 code 无横线且不含日期后缀
        code = L.generate_license_code(FP).replace("-", "").upper()
        assert L._resolve_license_expiry(FP, code) == ""
        assert L.verify_license_code(FP, code) is True


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

    for cls in (TestLicenseCodeResolution, TestLicenseFileRoundTrip,
                TestLicenseBackwardCompat):
        _run_class(cls)
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)