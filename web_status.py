"""
Status server for multi-coin adaptive grid bot.
"""

import json, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

STATE_FILE = 'bot_state.json'

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            state = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    state = json.load(f)
            
            coins_status = {}
            total_trades = 0
            total_pnl = 0
            
            for sym in ['XRPUSDT', 'ADAUSDT', 'INJUSDT', 'FILUSDT']:
                if sym in state:
                    s = state[sym]
                    coins_status[sym] = {
                        'regime': s.get('current_regime', 'unknown'),
                        'grid_center': s.get('grid_center', 0),
                        'trades': s.get('total_trades', 0),
                        'wins': s.get('wins', 0),
                        'losses': s.get('losses', 0),
                        'pnl': s.get('total_pnl', 0),
                        'positions': len(s.get('positions', []))
                    }
                    total_trades += s.get('total_trades', 0)
                    total_pnl += s.get('total_pnl', 0)
            
            meta = state.get('_meta', {})
            
            status = {
                'bot': 'Adaptive Grid Bot (Multi-Coin)',
                'mode': 'DEMO' if 'demo' in os.environ.get('BASE_URL', '') else 'LIVE',
                'coins': coins_status,
                'total_trades': total_trades,
                'total_pnl': total_pnl,
                'last_update': meta.get('last_update', 'never'),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            self.wfile.write(json.dumps(status, indent=2).encode())
        
        elif self.path == '/' or self.path == '/dashboard':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html = '''<!DOCTYPE html>
<html><head><title>Adaptive Grid Bot</title>
<meta http-equiv="refresh" content="30">
<style>
body{font-family:monospace;background:#1a1a2e;color:#eee;padding:20px}
.card{background:#16213e;padding:15px;margin:10px 0;border-radius:8px}
.stat{font-size:20px;font-weight:bold}
.green{color:#4ade80}.red{color:#f87171}.yellow{color:#fbbf24}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}
</style></head><body>
<h1>🤖 Adaptive Grid Bot — Multi-Coin</h1>
<div id="s">Loading...</div>
<script>
fetch('/status').then(r=>r.json()).then(d=>{
 let h='<div class="card">Mode: <span class="yellow">'+d.mode+'</span></div>';
 h+='<div class="grid">';
 for(let s in d.coins){
  let c=d.coins[s];
  let wr=c.trades>0?(c.wins/c.trades*100).toFixed(1):0;
  let cls=c.pnl>=0?'green':'red';
  h+='<div class="card"><b>'+s+'</b><br>';
  h+='Regime: '+c.regime+'<br>';
  h+='Trades: '+c.trades+' | WR: '+wr+'%<br>';
  h+='PnL: <span class="'+cls+'">$'+c.pnl.toFixed(2)+'</span><br>';
  h+='Positions: '+c.positions+'</div>';
 }
 h+='</div>';
 h+='<div class="card">Total Trades: '+d.total_trades+' | Total PnL: $'+d.total_pnl.toFixed(2)+'<br>';
 h+='Last Update: '+d.last_update+'</div>';
 document.getElementById('s').innerHTML=h;
});</script></body></html>'''
            self.wfile.write(html.encode())
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args): pass

def start_web_server(port=8080):
    server = HTTPServer(('0.0.0.0', port), StatusHandler)
    print(f'Status server on port {port}')
    server.serve_forever()
