"""tests/test_dt_skip.py — Task 6: dt_skip / dt_skip_all 實際標記 skipped."""
from __future__ import annotations

from unittest.mock import patch


def _seed(path):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    positions = [
        DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                           target_price=None, stop_loss=None, dt_score=90, status="watching"),
        DaytradingPosition(code="2454", name="聯發科", entry_low=None, entry_high=None,
                           target_price=None, stop_loss=None, dt_score=80, status="watching"),
    ]
    save_daytrading_positions(positions, path=path)


class TestDtSkip:
    def test_skip_single_marks_skipped(self, tmp_path):
        from daytrading_monitor import load_daytrading_positions
        import telegram_bot
        path = str(tmp_path / "pos.json")
        _seed(path)
        with patch("telegram_bot.send_text") as mock_send:
            telegram_bot._handle_dt_skip("999", "2330", dt_path=path)
        positions = {p.code: p for p in load_daytrading_positions(path=path)}
        assert positions["2330"].status == "skipped"
        assert positions["2454"].status == "watching"   # 未受影響
        assert mock_send.called

    def test_skip_all_marks_all_watching(self, tmp_path):
        from daytrading_monitor import load_daytrading_positions
        import telegram_bot
        path = str(tmp_path / "pos.json")
        _seed(path)
        with patch("telegram_bot.send_text") as mock_send:
            telegram_bot._handle_dt_skip_all("999", dt_path=path)
        positions = load_daytrading_positions(path=path)
        assert all(p.status == "skipped" for p in positions)
        assert mock_send.called

    def test_skipped_not_bought_by_buy_all(self, tmp_path):
        """skip 後，watching 清單不再包含該檔 → dt_buy_all 不會買到它。"""
        from daytrading_monitor import load_daytrading_positions
        import telegram_bot
        path = str(tmp_path / "pos.json")
        _seed(path)
        with patch("telegram_bot.send_text"):
            telegram_bot._handle_dt_skip("999", "2330", dt_path=path)
        watching = [p.code for p in load_daytrading_positions(path=path)
                    if p.status == "watching"]
        assert "2330" not in watching
        assert "2454" in watching
