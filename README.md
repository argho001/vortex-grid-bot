# 🌀 VORTEX — Adaptive Grid Trading Bot

**V**olatility **O**ptimized **R**egime-**T**racked **EX**ecution

## Features

- 🔄 Adaptive grid spacing (works on any coin)
- 📊 Regime detection (trend, range, crash)
- 📈 **Both directions** — SHORT and LONG based on regime
- 🛡️ **ATR% filter** — only trades when market is calm (ATR < 0.15%)
- 📐 **Min R:R 1:2** — skips bad risk/reward trades
- 🎯 **Trailing stop** — trails SL after 1.2× ATR profit
- 💰 Dynamic balance allocation
- 🔗 Exchange position sync
- 📱 Telegram notifications + trade history
- 🔒 Risk management (1% per trade, 10x leverage)

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

## Telegram Commands

| Command | Description |
|---|---|
| `status` | Current positions and balance |
| `recent` | Last 5 trades with PnL, fees, duration |
| `help` | Show available commands |

## Recommended Coins

Based on 30-day multi-coin backtest with ATR% filter:

| Coin | Direction | Trades/Month | Win Rate | PnL/Month |
|---|---|---|---|---|
| **XRP** | SHORT + LONG | 18 | 72% | +$395 |
| **SOL** | SHORT + LONG | 12 | 67% | +$318 |
| **DOGE** | LONG preferred | 8 | 62% | +$46 |

Change in `bot.py`:
```python
COINS = ['XRPUSDT', 'SOLUSDT', 'DOGEUSDT']
```

## Strategy

### Regime Detection
Uses ADX + EMA alignment to detect market regime:

| Regime | ADX | EMA Alignment | Direction |
|---|---|---|---|
| `range` | < 20 | No alignment | SHORT + LONG |
| `up` | 20-30 | EMA9 > EMA21 > EMA55 | LONG only |
| `dn` | 20-30 | EMA9 < EMA21 < EMA55 | SHORT only |
| `strong_up` | > 30 | EMA9 > EMA21 > EMA55 | SHORT only |
| `strong_dn` | > 30 | EMA9 < EMA21 < EMA55 | LONG only |

### Entry Filters
1. **ATR% < 0.15%** — Only trade when volatility is low (calm market)
2. **R:R ≥ 1:2** — Only take trades with good risk/reward
3. **Direction match** — Only trade direction allowed by regime
4. **Exposure check** — Max 40% of balance at risk

### Exit Logic
1. **Trailing Stop** — After 1.2× ATR profit, SL trails behind price
2. **Take Profit** — Fixed TP based on ATR multiplier
3. **Stop Loss** — Fixed SL based on ATR multiplier
4. **Regime Exit** — Close if regime changes and direction not allowed

## Backtest Results (30 Days)

### With ATR% Filter vs Without

| Coin | No Filter | ATR% < 0.15 | Improvement |
|---|---|---|---|
| XRP | -$356 | **+$418** | +$774 |
| SOL | -$2,055 | **+$276** | +$2,332 |
| DOGE | -$1,144 | **+$46** | +$1,189 |
| ETH | -$2,014 | -$447 | +$1,566 |

### Combined (XRP + SOL + DOGE)
- **Trades/day:** 1.3
- **Trades/month:** 38
- **Win rate:** 67%
- **PnL/month:** +$759
- **Avg trade duration:** 20 minutes

## How It Works

1. **Fetches 5m klines** every 60 seconds
2. **Calculates indicators** (EMA, ADX, ATR, DI)
3. **Detects regime** (range, up, down, strong_up, strong_dn)
4. **Checks ATR%** — skips if too volatile
5. **Builds grid** around current price
6. **Monitors grid levels** for price crossings
7. **Enters trades** with proper direction and R:R
8. **Trails SL** after 1.2× ATR profit
9. **Saves trade history** for Telegram reporting
10. **Syncs with exchange** every loop

## Files

- `bot.py` — Main bot logic
- `web_status.py` — Health check server
- `index.html` — Dashboard
- `requirements.txt` — Python dependencies
- `trade_history.json` — Trade history (auto-created)
- `bot_state.json` — Bot state (auto-created)

## Configuration

Key parameters in `bot.py`:

```python
# Trading
COINS = ['XRPUSDT']
LEVERAGE = 10
RISK_PCT = 0.01          # 1% risk per trade

# Filters
ATR_PCT_MAX = 0.15       # Only trade when ATR% < 0.15%
MIN_RR = 2.0             # Minimum R:R ratio (1:2)

# Grid
GRID_LEVELS = 6
MAX_EXPOSURE = 0.40      # Max 40% of balance
TRAIL_ATR = 5.0          # Grid trailing threshold
REGIME_COOLDOWN = 4      # Candles to wait after regime change

# Regime Direction
REGIME_DIRECTION = {
    'range':      ['SHORT', 'LONG'],
    'up':         ['LONG'],
    'dn':         ['SHORT'],
    'strong_up':  ['SHORT'],
    'strong_dn':  ['LONG'],
}
```

## ⚠️ Disclaimer

This bot trades with real money. Use at your own risk.
Start with demo mode (`https://demo-fapi.binance.com`), then small amounts.
Past performance ≠ future results.
