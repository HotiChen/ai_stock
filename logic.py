from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import ta
from plotly.subplots import make_subplots

RED = "#ef5350"
GREEN = "#26a69a"


def normalize_kbars_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names and types from Shioaji kbars response."""
    df = df.copy()
    df.columns = [c.capitalize() if c.lower() != "ts" else "ts" for c in df.columns]
    df["ts"] = pd.to_datetime(df["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add MA5/MA20/MA60/RSI/MACD columns to a kbars DataFrame."""
    df = df.copy()
    close = df["Close"]
    df["MA5"] = ta.trend.SMAIndicator(close, window=5).sma_indicator()
    df["MA20"] = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    df["MA60"] = ta.trend.SMAIndicator(close, window=60).sma_indicator()
    df["RSI"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    macd = ta.trend.MACD(close)
    df["MACD"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_hist"] = macd.macd_diff()
    return df


def get_bar_colors(df: pd.DataFrame) -> list[str]:
    """Return red/green colors for volume bars based on price direction."""
    return [RED if c >= o else GREEN for c, o in zip(df["Close"], df["Open"])]


def get_yesterday_close(snapshot: Any) -> float:
    """Derive yesterday's closing price from today's snapshot."""
    return snapshot.close - snapshot.change_price


def build_chart(df: pd.DataFrame) -> go.Figure:
    """Build a 4-pane candlestick chart: K-line, Volume, RSI, MACD."""
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.5, 0.15, 0.175, 0.175],
        subplot_titles=("K 線 + 均線", "成交量", "RSI(14)", "MACD"),
    )

    # K 線
    fig.add_trace(go.Candlestick(
        x=df["ts"], open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="K線",
        increasing_line_color=RED,
        decreasing_line_color=GREEN,
    ), row=1, col=1)

    # 均線
    ma_styles = {"MA5": "#f39c12", "MA20": "#3498db", "MA60": "#e74c3c"}
    for ma, color in ma_styles.items():
        if ma in df.columns and df[ma].notna().any():
            fig.add_trace(go.Scatter(
                x=df["ts"], y=df[ma], name=ma,
                line=dict(color=color, width=1.2),
            ), row=1, col=1)

    # 成交量
    fig.add_trace(go.Bar(
        x=df["ts"], y=df["Volume"],
        marker_color=get_bar_colors(df),
        name="量", showlegend=False,
    ), row=2, col=1)

    # RSI
    if "RSI" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["ts"], y=df["RSI"],
            line=dict(color="#9b59b6", width=1.5), name="RSI",
        ), row=3, col=1)
        fig.add_hrect(y0=70, y1=100, fillcolor="red", opacity=0.05,
                      row=3, col=1, line_width=0)
        fig.add_hrect(y0=0, y1=30, fillcolor="green", opacity=0.05,
                      row=3, col=1, line_width=0)
        fig.add_hline(y=70, line_dash="dash", line_color="red",
                      line_width=1, row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green",
                      line_width=1, row=3, col=1)

    # MACD
    if "MACD" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["ts"], y=df["MACD"],
            line=dict(color="#3498db", width=1.2), name="MACD",
        ), row=4, col=1)
        fig.add_trace(go.Scatter(
            x=df["ts"], y=df["MACD_signal"],
            line=dict(color="#e67e22", width=1.2), name="Signal",
        ), row=4, col=1)
        hist_colors = [RED if v >= 0 else GREEN for v in df["MACD_hist"].fillna(0)]
        fig.add_trace(go.Bar(
            x=df["ts"], y=df["MACD_hist"],
            marker_color=hist_colors, name="Hist", showlegend=False,
        ), row=4, col=1)

    fig.update_layout(
        height=800,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17,17,17,1)",
        font=dict(color="white"),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        margin=dict(t=40, b=10),
    )
    for i in range(1, 5):
        fig.update_xaxes(gridcolor="rgba(100,100,100,0.2)", row=i, col=1)
        fig.update_yaxes(gridcolor="rgba(100,100,100,0.2)", row=i, col=1)

    return fig
