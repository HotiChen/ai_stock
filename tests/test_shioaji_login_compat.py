"""
tests/test_shioaji_login_compat.py — 跨 shioaji 版本的登入相容性

ensure_connected 呼叫 api.login(..., fetch_contract=True)，但 shioaji 1.7.4
的 login 簽名已經沒有這個參數：

    ['self', 'api_key', 'secret_key', 'subscribe_trade', 'receive_window',
     'force_refresh']

舊版接受、新版不接受。使用者目前跑的是舊版所以沒事，但升級當天登入會直接
掛掉——而且錯誤訊息是 TypeError，跟「憑證錯誤」長得完全不一樣，很容易誤判。

修法：先試帶參數，遇到 TypeError 再退回不帶。兩個版本都能用。
"""
from unittest.mock import MagicMock, patch

import pytest


class _NewSdkApi:
    """shioaji >= 1.7：login 不接受 fetch_contract。"""

    def __init__(self, *a, **k):
        self.logged_in_with = None

    def login(self, api_key, secret_key, **kw):
        if "fetch_contract" in kw:
            raise TypeError(
                "Shioaji.login() got an unexpected keyword argument 'fetch_contract'")
        self.logged_in_with = kw
        return True


class _OldSdkApi:
    """舊版：接受 fetch_contract。"""

    def __init__(self, *a, **k):
        self.logged_in_with = None

    def login(self, api_key, secret_key, **kw):
        self.logged_in_with = kw
        return True


def _connect(api_cls):
    import monitor_agent
    with patch.object(monitor_agent.sj, "Shioaji", api_cls):
        return monitor_agent.ensure_connected("k", "s", simulation=True)


class TestLoginCompat:
    def test_new_sdk_login_succeeds(self):
        """★ 新版 SDK 不接受 fetch_contract，必須自動退回不帶參數。"""
        api = _connect(_NewSdkApi)
        assert api is not None, "新版 SDK 上必須仍能登入"

    def test_old_sdk_still_gets_fetch_contract(self):
        """舊版行為不變——contracts 仍在登入時抓好。"""
        api = _connect(_OldSdkApi)
        assert api is not None
        assert api.logged_in_with.get("fetch_contract") is True

    def test_real_auth_failure_still_returns_none(self):
        """★ 真正的憑證錯誤不得被相容性退回機制吃掉。"""
        class _BadCreds:
            def __init__(self, *a, **k):
                pass

            def login(self, api_key, secret_key, **kw):
                raise Exception("invalid api key")

        assert _connect(_BadCreds) is None
