"""Tests for market_index.fetch_market_index_change()."""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from market_index import fetch_market_index_change


def test_returns_float():
    with patch("market_index.yf") as mock_yf:
        mock_ticker = MagicMock()
        df = pd.DataFrame({"Close": [18000.0, 18180.0]})
        mock_ticker.history.return_value = df
        mock_yf.Ticker.return_value = mock_ticker
        result = fetch_market_index_change()
    assert isinstance(result, float)


def test_returns_correct_change_pct():
    """prev=18000, today=18180 → +1.0%"""
    with patch("market_index.yf") as mock_yf:
        mock_ticker = MagicMock()
        df = pd.DataFrame({"Close": [18000.0, 18180.0]})
        mock_ticker.history.return_value = df
        mock_yf.Ticker.return_value = mock_ticker
        result = fetch_market_index_change()
    assert result == pytest.approx(1.0)


def test_fallback_to_zero_on_error():
    with patch("market_index.yf") as mock_yf:
        mock_yf.Ticker.side_effect = Exception("network error")
        result = fetch_market_index_change()
    assert result == 0.0


def test_fallback_to_zero_on_empty_data():
    with patch("market_index.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_yf.Ticker.return_value = mock_ticker
        result = fetch_market_index_change()
    assert result == 0.0
