from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import auth, dashboard, predict, daytrade, order, portfolio, journal, chat, backtest, report, scanner, settings, market
from .ws import daytrade as ws_daytrade
from .ws import market as ws_market
from .ws import chart as ws_chart

app = FastAPI(title="QUANT·AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(predict.router)
app.include_router(daytrade.router)
app.include_router(order.router)
app.include_router(portfolio.router)
app.include_router(journal.router)
app.include_router(chat.router)
app.include_router(backtest.router)
app.include_router(report.router)
app.include_router(scanner.router)
app.include_router(settings.router)
app.include_router(market.router)

# WebSocket routers
app.include_router(ws_daytrade.router)
app.include_router(ws_market.router)
app.include_router(ws_chart.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup() -> None:
    print("QUANT·AI backend started — port 8000")
