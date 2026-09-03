"""
tests/test_env_inline_comment.py — .env 行內註解不得被當成設定值

.env.example 第 33 行：

    USER_PASSWORD_HASH=              # bcrypt 雜湊（建議）；或用 USER_PASSWORD 明文

python-dotenv 對**空值**不會剝掉行內註解，實測結果：

    'USER_PASSWORD_HASH' = '# bcrypt 雜湊（建議）；或用 USER_PASSWORD 明文'

於是一整段中文註解變成了密碼雜湊。後果不是安全漏洞（verify_password 遇到
壞雜湊回 False，是 fail-closed），而是**靜默失敗**：

  1. 垃圾值是 truthy → USER_PASSWORD 明文備援永遠不會被採用
  2. verify_password 永遠回 False → 怎麼樣都登不進去
  3. 「login is disabled」的警告因為 pw_hash 非空而不會觸發 → 完全沒有線索

修法：讀取後淨化——去空白，並把以 # 開頭的值視為未設定。
"""
import pytest

# backend.app.deps 需要 fastapi；本容器未安裝，但使用者的 venv 有。
pytest.importorskip("fastapi")


class TestSanitizeEnvValue:
    def test_strips_whitespace(self):
        from backend.app.deps import _sanitize_env
        assert _sanitize_env("  abc  ") == "abc"

    def test_comment_only_becomes_none(self):
        """★ 這是實際發生的情況：值為空、後面接註解。"""
        from backend.app.deps import _sanitize_env
        assert _sanitize_env("# bcrypt 雜湊（建議）；或用 USER_PASSWORD 明文") is None

    def test_whitespace_then_comment_becomes_none(self):
        from backend.app.deps import _sanitize_env
        assert _sanitize_env("   # 註解") is None

    def test_empty_becomes_none(self):
        from backend.app.deps import _sanitize_env
        assert _sanitize_env("") is None
        assert _sanitize_env("   ") is None

    def test_none_stays_none(self):
        from backend.app.deps import _sanitize_env
        assert _sanitize_env(None) is None

    def test_real_bcrypt_hash_untouched(self):
        """★ 真實 bcrypt 雜湊不得被動到——誤傷比漏擋嚴重。"""
        from backend.app.deps import _sanitize_env
        h = "$2b$12$" + "a" * 53
        assert _sanitize_env(h) == h

    def test_value_containing_hash_not_at_start_kept(self):
        """密碼裡可以有 #，只有**開頭**是 # 才視為註解。"""
        from backend.app.deps import _sanitize_env
        assert _sanitize_env("pa#ssword") == "pa#ssword"


class TestPasswordHashLoading:
    def test_comment_value_falls_through_to_plaintext(self, monkeypatch):
        """★ 註解垃圾值不得擋住 USER_PASSWORD 備援。

        原本的行為：垃圾值 truthy → 直接回傳 → 明文密碼永遠用不到 →
        使用者設了密碼卻怎麼樣都登不進去，而且沒有任何錯誤訊息。
        """
        from backend.app import deps
        monkeypatch.setenv("USER_PASSWORD_HASH", "# bcrypt 雜湊（建議）")
        monkeypatch.setenv("USER_PASSWORD", "s3cret")
        h = deps._load_password_hash()
        assert h is not None
        assert not h.startswith("#")
        from backend.app.security import verify_password
        assert verify_password("s3cret", h) is True

    def test_comment_value_with_no_plaintext_disables_login_loudly(
            self, monkeypatch, caplog):
        """沒有任何有效密碼時要走「login is disabled」那條路並留下警告，
        而不是抱著一個永遠驗不過的垃圾雜湊默默失敗。"""
        from backend.app import deps
        monkeypatch.setenv("USER_PASSWORD_HASH", "   # 註解")
        monkeypatch.delenv("USER_PASSWORD", raising=False)
        import logging
        with caplog.at_level(logging.WARNING):
            assert deps._load_password_hash() is None
        assert any("login is disabled" in r.message.lower()
                   or "disabled" in r.message.lower() for r in caplog.records)

    def test_valid_hash_still_used(self, monkeypatch):
        from backend.app import deps
        from backend.app.security import hash_password
        h = hash_password("hello")
        monkeypatch.setenv("USER_PASSWORD_HASH", h)
        assert deps._load_password_hash() == h
