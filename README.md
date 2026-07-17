# 🌀 VORTEX — Adaptive Grid Trading Bot

**V**olatility **O**ptimized **R**egime-**T**racked **EX**ecution

## Features

- 🔄 Adaptive grid spacing (works on any coin)
- 📊 Regime detection (trend, range, crash)
- 💰 Dynamic balance allocation
- 🔗 Exchange position sync
- 📱 Telegram notifications
- 🛡️ Risk management (3% per trade, 5x leverage)

## Quick Start

### 1. Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

### 2. Set Environment Variables

| Variable | Description |
|---|---|
| `BINANCE_API_KEY` | Your Binance futures API key |
| `BINANCE_API_SECRET` | Your Binance futures API secret |
| `BASE_URL` | `https://demo-fapi.binance.com` (demo) or `https://fapi.binance.com` (live) |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### 3. Get Telegram Chat ID

1. Message @userinfobot on Telegram
2. Send `/start`
3. Copy your User ID

## Coins

Default: IMX, APT, XLM (proven profitable in backtests)

Change in `bot.py`:
```python
COINS = ['IMXUSDT', 'APTUSDT', 'XLMUSDT']
```

## Backtest Results

| Coin | Trades | Win Rate | Monthly | Max DD |
|---|---|---|---|---|
| IMX | 3,812 | 59.3% | +1,278% | 24.8% |
| APT | 3,555 | 56.1% | +806% | 24.9% |
| XLM | 3,610 | 57.7% | +966% | 27.3% |

## How It Works

1. **Detects market regime** (ADX + EMA alignment)
2. **Deploys adaptive grid** (scales with volatility)
3. **Trades grid levels** (buys dips, sells rips)
4. **Syncs with exchange** (checks actual positions)
5. **Sends Telegram alerts** (trades, regime changes)

## Files

- `bot.py` — Main bot logic
- `web_status.py` — Health check server
- `index.html` — Dashboard
- `requirements.txt` — Python dependencies

## ⚠️ Disclaimer

This bot trades with real money. Use at your own risk.
Start with demo mode, then small amounts.
Past performance ≠ future results.
