"""
ADAPTIVE GRID BOT — MULTI-COIN LIVE VERSION
Runs XRP, ADA, INJ, FIL simultaneously.
Each coin gets its own grid, regime detection, and trades.

Environment variables:
  BINANCE_API_KEY    - Binance futures API key
  BINANCE_API_SECRET - Binance futures API secret
  BASE_URL           - API URL (demo/testnet/live)
"""

import os, json, time, hmac, hashlib, logging, threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import requests
import numpy as np
import pandas as pd
from web_status import start_web_server

# Position monitor thread - checks every 2 seconds
position_monitor_running = True

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

# Load .env
if os.path.exists('.env'):
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

API_KEY = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
BASE_URL = os.environ.get('BASE_URL', 'https://demo-fapi.binance.com')

# Trading config
COINS = ['XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'ETHUSDT', 'SUIUSDT', 'AAVEUSDT']
LEVERAGE = 10
RISK_PCT = 0.01
CHECK_INTERVAL = 60
# Capital is dynamic - uses actual account balance at runtime

# Grid parameters
GRID_LEVELS = 6
MAX_EXPOSURE = 0.40
TRAIL_ATR = 5.0
REGIME_LOOKBACK = 96
REGIME_COOLDOWN = 4

# Optimized RR: 1:2 (range) / 1:3 (strong_up)
# Only SHORT in range and strong_up regimes
REGIME_PARAMS = {
    'range':      {'sl': 0.8, 'tp': 1.6, 'grid_sp': 0.3},
    'up':         {'sl': 1.2, 'tp': 2.4, 'grid_sp': 0.7},
    'dn':         {'sl': 1.2, 'tp': 2.4, 'grid_sp': 0.7},
    'strong_up':  {'sl': 1.5, 'tp': 4.5, 'grid_sp': 1.0},
    'strong_dn':  {'sl': 1.5, 'tp': 3.0, 'grid_sp': 1.0},
}

# Selective strategy: trade both directions
# SHORT in range, strong_up
# LONG in range, strong_dn
ALLOWED_REGIMES = {'range', 'strong_up', 'strong_dn'}
REGIME_DIRECTION = {
    'range':      ['SHORT', 'LONG'],
    'up':         ['LONG'],
    'dn':         ['SHORT'],
    'strong_up':  ['SHORT'],
    'strong_dn':  ['LONG'],
}

# ═══ NEW: Min R:R filter ═══
MIN_RR = 2.0  # Minimum reward:risk ratio (1:2)

# ═══ NEW: Trade history for Telegram "Recent" command ═══
TRADE_HISTORY_FILE = 'trade_history.json'
MAX_TRADE_HISTORY = 100
MAX_CONCURRENT_POSITIONS = 3  # Max open positions across all coins

KLINE_INTERVAL = '5m'

STATE_FILE = 'bot_state.json'
LOG_FILE = 'bot.log'

# Telegram config
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger('adaptive_grid')

# ═══════════════════════════════════════════════════════════════
# TELEGRAM NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

def send_telegram(message):
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }, timeout=10)
    except Exception as e:
        log.error(f'Telegram error: {e}')

def get_status_message():
    """Generate status message for Telegram."""
    exchange_positions = get_all_positions()
    balance = get_balance()
    
    msg = '📊 <b>VORTEX STATUS</b>\n'
    msg += '═' * 30 + '\n\n'
    
    # Balance
    if balance:
        msg += f'💰 <b>Balance:</b> ${balance:,.2f}\n\n'
    
    # Open positions
    if exchange_positions:
        msg += f'📈 <b>Open Positions ({len(exchange_positions)}):</b>\n\n'
        total_pnl = 0
        for sym, pos in exchange_positions.items():
            pnl = pos['pnl']
            total_pnl += pnl
            emoji = '🟢' if pnl >= 0 else '🔴'
            pnl_pct = (pnl / (pos['margin'] * LEVERAGE)) * 100 if pos['margin'] > 0 else 0
            msg += f'{emoji} <b>{sym}</b>\n'
            msg += f'   Side: {pos["side"]}\n'
            msg += f'   Entry: ${pos["entry"]:.4f}\n'
            msg += f'   Qty: {pos["qty"]}\n'
            msg += f'   PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n\n'
        
        total_emoji = '🟢' if total_pnl >= 0 else '🔴'
        msg += f'{total_emoji} <b>Total PnL:</b> ${total_pnl:+.2f}\n'
    else:
        msg += '📭 <b>No open positions</b>\n'
    
    # Grid status
    msg += '\n📊 <b>Grid Status:</b>\n'
    for sym in COINS:
        if sym in exchange_positions:
            msg += f'  {sym}: Active (position open)\n'
        else:
            msg += f'  {sym}: Monitoring\n'
    
    msg += f'\n⏰ {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
    
    return msg

def save_trade_history(trade):
    """Save a completed trade to history file."""
    try:
        history = []
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE) as f:
                history = json.load(f)
        history.append(trade)
        # Keep only last N trades
        if len(history) > MAX_TRADE_HISTORY:
            history = history[-MAX_TRADE_HISTORY:]
        with open(TRADE_HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save trade history: {e}')

def get_recent_trades(count=5):
    """Get recent trades from history."""
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE) as f:
                history = json.load(f)
            return history[-count:]
    except:
        pass
    return []

def get_recent_trades_message():
    """Generate recent trades message for Telegram."""
    trades = get_recent_trades(5)
    if not trades:
        return '📭 <b>No recent trades</b>'
    
    msg = '📋 <b>RECENT TRADES (Last 5)</b>\n'
    msg += '═' * 30 + '\n\n'
    
    total_pnl = 0
    total_fees = 0
    
    for i, t in enumerate(trades, 1):
        pnl = t.get('pnl', 0)
        fees = t.get('fees', 0)
        total_pnl += pnl
        total_fees += fees
        
        emoji = '🟢' if pnl >= 0 else '🔴'
        side = t.get('side', '?')
        entry = t.get('entry', 0)
        exit_price = t.get('exit', 0)
        qty = t.get('qty', 0)
        reason = t.get('reason', '?')
        regime = t.get('regime', '?')
        duration = t.get('duration_min', 0)
        
        msg += f'{emoji} <b>Trade {i}</b>\n'
        msg += f'   {side} {qty:.0f} XRP\n'
        msg += f'   Entry: ${entry:.4f} → Exit: ${exit_price:.4f}\n'
        msg += f'   PnL: <b>${pnl:+.2f}</b> | Fees: ${fees:.2f}\n'
        msg += f'   Duration: {duration:.0f}min | {reason}\n'
        msg += f'   Regime: {regime}\n\n'
    
    msg += '═' * 30 + '\n'
    msg += f'📊 Total PnL: <b>${total_pnl:+.2f}</b>\n'
    msg += f'💸 Total Fees: ${total_fees:.2f}'
    
    return msg

def handle_telegram_update(update):
    """Handle incoming Telegram message."""
    if 'message' not in update:
        return
    msg = update['message']
    text = msg.get('text', '').strip().lower()
    chat_id = str(msg['chat']['id'])
    
    # Only respond to the configured chat
    if chat_id != TELEGRAM_CHAT_ID:
        return
    
    if text in ['update', '/update', 'status', '/status']:
        status = get_status_message()
        send_telegram(status)
    elif text in ['recent', '/recent', 'trades', '/trades']:
        recent = get_recent_trades_message()
        send_telegram(recent)
    elif text in ['help', '/help', 'start', '/start']:
        help_msg = (
            '🤖 <b>VORTEX Bot Commands</b>\n\n'
            '📊 <b>update</b> — Get current status\n'
            '📊 <b>status</b> — Same as update\n'
            '📋 <b>recent</b> — Last 5 trades\n'
            '❓ <b>help</b> — Show this message\n'
        )
        send_telegram(help_msg)

# Track last processed Telegram update ID
telegram_last_update_id = 0

def poll_telegram():
    """Poll Telegram for new messages."""
    global telegram_last_update_id
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates'
        params = {'offset': telegram_last_update_id + 1, 'timeout': 1}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if data.get('ok') and data.get('result'):
            for update in data['result']:
                telegram_last_update_id = update['update_id']
                handle_telegram_update(update)
    except Exception as e:
        pass  # Silent fail for polling

# ═══════════════════════════════════════════════════════════════
# POSITION MONITOR THREAD — Checks every 2 seconds
# ═══════════════════════════════════════════════════════════════

# Shared state between main loop and monitor thread
tracked_positions = {}  # {symbol: {side, entry, qty, sl, tp}}
position_lock = threading.Lock()

def close_position_market(symbol, side, qty):
    """Close a position with market order."""
    close_side = 'SELL' if side == 'LONG' else 'BUY'
    result = api_request('POST', '/fapi/v1/order', {
        'symbol': symbol,
        'side': close_side,
        'type': 'MARKET',
        'quantity': qty
    }, signed=True)
    return result

def position_monitor():
    """Thread that monitors positions every 2 seconds and acts as SL/TP."""
    log.info('Position monitor thread started (checking every 2s)')
    
    while position_monitor_running:
        try:
            with position_lock:
                positions_to_check = dict(tracked_positions)
            
            for symbol, pos in positions_to_check.items():
                try:
                    # Get real-time price (faster than 1m kline)
                    ticker = api_request('GET', '/fapi/v1/ticker/price', {'symbol': symbol})
                    if not ticker or 'price' not in ticker:
                        continue
                    price = float(ticker['price'])
                    
                    sl_hit = False
                    tp_hit = False
                    
                    # ═══ TRAILING STOP ═══
                    # Activate when price reaches 50% of TP distance
                    atr_e = pos.get('atr_at_entry', 0.001)
                    original_sl = pos.get('original_sl', pos['sl'])
                    trailing_active = False
                    
                    if pos['side'] == 'LONG':
                        tp_dist = pos['tp'] - pos['entry']
                        halfway = pos['entry'] + (tp_dist * 0.5)
                        if price >= halfway:
                            trailing_active = True
                            # Price reached 50% of TP → trail SL
                            new_sl = max(pos['sl'], price - atr_e * 1.2)
                            if new_sl > pos['sl']:
                                pos['sl'] = new_sl
                                log.info(f'{symbol} TRAIL SL (LONG): new SL={new_sl:.4f}')
                        sl_hit = price <= pos['sl']
                        tp_hit = price >= pos['tp']
                    else:  # SHORT
                        tp_dist = pos['entry'] - pos['tp']
                        halfway = pos['entry'] - (tp_dist * 0.5)
                        if price <= halfway:
                            trailing_active = True
                            # Price reached 50% of TP → trail SL
                            new_sl = min(pos['sl'], price + atr_e * 1.2)
                            if new_sl < pos['sl']:
                                pos['sl'] = new_sl
                                log.info(f'{symbol} TRAIL SL (SHORT): new SL={new_sl:.4f}')
                        sl_hit = price >= pos['sl']
                        tp_hit = price <= pos['tp']
                    
                    if sl_hit:
                        entry_price = pos.get('entry', 0)
                        pnl = (entry_price - price) * pos['qty'] if pos['side'] == 'SHORT' else (price - entry_price) * pos['qty']
                        fees = pos['qty'] * entry_price * 0.0004 + pos['qty'] * price * 0.0004
                        pnl -= fees
                        
                        # Check if SL was trailed (different from original)
                        sl_was_trailed = pos['sl'] != original_sl
                        
                        if sl_was_trailed:
                            # TRAILING SL HIT — profitable exit
                            log.info(f'🟢 TRAIL SL HIT: {symbol} {pos["side"]} @ {price:.4f} PnL=${pnl:+.2f}')
                            send_telegram(
                                f'🟢 <b>TRAIL SL HIT</b>\n'
                                f'{symbol} {pos["side"]}\n'
                                f'Entry: ${entry_price:.4f}\n'
                                f'Exit: ${price:.4f}\n'
                                f'SL: ${pos["sl"]:.4f} (trailed)\n'
                                f'PnL: <b>${pnl:+.2f}</b>\n'
                                f'Fees: ${fees:.2f}'
                            )
                        else:
                            # NORMAL SL HIT — loss
                            log.info(f'🔴 SL HIT: {symbol} {pos["side"]} @ {price:.4f} PnL=${pnl:+.2f}')
                            send_telegram(
                                f'🔴 <b>SL HIT</b>\n'
                                f'{symbol} {pos["side"]}\n'
                                f'Entry: ${entry_price:.4f}\n'
                                f'Exit: ${price:.4f}\n'
                                f'SL: ${pos["sl"]:.4f}\n'
                                f'PnL: <b>${pnl:+.2f}</b>\n'
                                f'Fees: ${fees:.2f}'
                            )
                        
                        result = close_position_market(symbol, pos['side'], pos['qty'])
                        if result:
                            log.info(f'{symbol} position closed via SL')
                            save_trade_history({
                                'symbol': symbol, 'side': pos['side'], 'entry': entry_price,
                                'exit': price, 'qty': pos['qty'], 'pnl': pnl, 'fees': fees,
                                'reason': 'TRAIL_SL' if sl_was_trailed else 'SL',
                                'regime': pos.get('regime', '?'),
                                'time': datetime.now(timezone.utc).isoformat(),
                                'duration_min': (datetime.now(timezone.utc) - datetime.fromisoformat(pos.get('entry_time', datetime.now(timezone.utc).isoformat()))).total_seconds() / 60
                            })
                        with position_lock:
                            if symbol in tracked_positions:
                                del tracked_positions[symbol]
                    
                    elif tp_hit:
                        entry_price = pos.get('entry', 0)
                        pnl = (entry_price - price) * pos['qty'] if pos['side'] == 'SHORT' else (price - entry_price) * pos['qty']
                        fees = pos['qty'] * entry_price * 0.0004 + pos['qty'] * price * 0.0004
                        pnl -= fees
                        log.info(f'🟢 TP HIT: {symbol} {pos["side"]} @ {price:.4f} PnL=${pnl:+.2f}')
                        send_telegram(
                            f'🟢 <b>TP HIT</b>\n'
                            f'{symbol} {pos["side"]}\n'
                            f'Entry: ${entry_price:.4f}\n'
                            f'Exit: ${price:.4f}\n'
                            f'TP: ${pos["tp"]:.4f}\n'
                            f'PnL: <b>${pnl:+.2f}</b>\n'
                            f'Fees: ${fees:.2f}'
                        )
                        result = close_position_market(symbol, pos['side'], pos['qty'])
                        if result:
                            log.info(f'{symbol} position closed via TP')
                            save_trade_history({
                                'symbol': symbol, 'side': pos['side'], 'entry': entry_price,
                                'exit': price, 'qty': pos['qty'], 'pnl': pnl, 'fees': fees,
                                'reason': 'TP', 'regime': pos.get('regime', '?'),
                                'time': datetime.now(timezone.utc).isoformat(),
                                'duration_min': (datetime.now(timezone.utc) - datetime.fromisoformat(pos.get('entry_time', datetime.now(timezone.utc).isoformat()))).total_seconds() / 60
                            })
                        with position_lock:
                            if symbol in tracked_positions:
                                del tracked_positions[symbol]
                
                except Exception as e:
                    log.error(f'Position monitor error for {symbol}: {e}')
            
            time.sleep(2)  # Check every 2 seconds
            
        except Exception as e:
            log.error(f'Position monitor thread error: {e}')
            time.sleep(5)

# ═══════════════════════════════════════════════════════════════
# BINANCE API
# ═══════════════════════════════════════════════════════════════

def sign(params):
    return hmac.new(API_SECRET.encode(), urlencode(params).encode(), hashlib.sha256).hexdigest()

def api_request(method, path, params=None, signed=False):
    if params is None: params = {}
    if signed:
        params['timestamp'] = int(time.time() * 1000)
        params['signature'] = sign(params)
    url = f'{BASE_URL}{path}'
    headers = {'X-MBX-APIKEY': API_KEY}
    try:
        if method == 'GET': r = requests.get(url, params=params, headers=headers, timeout=10)
        elif method == 'POST': r = requests.post(url, params=params, headers=headers, timeout=10)
        elif method == 'DELETE': r = requests.delete(url, params=params, headers=headers, timeout=10)
        else: return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f'API {method} {path}: {e}')
        return None

def get_balance():
    data = api_request('GET', '/fapi/v2/account', signed=True)
    if data and 'assets' in data:
        for a in data['assets']:
            if a['asset'] == 'USDT':
                return float(a['availableBalance'])
    return None

def get_klines(symbol, interval='15m', limit=200):
    data = api_request('GET', '/fapi/v1/klines', params={'symbol': symbol, 'interval': interval, 'limit': limit})
    if not data: return pd.DataFrame()
    df = pd.DataFrame(data, columns=['ts','o','h','l','c','v','ct','qv','n','tbb','tbq','ig'])
    df = df[['ts','o','h','l','c','v']].astype(float)
    df['dt'] = pd.to_datetime(df['ts'], unit='ms')
    df.set_index('dt', inplace=True)
    return df

def get_lot_step(symbol):
    data = api_request('GET', '/fapi/v1/exchangeInfo', params={'symbol': symbol})
    if data and 'symbols' in data:
        for s in data['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        return float(f['stepSize'])
    return 0.001

def set_leverage(symbol):
    api_request('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': LEVERAGE}, signed=True)

def get_all_positions():
    """Check all open positions on the exchange. Returns dict of {symbol: position_data}."""
    data = api_request('GET', '/fapi/v2/positionRisk', signed=True)
    if not data: return {}
    positions = {}
    for p in data:
        amt = float(p.get('positionAmt', 0))
        if abs(amt) > 0:
            positions[p['symbol']] = {
                'side': 'LONG' if amt > 0 else 'SHORT',
                'qty': abs(amt),
                'entry': float(p['entryPrice']),
                'pnl': float(p['unRealizedProfit']),
                'margin': abs(amt) * float(p['entryPrice']) / LEVERAGE
            }
    return positions

def get_open_orders(symbol=None):
    """Check all open orders. Returns list of orders."""
    params = {}
    if symbol: params['symbol'] = symbol
    data = api_request('GET', '/fapi/v1/openOrders', params=params, signed=True)
    return data if data else []

def place_order(symbol, side, qty, sl_price, tp_price, lot_step, atr_at_entry=0.001):
    qty = round(int(qty / lot_step) * lot_step, 8)
    if qty <= 0: return False
    
    order = api_request('POST', '/fapi/v1/order', {
        'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': qty
    }, signed=True)
    
    if not order or 'orderId' not in order:
        log.error(f'{symbol} entry failed: {order}')
        return False
    
    log.info(f'{symbol} ENTRY: {side} {qty} @ market')
    send_telegram(f'🟢 <b>NEW TRADE</b>\n{symbol} {side} {qty}\nSL: {sl_price:.4f} | TP: {tp_price:.4f}')
    
    # Add to position monitor thread (acts as SL/TP)
    pos_side = 'LONG' if side == 'BUY' else 'SHORT'
    with position_lock:
        tracked_positions[symbol] = {
            'side': pos_side,
            'entry': float(order.get('avgPrice', 0)),
            'qty': qty,
            'sl': sl_price,
            'tp': tp_price,
            'original_sl': sl_price,
            'atr_at_entry': atr_at_entry,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'regime': 'unknown'
        }
    log.info(f'{symbol} added to position monitor: SL={sl_price:.4f}, TP={tp_price:.4f}')
    
    sl_side = 'SELL' if side == 'BUY' else 'BUY'
    sl = api_request('POST', '/fapi/v1/order', {
        'symbol': symbol, 'side': sl_side, 'type': 'STOP_MARKET',
        'quantity': qty, 'stopPrice': round(sl_price, 4), 'reduceOnly': 'true'
    }, signed=True)
    
    tp = api_request('POST', '/fapi/v1/order', {
        'symbol': symbol, 'side': sl_side, 'type': 'TAKE_PROFIT_MARKET',
        'quantity': qty, 'stopPrice': round(tp_price, 4), 'reduceOnly': 'true'
    }, signed=True)
    
    if not sl: log.warning(f'{symbol} SL failed')
    if not tp: log.warning(f'{symbol} TP failed')
    
    log.info(f'{symbol} SL={sl_price:.4f} TP={tp_price:.4f}')
    return True

def cancel_all_orders(symbol):
    api_request('DELETE', '/fapi/v1/allOpenOrders', {'symbol': symbol}, signed=True)

# ═══════════════════════════════════════════════════════════════
# INDICATORS & REGIME
# ═══════════════════════════════════════════════════════════════

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def calc_atr(h, l, c, p=14):
    tr = pd.DataFrame({'hl':h-l, 'hc':abs(h-c.shift(1)), 'lc':abs(l-c.shift(1))}).max(axis=1)
    return tr.rolling(p).mean()

def prepare(df):
    c, h, l, v = df['c'], df['h'], df['l'], df['v']
    df['ema9'] = ema(c, 9); df['ema21'] = ema(c, 21); df['ema55'] = ema(c, 55)
    df['atr'] = calc_atr(h, l, c)
    pdm = h.diff(); mdm = -l.diff()
    pdm = pdm.where((pdm > mdm) & (pdm > 0), 0)
    mdm = mdm.where((mdm > pdm) & (mdm > 0), 0)
    a14 = calc_atr(h, l, c, 14)
    pdi = 100 * ema(pdm, 14) / a14.replace(0, 0.001)
    mdi = 100 * ema(mdm, 14) / a14.replace(0, 0.001)
    dx = 100 * abs(pdi - mdi) / (pdi + mdi).replace(0, 0.001)
    df['adx'] = ema(dx, 14); df['pdi'] = pdi; df['mdi'] = mdi
    df['ea_up'] = (df['ema9'] > df['ema21']) & (df['ema21'] > df['ema55'])
    df['ea_dn'] = (df['ema9'] < df['ema21']) & (df['ema21'] < df['ema55'])
    return df.dropna()

def detect_regime(row):
    adx = row['adx']
    if adx > 30:
        if row['ea_up'] and row['pdi'] > row['mdi']: return 'strong_up'
        if row['ea_dn'] and row['mdi'] > row['pdi']: return 'strong_dn'
    if adx > 20:
        if row['ea_up'] and row['pdi'] > row['mdi']: return 'up'
        if row['ea_dn'] and row['mdi'] > row['pdi']: return 'dn'
    return 'range'

# ═══════════════════════════════════════════════════════════════
# GRID ENGINE (per coin)
# ═══════════════════════════════════════════════════════════════

class CoinGrid:
    def __init__(self, symbol, lot_step):
        self.symbol = symbol
        self.lot_step = lot_step
        self.grid_orders = []
        self.grid_center = 0
        self.current_regime = 'range'
        self.positions = []
        self.cooldown_until = 0
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0

    def make_grid(self, price, atr_v, regime):
        self.current_regime = regime
        self.grid_center = price
        p = REGIME_PARAMS.get(regime, REGIME_PARAMS['range'])
        
        # ATR-based spacing
        sp_atr = atr_v * p['grid_sp']
        
        # Percentage-based spacing (minimum 0.5% of price)
        sp_pct = price * 0.005
        
        # Use the larger of the two — ensures grid works for all coins
        sp = max(sp_atr, sp_pct)
        
        if regime == 'range': lo, hi = price - sp*GRID_LEVELS/2, price + sp*GRID_LEVELS/2
        elif regime == 'up': lo = price-sp*(GRID_LEVELS*0.7); hi = price+sp*(GRID_LEVELS*0.3)
        elif regime == 'dn': lo = price-sp*(GRID_LEVELS*0.3); hi = price+sp*(GRID_LEVELS*0.7)
        elif regime == 'strong_up': lo = price-sp*(GRID_LEVELS*0.8); hi = price+sp*(GRID_LEVELS*0.2)
        elif regime == 'strong_dn': lo = price-sp*(GRID_LEVELS*0.2); hi = price+sp*(GRID_LEVELS*0.8)
        else: return
        
        self.grid_orders = []
        for i in range(GRID_LEVELS):
            lp = lo + (hi-lo)*i/(GRID_LEVELS-1)
            self.grid_orders.append({'price': lp, 'type': 'BUY' if lp < price else 'SELL', 'filled': False})
        
        log.info(f'{self.symbol} Grid: {regime} | center={price:.4f} | range=[{lo:.4f}, {hi:.4f}]')

    def trail_grid(self, price, atr_v):
        if self.current_regime in ('up', 'strong_up') and price > self.grid_center + atr_v * TRAIL_ATR:
            self.make_grid(price, atr_v, self.current_regime)
        elif self.current_regime in ('dn', 'strong_dn') and price < self.grid_center - atr_v * TRAIL_ATR:
            self.make_grid(price, atr_v, self.current_regime)

    def check_grid_hit(self, price, prev_price):
        signals = []
        for o in self.grid_orders:
            if o['filled']: continue
            if o['type'] == 'BUY' and prev_price > o['price'] and price <= o['price']:
                signals.append(('BUY', o['price']))
                o['filled'] = True
            elif o['type'] == 'SELL' and prev_price < o['price'] and price >= o['price']:
                signals.append(('SELL', o['price']))
                o['filled'] = True
        return signals

    def get_exposure(self):
        bal = get_balance()
        if not bal or bal <= 0: return 1.0
        total = sum(p.get('value', 0) for p in self.positions)
        return total / bal

# ═══════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def save_state(grids):
    state = {}
    for sym, grid in grids.items():
        state[sym] = {
            'grid_orders': grid.grid_orders,
            'grid_center': grid.grid_center,
            'current_regime': grid.current_regime,
            'positions': grid.positions,
            'cooldown_until': grid.cooldown_until,
            'total_trades': grid.total_trades,
            'wins': grid.wins,
            'losses': grid.losses,
            'total_pnl': grid.total_pnl,
        }
    state['_meta'] = {'last_update': datetime.now(timezone.utc).isoformat(), 'coins': COINS}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def load_state(grids):
    if not os.path.exists(STATE_FILE): return
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        for sym, grid in grids.items():
            if sym in state:
                s = state[sym]
                grid.grid_orders = s.get('grid_orders', [])
                grid.grid_center = s.get('grid_center', 0)
                grid.current_regime = s.get('current_regime', 'range')
                grid.positions = s.get('positions', [])
                grid.cooldown_until = s.get('cooldown_until', 0)
                grid.total_trades = s.get('total_trades', 0)
                grid.wins = s.get('wins', 0)
                grid.losses = s.get('losses', 0)
                grid.total_pnl = s.get('total_pnl', 0)
        log.info('State loaded')
    except Exception as e:
        log.error(f'State load error: {e}')

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    log.info('=' * 60)
    log.info('ADAPTIVE GRID BOT — MULTI-COIN')
    log.info(f'Coins: {", ".join(COINS)}')
    log.info(f'Leverage: {LEVERAGE}x | Risk: {RISK_PCT*100}%')
    log.info(f'API: {BASE_URL}')
    log.info('=' * 60)
    
    if not API_KEY or not API_SECRET:
        log.error('Missing API credentials')
        return
    
    # Check balance
    balance = get_balance()
    if balance:
        log.info(f'Account balance: ${balance:.2f}')
    
    # Initialize grids (capital is dynamic - uses actual balance)
    grids = {}
    for sym in COINS:
        lot_step = get_lot_step(sym)
        grids[sym] = CoinGrid(sym, lot_step)
        set_leverage(sym)
        log.info(f'{sym} initialized (lot_step={lot_step})')
    
    # Load state
    load_state(grids)
    
    # Initialize grids with current data
    for sym, grid in grids.items():
        if not grid.grid_orders:
            df = get_klines(sym, KLINE_INTERVAL, 200)
            if df.empty:
                log.error(f'{sym} failed to fetch data')
                continue
            df = prepare(df)
            row = df.iloc[-1]
            regime = detect_regime(row)
            grid.make_grid(float(row['c']), float(row['atr']), regime)
    
    prev_prices = {}
    loop_count = 0
    
    log.info('Starting main loop...')
    
    while True:
        try:
            loop_count += 1
            
            # ═══ POLL TELEGRAM FOR COMMANDS ═══
            poll_telegram()
            
            # ═══ CHECK ACTUAL POSITIONS ON EXCHANGE ═══
            exchange_positions = get_all_positions()
            exchange_orders = {}
            
            for sym, grid in grids.items():
                # Check if exchange has position for this coin
                if sym in exchange_positions:
                    ep = exchange_positions[sym]
                    # Sync: if bot doesn't know about this position, warn ONCE and add to monitor
                    if not grid.positions and sym not in tracked_positions:
                        log.warning(f'{sym} has open position on exchange but bot unaware! Side: {ep["side"]}, Qty: {ep["qty"]}, Entry: {ep["entry"]}')
                        send_telegram(f'⚠️ <b>SYNC WARNING</b>\n{sym} has open position on exchange\nBot unaware! Side: {ep["side"]}\nEntry: ${ep["entry"]:.4f}\nQty: {ep["qty"]}\n\nAdding to position monitor (SL/TP every 2s)')
                        # Add to position monitor thread
                        sl = ep['entry'] * (1 + 0.012) if ep['side'] == 'SHORT' else ep['entry'] * (1 - 0.012)
                        tp = ep['entry'] * (1 - 0.015) if ep['side'] == 'SHORT' else ep['entry'] * (1 + 0.015)
                        with position_lock:
                            tracked_positions[sym] = {
                                'side': ep['side'],
                                'entry': ep['entry'],
                                'qty': ep['qty'],
                                'sl': sl,
                                'tp': tp
                            }
                        log.info(f'{sym} added to position monitor: SL={sl:.4f}, TP={tp:.4f}')
                else:
                    # Exchange has no position, clean up bot state
                    if grid.positions:
                        log.info(f'{sym} position closed on exchange (SL/TP hit). Cleaning up bot state.')
                        send_telegram(f'🔴 <b>POSITION CLOSED</b>\n{sym} SL/TP hit on exchange')
                        grid.positions = []
                    # Remove from position monitor when position closes
                    with position_lock:
                        if sym in tracked_positions:
                            del tracked_positions[sym]
                
                # Check open orders
                orders = get_open_orders(sym)
                exchange_orders[sym] = orders
            
            for sym, grid in grids.items():
                try:
                    # Fetch data
                    df = get_klines(sym, KLINE_INTERVAL, 200)
                    if df.empty: continue
                    df = prepare(df)
                    row = df.iloc[-1]
                    price = float(row['c'])
                    atr_v = float(row['atr'])
                    regime = detect_regime(row)
                    prev_price = prev_prices.get(sym, price)
                    
                    # Log every 10 loops
                    if loop_count % 10 == 0:
                        pos_info = 'None'
                        if sym in exchange_positions:
                            ep = exchange_positions[sym]
                            pos_info = f'{ep["side"]} {ep["qty"]} @ {ep["entry"]:.4f} (PnL: {ep["pnl"]:.2f})'
                        log.info(f'{sym} | Price: {price:.4f} | Regime: {regime} | Pos: {pos_info} | Orders: {len(exchange_orders.get(sym, []))} | Trades: {grid.total_trades}')
                    
                    # Regime change
                    if regime != grid.current_regime:
                        log.info(f'{sym} REGIME: {grid.current_regime} → {regime}')
                        
                        # Human-readable regime explanation
                        regime_info = {
                            'range':      '📊 RANGING — Price moving sideways. Bot: SHORT + LONG',
                            'up':         '📈 UPTREND — Price going UP. Bot: LONG only',
                            'dn':         '📉 DOWNTREND — Price going DOWN. Bot: SHORT only',
                            'strong_up':  '🚀 STRONG UP — Price pumping hard. Bot: SHORT (fading rally)',
                            'strong_dn':  '💥 STRONG DOWN — Price dumping hard. Bot: LONG (fading drop)',
                        }
                        info = regime_info.get(regime, 'Unknown regime')
                        
                        send_telegram(
                            f'📊 <b>REGIME CHANGE</b>\n'
                            f'{sym}: {grid.current_regime} → {regime}\n\n'
                            f'{info}'
                        )
                        grid.cooldown_until = loop_count + REGIME_COOLDOWN
                        grid.make_grid(price, atr_v, regime)
                        
                        # Close positions only if direction not allowed in new regime
                        to_close = []
                        for j, pos in enumerate(grid.positions):
                            allowed_sides = REGIME_DIRECTION.get(regime, [])
                            if pos['side'] not in allowed_sides:
                                to_close.append(j)
                        for j in sorted(to_close, reverse=True):
                            pos = grid.positions.pop(j)
                            close_side = 'SELL' if pos['side'] == 'LONG' else 'BUY'
                            cancel_all_orders(sym)
                            api_request('POST', '/fapi/v1/order', {
                                'symbol': sym, 'side': close_side, 'type': 'MARKET',
                                'quantity': round(pos['qty'], 8)
                            }, signed=True)
                            log.info(f'{sym} Closed {pos["side"]} due to regime change')
                    
                    # Trail grid
                    grid.trail_grid(price, atr_v)
                    
                    # Cooldown
                    if loop_count < grid.cooldown_until:
                        prev_prices[sym] = price
                        continue
                    
                    # Check grid hits
                    signals = grid.check_grid_hit(price, prev_price)
                    
                    for sig_type, sig_price in signals:
                        # Exposure check
                        if grid.get_exposure() >= MAX_EXPOSURE: continue
                        
                        # ═══ Only 1 position per coin ═══
                        if len(grid.positions) >= 1: continue
                        
                        # Direction check — use REGIME_DIRECTION
                        side = 'LONG' if sig_type == 'BUY' else 'SHORT'
                        allowed_sides = REGIME_DIRECTION.get(regime, [])
                        if side not in allowed_sides:
                            continue
                        
                        # DYNAMIC BALANCE ALLOCATION
                        # Count open positions across ALL coins
                        total_open_positions = sum(len(g.positions) for g in grids.values())
                        if total_open_positions >= MAX_CONCURRENT_POSITIONS: continue  # max 3 at a time
                        available_slots = MAX_CONCURRENT_POSITIONS - total_open_positions
                        if available_slots <= 0: continue
                        
                        # Get current balance
                        balance = get_balance()
                        if not balance or balance <= 0: continue
                        
                        # Split available balance by remaining slots
                        coin_capital = balance / available_slots
                        
                        # Calculate position
                        params = REGIME_PARAMS.get(regime, REGIME_PARAMS['range'])
                        sl_pct = atr_v * params['sl'] / price
                        if sl_pct <= 0 or sl_pct > 0.05: continue
                        
                        # ═══ NEW: MIN R:R FILTER ═══
                        rr_ratio = params['tp'] / params['sl']
                        if rr_ratio < MIN_RR:
                            log.info(f'{sym} SKIP: R:R={rr_ratio:.1f} < {MIN_RR}')
                            continue
                        
                        risk_amt = coin_capital * RISK_PCT
                        position_value = risk_amt / sl_pct
                        qty = position_value / price
                        
                        # Check minimums
                        notional = qty * price
                        if notional < 1: continue
                        margin_needed = notional / LEVERAGE
                        if margin_needed > balance * 0.9: continue
                        
                        # Place order
                        order_side = 'BUY' if side == 'LONG' else 'SELL'
                        sl = price - atr_v * params['sl'] if side == 'LONG' else price + atr_v * params['sl']
                        tp = price + atr_v * params['tp'] if side == 'LONG' else price - atr_v * params['tp']
                        success = place_order(sym, order_side, qty, sl, tp, grid.lot_step, atr_v)
                        if success:
                            grid.positions.append({
                                'side': side, 'entry': price, 'sl': sl, 'tp': tp,
                                'qty': qty, 'value': position_value,
                                'entry_time': datetime.now(timezone.utc).isoformat(),
                                'regime': regime
                            })
                            # Update tracked_positions with regime info
                            with position_lock:
                                if sym in tracked_positions:
                                    tracked_positions[sym]['regime'] = regime
                                    tracked_positions[sym]['entry_time'] = datetime.now(timezone.utc).isoformat()
                            grid.total_trades += 1
                        else:
                            for o in grid.grid_orders:
                                if o['price'] == sig_price: o['filled'] = False; break
                    
                    prev_prices[sym] = price
                    
                except Exception as e:
                    log.error(f'{sym} error: {e}')
            
            save_state(grids)
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log.info('Bot stopped by user')
            save_state(grids)
            break
        except Exception as e:
            log.error(f'Loop error: {e}')
            time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    web_thread = threading.Thread(target=start_web_server, args=(port,), daemon=True)
    web_thread.start()
    
    # Start position monitor thread (checks SL/TP every 2 seconds)
    monitor_thread = threading.Thread(target=position_monitor, daemon=True)
    monitor_thread.start()
    log.info('Position monitor thread started')
    
    main()
