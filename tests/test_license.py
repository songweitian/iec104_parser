"""授权模块单元测试：Ed25519 永久/限时/过期/篡改/旧版停用

测试用自建的临时 Ed25519 密钥对签发（不依赖开发者私钥文件 license_ed25519.key），
通过 monkeypatch license._LICENSE_PUBLIC_KEY_HEX 驱动验签；
授权文件路径替换到临时目录隔离真实 license.dat。
依赖 pytest（或单独运行：python tests/test_license.py）
"""
import os
import sys
import base64
import json
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
        L._HWM_FILE = os.path.join(self._tmpdir, ".lic_hwm")

    def teardown_method(self):
        for path in (L.LICENSE_FILE, L._HWM_FILE):
            if os.path.exists(path):
                os.remove(path)
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


class _FakeDate:
    """date 替身：today() 返回固定日期（模拟系统时钟被拨回）"""

    def __init__(self, fixed: date):
        self._fixed = fixed

    def today(self) -> date:
        return self._fixed


class TestClockRollback:
    """时钟回拨防护：到期判定取 max(系统日期, 历史水位)"""

    @property
    def _fp(self):
        return L.get_hardware_fingerprint()

    def setup_method(self):
        _use_test_key()
        self._tmpdir = tempfile.mkdtemp()
        L.LICENSE_FILE = os.path.join(self._tmpdir, "license.dat")
        L._HWM_FILE = os.path.join(self._tmpdir, ".lic_hwm")

    def teardown_method(self):
        for path in (L.LICENSE_FILE, L._HWM_FILE):
            if os.path.exists(path):
                os.remove(path)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _restore_pub_key()

    def _activate_timed(self, days):
        expiry = (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")
        ok, _ = L.activate(make_code(self._fp, expiry))
        assert ok
        return expiry

    def _patch_date(self, fake):
        self._orig_date = L.date
        L.date = fake

    def _restore_date(self):
        L.date = self._orig_date

    def test_activation_seeds_watermark(self):
        self._activate_timed(30)
        raw = json.loads(base64.b64decode(
            open(L.LICENSE_FILE, encoding="utf-8").read().strip()))
        assert raw.get("last_seen") == date.today().isoformat()
        assert L._read_hwm_file() == date.today()

    def test_rollback_blocked_by_watermark(self):
        """把系统时钟拨回 10 天：有效期不得因此延长"""
        self._activate_timed(30)
        rolled_back = date.today() - timedelta(days=10)
        try:
            self._patch_date(_FakeDate(rolled_back))
            status = L.get_license_status()
        finally:
            self._restore_date()
        assert status["activated"] is True
        assert status["days_remaining"] == 30   # 若被回拨蒙蔽会是 40

    def test_rollback_blocked_even_without_last_seen_field(self):
        """授权文件里的 last_seen 被删：独立水位文件仍然兜底"""
        self._activate_timed(30)
        raw = json.loads(base64.b64decode(
            open(L.LICENSE_FILE, encoding="utf-8").read().strip()))
        raw.pop("last_seen", None)
        with open(L.LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(base64.b64encode(json.dumps(raw).encode()).decode())
        rolled_back = date.today() - timedelta(days=10)
        try:
            self._patch_date(_FakeDate(rolled_back))
            status = L.get_license_status()
        finally:
            self._restore_date()
        assert status["days_remaining"] == 30

    def test_all_watermarks_removed_is_the_boundary(self):
        """双水位全被删（重装级清理）：回拨会得逞——防护的已知边界"""
        self._activate_timed(30)
        raw = json.loads(base64.b64decode(
            open(L.LICENSE_FILE, encoding="utf-8").read().strip()))
        raw.pop("last_seen", None)
        with open(L.LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(base64.b64encode(json.dumps(raw).encode()).decode())
        os.remove(L._HWM_FILE)
        rolled_back = date.today() - timedelta(days=10)
        try:
            self._patch_date(_FakeDate(rolled_back))
            status = L.get_license_status()
        finally:
            self._restore_date()
        assert status["days_remaining"] == 40   # 水位缺失 → 回拨生效（边界行为）

    def test_effective_today_takes_max(self):
        """_effective_today = max(系统日期, last_seen, 水位文件)"""
        self._activate_timed(30)
        today = date.today()
        near, nearer = today + timedelta(days=60), today + timedelta(days=120)
        data = {"last_seen": near.isoformat()}
        L._write_hwm_file(today + timedelta(days=30))
        assert L._effective_today(data) == near
        assert L._effective_today({}) == today + timedelta(days=30)
        assert L._effective_today(None) == today + timedelta(days=30)
        L._write_hwm_file(nearer)
        assert L._effective_today(data) == nearer

    def test_far_future_watermark_ignored(self):
        """水位超前系统日期 1 年以上视为脏数据（时钟曾跳到未来），不得让
        授权永久过期——这是"主板电池掉电即永久锁死"的复发防护"""
        self._activate_timed(30)
        L._write_hwm_file(date(2099, 1, 1))
        assert L._effective_today({}) == date.today()
        data = {"last_seen": "2099-01-01"}
        assert L._effective_today(data) == date.today()
        status = L.get_license_status()
        assert status["activated"] is True

    def test_corrupt_watermark_ignored(self):
        self._activate_timed(30)
        with open(L._HWM_FILE, "w", encoding="utf-8") as f:
            f.write("not-a-date[::-1]")
        assert L._read_hwm_file() is None
        data = {"last_seen": date.today().isoformat()}
        assert L._effective_today(data) == date.today()


class TestFingerprintDrift:
    """指纹漂移与整机拷贝：组件级绑定（guid 必须命中）+ 本机指纹验签"""

    @property
    def _fp(self):
        return L.get_hardware_fingerprint()

    def setup_method(self):
        _use_test_key()
        self._tmpdir = tempfile.mkdtemp()
        L.LICENSE_FILE = os.path.join(self._tmpdir, "license.dat")
        L._HWM_FILE = os.path.join(self._tmpdir, ".lic_hwm")

    def teardown_method(self):
        for path in (L.LICENSE_FILE, L._HWM_FILE):
            if os.path.exists(path):
                os.remove(path)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _restore_pub_key()

    def test_components_saved_on_activate(self):
        ok, _ = L.activate(make_code(self._fp))
        assert ok
        raw = json.loads(base64.b64decode(
            open(L.LICENSE_FILE, encoding="utf-8").read().strip()))
        comps = raw.get("components")
        assert isinstance(comps, dict) and len(comps) == 3
        for v in comps.values():
            assert len(v) == 32        # 哈希，非明文

    def test_single_drift_needs_reactivation(self):
        """指纹漂移（如改主机名）：不再静默放行，标记为需要重新激活

        旧实现是"组件 ≥2/3 匹配就用文件里的 fingerprint 验签"。但
        fingerprint / components 都未签名、可任意编辑，整机拷贝后只改
        components 即可在任意机器通过（见 test_components_swap_rejected）。
        改为：验签只认**本机**指纹；同机漂移时 activated=False 且
        needs_reactivation=True，UI 据此提示重新激活，而不是静默失效。
        """
        ok, _ = L.activate(make_code(self._fp))
        assert ok
        orig_fp = L.get_hardware_fingerprint
        try:
            L.get_hardware_fingerprint = lambda: "DEADBEEF" * 4
            # 仍识别为"同一台机器的授权文件"，只是不能继续验签通过
            assert L.load_license() is not None
            st = L.get_license_status()
            assert st["activated"] is False
            assert st["needs_reactivation"] is True
        finally:
            L.get_hardware_fingerprint = orig_fp

    def test_components_swap_rejected(self):
        """整机拷贝后把 components 换成目标机器的：不得通过（旧实现可绕过）"""
        ok, _ = L.activate(make_code(self._fp))
        assert ok
        raw = json.loads(base64.b64decode(
            open(L.LICENSE_FILE, encoding="utf-8").read().strip()))

        orig_fp = L.get_hardware_fingerprint
        orig_comps = L.get_hardware_components
        try:
            # 模拟在另一台机器上运行，并把组件快照换成那台机器的
            L.get_hardware_fingerprint = lambda: "DEADBEEF" * 4
            L.get_hardware_components = lambda: {
                "guid": "F" * 32, "mac": "E" * 32, "host": "D" * 32}
            raw["components"] = {
                "guid": "F" * 32, "mac": "E" * 32, "host": "D" * 32}
            with open(L.LICENSE_FILE, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(json.dumps(raw).encode()).decode())
            assert L.is_activated() is False
        finally:
            L.get_hardware_fingerprint = orig_fp
            L.get_hardware_components = orig_comps

    def test_wholesale_copy_rejected(self):
        """授权文件整体拷到别的机器：组件全不匹配 → 无效"""
        ok, _ = L.activate(make_code(self._fp))
        assert ok
        orig_fp = L.get_hardware_fingerprint
        orig_comps = L.get_hardware_components
        try:
            L.get_hardware_fingerprint = lambda: "DEADBEEF" * 4
            L.get_hardware_components = lambda: {
                "guid": "F" * 32, "mac": "E" * 32, "host": "D" * 32}
            assert L.load_license() is None
            assert L.is_activated() is False
        finally:
            L.get_hardware_fingerprint = orig_fp
            L.get_hardware_components = orig_comps

    def test_legacy_license_file_rejected(self):
        """旧格式授权文件（无 schema 字段）一律作废，需重新激活

        升级为"只认本机指纹验签"后，旧文件里的 fingerprint / components
        都未签名、可任意编辑，继续兼容等于保留绕过口子。产品侧已确认：
        升级到本版本后所有用户重新激活一次。
        """
        code = make_code(self._fp)
        legacy = {"fingerprint": self._fp, "code": code, "holder": ""}
        with open(L.LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(base64.b64encode(json.dumps(legacy).encode()).decode())
        assert L.load_license() is None

        st = L.get_license_status()
        assert st["activated"] is False
        # 有授权文件但不可用 → 提示重新激活，区别于从未激活
        assert st["needs_reactivation"] is True

    def test_current_schema_license_accepted(self):
        """当前 schema 的授权文件正常加载，且不需要重新激活"""
        ok, _ = L.activate(make_code(self._fp))
        assert ok
        assert L.load_license() is not None
        assert L.get_license_status()["needs_reactivation"] is False


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
                TestLicenseFileRoundTrip, TestClockRollback,
                TestFingerprintDrift):
        _run_class(cls)
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)