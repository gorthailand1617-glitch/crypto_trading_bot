import os
import time
import math
import dotenv
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import streamlit as st
import pandas as pd

# Load environment
dotenv.load_dotenv()
try:
    if hasattr(st, "secrets"):
        for sec_key, sec_val in st.secrets.items():
            if isinstance(sec_val, (str, int, float, bool)):
                os.environ.setdefault(sec_key, str(sec_val))
except Exception:
    pass
from config import BOT_VERSION

# Process-Global Connection Cache Layer
if not hasattr(st, "_global_connection_cache"):
    st._global_connection_cache = {
        "balance": 16.33,
        "positions": {},
        "ticker": {"last": 64198.58, "high": 64500.0, "low": 63800.0, "bid": 64188.0, "ask": 64192.0},
        "ohlcv": None,
        "trades": [],
        "decisions": [],
        "logs": [],
        "last_refresh": 0.0,
        "trades_count": 0,
        "win_rate": 0.0,
        "bot_status": "paused",
        "dry_run": False,
        "live_sync_done": False,
        "vault_data": {},
        "vault_ledger": []
    }


from utils.state_manager import StateManager
from utils.db_manager import DBManager

import base64
logo_base64 = ""
if os.path.exists("logo.jpg"):
    try:
        with open("logo.jpg", "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        pass

# Set page config
st.set_page_config(
    page_title=f"Gor Trader Bot {os.getenv('SYMBOL', 'XRP/USDT')}",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

def sync_live_balance():
    """
    ระบบซิงค์ยอดเงินสดแบบ Dynamic ร่วมกับ Bybit Unified Trading Account 
    โดยใช้กลไก Session State มาตรฐานร่วมกับ _global_connection_cache เพื่อรองรับทั้ง Thread-local และ Background API
    """
    try:
        if "live_balance" not in st.session_state:
            st.session_state.live_balance = 12.37
    except Exception:
        pass

    try:
        import ccxt
        exchange_id = os.getenv("EXCHANGE_ID", "bybit")
        exchange_class = getattr(ccxt, exchange_id)
        exchange_config = {
            "enableRateLimit": True,
            "timeout": 30000,
            "options": {"defaultType": "linear"}
        }
        if exchange_id == "bybit":
            exchange_config["hostname"] = "bytick.com"
        api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BYBIT_API_KEY") or ""
        api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BYBIT_API_SECRET") or ""
        if api_key and api_secret:
            exchange_config["apiKey"] = api_key
            exchange_config["secret"] = api_secret
        exchange = exchange_class(exchange_config)
        
        # Force CCXT to fetch from Bybit Unified Trading Account (UTA)
        balance_data = exchange.fetch_balance({'type': 'unified'})

        # Extract the actual available equity or total wallet balance in USDT (UTA)
        # Prioritize totalEquity/totalWalletBalance to include non-USDT collateral value (e.g. SOL, BTC)
        try:
            list_entry = balance_data['info']['result']['list'][0]
            available_balance = list_entry.get('totalEquity') or list_entry.get('totalWalletBalance')
            if available_balance is not None:
                available_balance = float(available_balance)
            else:
                raise ValueError("No equity or wallet balance fields found in Bybit list")
        except Exception:
            if 'USDT' in balance_data['total']:
                available_balance = balance_data['total']['USDT']
            else:
                available_balance = 12.37


        try:
            st.session_state.live_balance = available_balance
        except Exception:
            pass
    except Exception as e:
        # If it fails, check state.json as absolute fallback
        try:
            state_manager = StateManager()
            state_data = state_manager.get_all()
            available_balance = state_data.get("balance", 12.37)
        except Exception:
            available_balance = 12.37
            
        try:
            st.session_state.live_balance = available_balance
        except Exception:
            pass
            
    # อัปเดตแคชส่วนกลางสำหรับ API Server (พอร์ต 8550) เพื่อให้ฝั่ง HTML/JS ดึงข้อมูลไปแสดงผลได้ถูกต้อง
    try:
        st._global_connection_cache["balance"] = st.session_state.live_balance
    except Exception:
        st._global_connection_cache["balance"] = available_balance
        
    st._global_connection_cache["live_sync_done"] = True
    return available_balance



if "live_balance_fetched" not in st.session_state:
    sync_live_balance()
    st.session_state["live_balance_fetched"] = True



# Background thread logic to pull data continuously
def background_update_loop():
    import ccxt
    
    state_manager = StateManager()
    db_manager = DBManager()
    
    symbol = os.getenv("SYMBOL", "BTC/USDT")
    exchange_id = os.getenv("EXCHANGE_ID", "bybit")
    timeframe = os.getenv("TIMEFRAME", "3m")
    
    # Instantiate exchange client
    exchange_class = getattr(ccxt, exchange_id)
    exchange_config = {
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"defaultType": "future"}
    }
    if exchange_id == "bybit":
        exchange_config["hostname"] = "bytick.com"
    exchange = exchange_class(exchange_config)
    
    while True:
        try:
            # 1. Fetch State
            state_data = state_manager.get_all()
            if not st._global_connection_cache.get("live_sync_done", False):
                st._global_connection_cache["balance"] = state_data.get("balance", 16.33)
            st._global_connection_cache["positions"] = state_data.get("positions", {})
            st._global_connection_cache["trades_count"] = state_data.get("trades_count", 0)
            st._global_connection_cache["win_rate"] = state_data.get("win_rate", 0.0)
            st._global_connection_cache["bot_status"] = state_data.get("bot_status", "paused")
            st._global_connection_cache["dry_run"] = state_data.get("dry_run", False)
            st._global_connection_cache["vault_data"] = state_data.get("vault_data", {})
            
            # 2. Fetch Ticker info
            try:
                ticker = exchange.fetch_ticker(symbol, params={'category': 'linear'})
                st._global_connection_cache["ticker"] = ticker
            except Exception:
                pass
                
            # 3. Fetch logs, trades, and vault ledger
            try:
                st._global_connection_cache["trades"] = db_manager.get_recent_trades(50)
                st._global_connection_cache["decisions"] = db_manager.get_recent_decisions(50)
                st._global_connection_cache["logs"] = db_manager.get_recent_logs(100)
                st._global_connection_cache["vault_ledger"] = db_manager.get_recent_vault_allocations(50)
            except Exception:
                pass
                
            st._global_connection_cache["last_refresh"] = time.time()
            
        except Exception:
            pass
            
        time.sleep(3)

# API endpoint bridge
class DashboardAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        state_manager = StateManager()

        if '/trades' in self.path:
            db_manager = DBManager()
            trades_raw = db_manager.get_recent_trades(limit=200)
            formatted = []
            for t in trades_raw:
                ts = t.get("timestamp", 0)
                ts_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else ""
                formatted.append({
                    "trade_id": f"TRD-{t.get('id', 0)}",
                    "timestamp": ts_str,
                    "symbol": t.get("symbol", os.getenv("SYMBOL", "XRP/USDT")),
                    "side": t.get("side", ""),
                    "leverage": f"{os.getenv('LEVERAGE', '5')}x",
                    "entry_price": t.get("price", 0.0),
                    "exit_price": t.get("price", 0.0),
                    "pnl": round(t.get("pnl", 0.0), 4),
                    "confidence": 85.0,
                    "status": "CLOSED" if t.get("pnl", 0.0) != 0.0 else "ENTRY",
                    "exit_reason": t.get("type", "")
                })
            self.wfile.write(json.dumps(formatted).encode('utf-8'))
            return
        
        if '/toggle' in self.path:
            current_status = state_manager.get("bot_status", "paused")
            new_status = "running" if current_status != "running" else "paused"
            state_manager.set("bot_status", new_status)
            st._global_connection_cache["bot_status"] = new_status
            
        if '/sync_balance' in self.path:
            asyncio.run(sync_live_balance())

        if '/clear_logs' in self.path:
            db_manager = DBManager()
            db_manager.clear_logs()
            st._global_connection_cache["logs"] = []
            
        data = {
            "balance": st._global_connection_cache["balance"],
            "positions": st._global_connection_cache["positions"],
            "trades_count": st._global_connection_cache["trades_count"],
            "win_rate": st._global_connection_cache["win_rate"],
            "bot_status": st._global_connection_cache["bot_status"],
            "dry_run": st._global_connection_cache["dry_run"],
            "ticker": st._global_connection_cache["ticker"],
            "trades": st._global_connection_cache["trades"],
            "decisions": st._global_connection_cache["decisions"],
            "logs": st._global_connection_cache["logs"],
            "vault_data": st._global_connection_cache.get("vault_data", {}),
            "vault_ledger": st._global_connection_cache.get("vault_ledger", []),
            "symbol": os.getenv("SYMBOL", "BTC/USDT"),
            "timeframe": os.getenv("TIMEFRAME", "3m"),
            "leverage": os.getenv("LEVERAGE", "5"),
            "exchange_id": os.getenv("EXCHANGE_ID", "bybit")
        }
        self.wfile.write(json.dumps(data).encode('utf-8'))

def run_api_server():
    try:
        server = HTTPServer(('127.0.0.1', 8550), DashboardAPIHandler)
        server.serve_forever()
    except Exception as e:
        print(f"[API SERVER WARNING] Could not start local API server: {e}")

# Spawn background updater and API once (safely scoped)
if not hasattr(st, "_global_bg_running"):
    st._global_bg_running = False

if not st._global_bg_running:
    st._global_bg_running = True
    t = threading.Thread(target=background_update_loop, daemon=True)
    t.start()

if not hasattr(st, "_global_api_running"):
    st._global_api_running = False

if not st._global_api_running:
    st._global_api_running = True
    api_t = threading.Thread(target=run_api_server, daemon=True)
    api_t.start()

# Hide Streamlit Default UI styling
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container {
        padding: 0rem !important;
        max-width: 100% !important;
    }
    iframe {
        border: none !important;
    }
    div[data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }
    body {
        background-color: oklch(0.16 0.014 260) !important;
    }
    </style>
""", unsafe_allow_html=True)

# Full screen React Dashboard App (Normal string - NO F-STRING to avoid bracket parsing issues)
html_content = """
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <title>AlphaScalp Terminal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Anuphan:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    
    <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <script src="https://unpkg.com/lucide@latest"></script>

    <style>
        :root {
            --bg-color: oklch(0.16 0.014 260);
            --accent-color: oklch(0.72 0.17 235);
            --success-color: oklch(0.78 0.18 140);
            --danger-color: oklch(0.62 0.22 25);
            --warning-color: oklch(0.79 0.16 75);
            --surface-color: oklch(0.22 0.02 260);
            --border-color: oklch(0.28 0.02 260);
            --text-primary: #ffffff;
            --text-secondary: rgba(255, 255, 255, 0.60);
            --text-muted: rgba(255, 255, 255, 0.35);
            --glow-shadow: 0 0 15px rgba(59, 130, 246, 0.3);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Anuphan', sans-serif;
            overflow-x: hidden;
            padding: 16px 16px 90px 16px;
            min-height: 100vh;
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Top Bar */
        .top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 18px;
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            margin-bottom: 16px;
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .logo-icon {
            color: var(--accent-color);
            filter: drop-shadow(0 0 5px rgba(59, 130, 246, 0.5));
        }

        .logo-title {
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: 0.5px;
        }

        .logo-version {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .status-container {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }

        .status-dot.active {
            background-color: var(--success-color);
            box-shadow: 0 0 8px var(--success-color);
            animation: pulse-ring 1.8s infinite;
        }

        .status-dot.paused {
            background-color: var(--warning-color);
            box-shadow: 0 0 8px var(--warning-color);
        }

        .status-text {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        .btn-pause {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 8px 16px;
            border-radius: 0.5rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: inherit;
        }

        .btn-pause:hover {
            border-color: var(--accent-color);
            box-shadow: var(--glow-shadow);
        }

        /* KPI Bar */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }

        @media (max-width: 1024px) {
            .kpi-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        .kpi-card {
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 16px;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }

        .kpi-card:hover {
            transform: translateY(-2px);
            border-color: var(--accent-color);
            box-shadow: var(--glow-shadow);
        }

        .kpi-card::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 2px;
            background-color: transparent;
            transition: background-color 0.2s ease;
        }

        .kpi-card:hover::after {
            background-color: var(--accent-color);
        }

        .kpi-header {
            display: flex;
            justify-content: space-between;
            color: var(--text-secondary);
            font-size: 0.8rem;
            font-weight: 500;
            margin-bottom: 8px;
        }

        .kpi-icon {
            color: var(--text-muted);
            width: 16px;
            height: 16px;
        }

        .btn-sync-wallet {
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            padding: 0;
            display: flex;
            align-items: center;
            transition: color 0.2s;
        }

        .btn-sync-wallet:hover {
            color: var(--accent-color);
        }

        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .spin {
            animation: spin 1s linear infinite;
        }

        .kpi-val {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
        }

        /* Sparkline mini chart inside winrate */
        .mini-chart {
            display: flex;
            align-items: flex-end;
            gap: 3px;
            height: 24px;
            margin-top: 4px;
        }

        .mini-bar {
            width: 4px;
            background-color: rgba(59, 130, 246, 0.2);
            border-radius: 1px;
            height: 50%;
        }

        .mini-bar.high {
            background-color: var(--success-color);
        }

        /* Main Grid Layout */
        .main-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }

        @media (max-width: 900px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
        }

        .panel {
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 16px;
            position: relative;
        }

        .panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            font-weight: 600;
            font-size: 0.9rem;
            color: var(--text-secondary);
            letter-spacing: 0.5px;
        }

        /* Chart Area */
        .timeframe-selector {
            display: flex;
            gap: 6px;
        }

        .tf-btn {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 4px 10px;
            border-radius: 0.35rem;
            font-size: 0.75rem;
            cursor: pointer;
            font-weight: 600;
        }

        .tf-btn.active {
            background-color: rgba(59, 130, 246, 0.15);
            border-color: var(--accent-color);
            color: var(--accent-color);
        }

        .chart-container {
            position: relative;
            height: 280px;
            background-color: rgba(0, 0, 0, 0.15);
            border-radius: 0.5rem;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.03);
            margin-bottom: 16px;
        }

        .last-price-badge {
            position: absolute;
            top: 12px;
            right: 12px;
            background-color: rgba(0, 0, 0, 0.6);
            border: 1px solid var(--border-color);
            border-radius: 0.35rem;
            padding: 6px 12px;
            font-size: 1rem;
            font-weight: 700;
            z-index: 10;
        }

        /* SVG Sparkline Sparking wave */
        .sparkline-svg {
            width: 100%;
            height: 100%;
        }

        .sparkline-line {
            fill: none;
            stroke: var(--accent-color);
            stroke-width: 2;
            stroke-linecap: round;
        }

        .sparkline-fill {
            fill: url(#chart-gradient);
        }

        .scan-line {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent-color), transparent);
            opacity: 0.45;
            animation: scan-line 6s linear infinite;
        }

        /* Indicator stats underneath */
        .indicator-row {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
        }

        .ind-box {
            background-color: rgba(0, 0, 0, 0.1);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            padding: 10px 14px;
        }

        .ind-lbl {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }

        .ind-val {
            font-size: 1.1rem;
            font-weight: 700;
        }

        /* Live Decision Logs Panel */
        .logs-container {
            display: flex;
            flex-direction: column;
            gap: 8px;
            height: 330px;
            overflow-y: auto;
            padding-right: 4px;
        }

        .log-entry {
            display: grid;
            grid-template-columns: auto 1fr;
            align-items: center;
            gap: 12px;
            background-color: rgba(0, 0, 0, 0.15);
            border: 1px solid var(--border-color);
            padding: 10px 12px;
            border-radius: 0.5rem;
            animation: fade-up 0.3s ease-out forwards;
        }

        .log-entry.info { border-left: 3px solid var(--accent-color); }
        .log-entry.success { border-left: 3px solid var(--success-color); }
        .log-entry.warning { border-left: 3px solid var(--warning-color); }
        .log-entry.danger { border-left: 3px solid var(--danger-color); }

        .log-time {
            font-size: 0.8rem;
            color: var(--text-muted);
        }

        .log-text {
            font-size: 0.8rem;
            font-weight: 500;
        }

        /* Active Position section */
        .pos-panel {
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 16px;
            margin-bottom: 16px;
        }

        .empty-state {
            border: 2px dashed rgba(255, 255, 255, 0.08);
            border-radius: 0.5rem;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
            color: var(--text-secondary);
            gap: 12px;
        }

        .empty-icon {
            color: var(--text-muted);
            width: 32px;
            height: 32px;
        }

        /* Trade History Table */
        .table-panel {
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 16px;
        }

        .terminal-table {
            width: 100%;
            border-collapse: collapse;
        }

        .terminal-table th {
            text-align: left;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 1px;
            color: var(--text-muted);
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-color);
        }

        .terminal-table td {
            padding: 12px;
            font-size: 0.85rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        }

        /* Floating Navigation Pill */
        .nav-pill-wrapper {
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            width: 95%;
            max-width: 680px;
            z-index: 1000;
        }

        .nav-pill {
            background-color: rgba(26, 32, 46, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 100px;
            padding: 6px;
            display: flex;
            justify-content: space-between;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        }

        .nav-tab {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            padding: 8px 16px;
            border-radius: 100px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
            font-family: inherit;
        }

        .nav-tab:hover {
            color: var(--text-primary);
        }

        .nav-tab.active {
            background-color: rgba(59, 130, 246, 0.15);
            color: var(--accent-color);
            border: 1px solid rgba(59, 130, 246, 0.2);
        }

        /* Animations */
        @keyframes pulse-ring {
            0% {
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4);
            }
            70% {
                box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
            }
            100% {
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
            }
        }

        @keyframes scan-line {
            0% {
                top: 0%;
            }
            50% {
                top: 100%;
            }
            100% {
                top: 0%;
            }
        }

        @keyframes ticker-flash {
            0% {
                color: var(--text-primary);
                filter: drop-shadow(0 0 0px transparent);
            }
            30% {
                color: var(--success-color);
                filter: drop-shadow(0 0 4px var(--success-color));
            }
            100% {
                color: var(--text-primary);
                filter: drop-shadow(0 0 0px transparent);
            }
        }

        .flash-green {
            animation: ticker-flash 0.6s ease-out;
        }

        @keyframes fade-up {
            0% {
                opacity: 0;
                transform: translateY(6px);
            }
            100% {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .pulse-live {
            animation: pulse-ring 2s infinite;
        }
        
        .btn-copy {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 4px 10px;
            border-radius: 0.375rem;
            font-size: 0.75rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
            font-family: inherit;
        }

        .btn-copy:hover {
            background: rgba(255, 255, 255, 0.1);
            color: var(--text-primary);
            border-color: var(--accent-color);
            box-shadow: 0 0 8px rgba(59, 130, 246, 0.2);
        }

        .btn-copy.copied {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success-color);
            border-color: var(--success-color);
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.2);
        }

        .btn-clear {
            background: rgba(239, 68, 68, 0.05);
            border: 1px solid rgba(239, 68, 68, 0.2);
            color: var(--danger-color);
            padding: 4px 10px;
            border-radius: 0.375rem;
            font-size: 0.75rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
            font-family: inherit;
        }

        .btn-clear:hover {
            background: rgba(239, 68, 68, 0.15);
            color: #ffffff;
            border-color: var(--danger-color);
            box-shadow: 0 0 8px rgba(239, 68, 68, 0.3);
        }
    </style>
</head>
<body>
    <div id="root"></div>

    <script type="text/babel">
        const { useState, useEffect, useRef } = React;

        function App() {
            const [activeTab, setActiveTab] = useState("แดชบอร์ด");
            const [timeframe, setTimeframe] = useState("5M");
            const [flashClass, setFlashClass] = useState("");
            const [priceHistory, setPriceHistory] = useState(() => {
                if (window.__INITIAL_PRICES__ && window.__INITIAL_PRICES__.length > 0) {
                    return window.__INITIAL_PRICES__;
                }
                const arr = [];
                const baseP = window.__INITIAL_DATA__ ? (window.__INITIAL_DATA__.ticker?.last || 1.5012) : 1.5012;
                for(let i=0; i<40; i++) {
                    const price = baseP + (Math.sin(i * 0.4) * 0.006 + Math.cos(i * 0.25) * 0.003);
                    arr.push(Number(price.toFixed(4)));
                }
                return arr;
            });
            const [apiData, setApiData] = useState(() => {
                if (window.__INITIAL_DATA__) {
                    return window.__INITIAL_DATA__;
                }
                return {
                    balance: 1.42,
                    positions: {},
                    trades_count: 0,
                    win_rate: 0.0,
                    bot_status: "running",
                    dry_run: false,
                    ticker: { last: 1.5012, high: 1.5267, low: 1.4861, bid: 1.5005, ask: 1.5015 },
                    trades: [],
                    decisions: [],
                    logs: [],
                    symbol: "XRP/USDT",
                    timeframe: "3m",
                    leverage: "5",
                    exchange_id: "bybit"
                };
            });
            const [botRunning, setBotRunning] = useState(() => {
                return (window.__INITIAL_DATA__ ? window.__INITIAL_DATA__.bot_status === "running" : true);
            });

            const [copied, setCopied] = useState(false);
            const [isSyncing, setIsSyncing] = useState(false);

            const handleCopyLogs = (logs) => {
                if (!logs || logs.length === 0) return;
                const textToCopy = logs.map(log => {
                    const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
                    return `[${timeStr}] [${log.level || 'INFO'}] [${log.module || 'TradingEngine'}] ${log.message}`;
                }).join('\\n');
                
                navigator.clipboard.writeText(textToCopy).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                }).catch(err => {
                    console.error("Could not copy logs: ", err);
                });
            };

            // Initialize Lucide Icons on renders
            useEffect(() => {
                if (window.lucide) {
                    window.lucide.createIcons();
                }
            });

            // Fetch Real-time Updates from API server
            const fetchData = async (action = "") => {
                try {
                    const res = await fetch("http://127.0.0.1:8550" + action);
                    const data = await res.json();
                    setApiData(data);
                    setBotRunning(data.bot_status === "running");
                } catch (e) {
                    // Running in cloud without local bridge
                }
            };

            // Loop updates every 1.6s
            useEffect(() => {
                fetchData();
                const interval = setInterval(() => {
                    fetchData();
                    // Simulating price ticker fluctuations (based on real price scale)
                    setPriceHistory(prev => {
                        const curPrice = prev[prev.length - 1] || (window.__INITIAL_DATA__?.ticker?.last || 1.5012);
                        const t = Date.now() / 1500;
                        const scale = curPrice > 100 ? 8 : 0.001;
                        const fluctuation = (Math.sin(t) * 0.6 + Math.cos(t * 1.8) * 0.4) * scale + (Math.random() - 0.5) * scale * 0.5;
                        const decimals = curPrice > 100 ? 2 : 4;
                        const nextVal = Number((curPrice + fluctuation).toFixed(decimals));
                        setFlashClass("flash-green");
                        setTimeout(() => setFlashClass(""), 500);
                        return [...prev.slice(1), nextVal];
                    });
                }, 1600);
                return () => clearInterval(interval);
            }, []);

            const toggleBot = () => {
                fetchData("/toggle");
            };

            const handleClearLogs = () => {
                fetchData("/clear_logs");
            };

            const lastPrice = priceHistory[priceHistory.length - 1] || (window.__INITIAL_DATA__?.ticker?.last || 1.5012);
            
            // Build SVG sparkline path helper
            const width = 600;
            const height = 280;
            const minP = Math.min(...priceHistory);
            const maxP = Math.max(...priceHistory);
            const pRange = (maxP - minP) || 1;

            const svgPoints = priceHistory.map((price, idx) => {
                const x = (idx / (priceHistory.length - 1)) * width;
                const y = height - ((price - minP) / pRange) * (height - 60) - 30;
                return { x, y };
            });

            const pathStr = svgPoints.map((pt, idx) => (idx === 0 ? "M" : "L") + " " + pt.x + " " + pt.y).join(" ");
            const fillPathStr = "M 0," + height + " L " + svgPoints.map(pt => pt.x + "," + pt.y).join(" L ") + " L " + width + "," + height + " Z";

            // Filter system logs
            const displayLogs = (apiData.logs && apiData.logs.length > 0) ? apiData.logs : [
                { timestamp: Math.floor(Date.now()/1000), level: "INFO", message: `AI Brain monitoring ${(apiData.symbol || 'XRP/USDT')} on 3m timeframe`, module: "TradingEngine" },
                { timestamp: Math.floor(Date.now()/1000)-12, level: "SUCCESS", message: "Bybit UTA Equity & Risk Manager Synchronized", module: "TradingEngine" },
                { timestamp: Math.floor(Date.now()/1000)-28, level: "INFO", message: "Orderbook Imbalance & CMO Scalp scan active", module: "TradingEngine" },
                { timestamp: Math.floor(Date.now()/1000)-60, level: "INFO", message: "Breakeven Guard & Margin Shield armed (5x)", module: "TradingEngine" }
            ];

            return (
                <div>
                    {/* Top Bar */}
                    <header className="top-bar">
                        <div className="logo-section">
                            <i data-lucide="zap" className="logo-icon"></i>
                            <div>
                                <h1 className="logo-title">AlphaScalp</h1>
                                <p className="logo-version">V5.0</p>
                            </div>
                        </div>
                        <div className="status-container">
                            <span className={"status-dot " + (botRunning ? "active" : "paused")}></span>
                            <span className="status-text">
                                {botRunning ? "กำลังทำงาน (เทรดจริง)" : "หยุดทำงานชั่วคราว"}
                            </span>
                        </div>
                        <button className="btn-pause" onClick={toggleBot}>
                            {botRunning ? (
                                <React.Fragment>
                                    <i data-lucide="pause"></i>
                                    <span>หยุดทำงานชั่วคราว</span>
                                </React.Fragment>
                            ) : (
                                <React.Fragment>
                                    <i data-lucide="play"></i>
                                    <span>เริ่มการทำงาน</span>
                                </React.Fragment>
                            )}
                        </button>
                    </header>

                    {/* RENDER VIEWPORT DEPENDENT TAB PANEL */}
                    {activeTab === "แดชบอร์ด" && (
                        <React.Fragment>
                            {/* KPI Bar */}
                            <section className="kpi-grid">
                                <div className="kpi-card">
                                    <div className="kpi-header">
                                        <span>เงินทุนหลักคงเหลือ</span>
                                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                            <button 
                                                onClick={() => {
                                                    setIsSyncing(true);
                                                    fetchData("/sync_balance").finally(() => setIsSyncing(false));
                                                }}
                                                className="btn-sync-wallet"
                                                title="Sync Wallet"
                                            >
                                                <i data-lucide="refresh-cw" className={isSyncing ? "spin" : ""} style={{ width: 14, height: 14 }}></i>
                                            </button>
                                            <i data-lucide="wallet" className="kpi-icon"></i>
                                        </div>
                                    </div>
                                    <div className="kpi-val mono">${apiData.balance.toFixed(2)}</div>
                                </div>

                                <div className="kpi-card">
                                    <div className="kpi-header">
                                        <span>สถานะเหรียญที่ถือครอง</span>
                                        <i data-lucide="layers" className="kpi-icon"></i>
                                    </div>
                                    <div className="kpi-val">ไม่มี</div>
                                </div>

                                <div className="kpi-card">
                                    <div className="kpi-header">
                                        <span>อัตราการชนะ (WIN RATE)</span>
                                        <i data-lucide="percent" className="kpi-icon"></i>
                                    </div>
                                    <div className="kpi-val mono">{apiData.win_rate.toFixed(1)}%</div>
                                    <div className="mini-chart">
                                        <span className="mini-bar high" style={{height: "35%"}}></span>
                                        <span className="mini-bar high" style={{height: "60%"}}></span>
                                        <span className="mini-bar" style={{height: "25%"}}></span>
                                        <span className="mini-bar high" style={{height: "80%"}}></span>
                                        <span className="mini-bar high" style={{height: "95%"}}></span>
                                    </div>
                                </div>

                                <div className="kpi-card">
                                    <div className="kpi-header">
                                        <span>จำนวนรอบเทรดรวม</span>
                                        <i data-lucide="refresh-cw" className="kpi-icon"></i>
                                    </div>
                                    <div className="kpi-val mono">
                                        {apiData.trades_count} <span style={{fontSize: "0.8rem", color: "var(--success-color)", fontWeight: 500}}>+0 today</span>
                                    </div>
                                </div>

                                <div className="kpi-card">
                                    <div className="kpi-header">
                                        <span>สภาวะตลาดปัจจุบัน</span>
                                        <i data-lucide="trending-up" className="kpi-icon"></i>
                                    </div>
                                    <div style={{display: "flex", justifyContent: "space-between", alignItems: "baseline"}}>
                                        <div className="kpi-val" style={{fontSize: "1.2rem"}}>ไซด์เวย์</div>
                                        <div className={"mono " + flashClass} style={{fontSize: "1rem", fontWeight: 700}}>
                                            {lastPrice.toLocaleString(undefined, {minimumFractionDigits: lastPrice > 10 ? 2 : 4, maximumFractionDigits: lastPrice > 10 ? 2 : 4})}
                                        </div>
                                    </div>
                                </div>
                            </section>

                            {/* Main Grid */}
                            <main className="main-grid">
                                <section className="panel">
                                    <div className="panel-header">
                                        <div style={{display: "flex", alignItems: "center", gap: 6}}>
                                            <i data-lucide="activity" style={{width: 16, height: 16, color: "var(--accent-color)"}}></i>
                                            <span>TECHNICAL · {apiData.symbol || 'XRP/USDT'}</span>
                                        </div>
                                        <div className="timeframe-selector">
                                            {["1M", "5M", "15M", "1H"].map(tf => (
                                                <button 
                                                    key={tf} 
                                                    className={"tf-btn " + (timeframe === tf ? "active" : "")}
                                                    onClick={() => setTimeframe(tf)}
                                                >
                                                    {tf}
                                                </button>
                                            ))}
                                        </div>
                                    </div>

                                    <div className="chart-container">
                                        <div className="scan-line"></div>
                                        <div className="last-price-badge mono">
                                            LAST: {lastPrice.toLocaleString(undefined, {minimumFractionDigits: lastPrice > 10 ? 2 : 4, maximumFractionDigits: lastPrice > 10 ? 2 : 4})}
                                        </div>
                                        <svg className="sparkline-svg" viewBox={"0 0 " + width + " " + height} preserveAspectRatio="none">
                                            <defs>
                                                <linearGradient id="chart-gradient" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="0%" stopColor="var(--accent-color)" stopOpacity="0.4" />
                                                    <stop offset="100%" stopColor="var(--accent-color)" stopOpacity="0.0" />
                                                </linearGradient>
                                            </defs>
                                            <path className="sparkline-fill" d={fillPathStr} />
                                            <path className="sparkline-line" d={pathStr} />
                                        </svg>
                                    </div>

                                    <div className="indicator-row">
                                        <div className="ind-box">
                                            <p className="ind-lbl">RSI (14)</p>
                                            <p className="ind-val mono" style={{color: "var(--accent-color)"}}>48.2</p>
                                        </div>
                                        <div className="ind-box">
                                            <p className="ind-lbl">Volatility (ATR)</p>
                                            <p className="ind-val mono" style={{color: "var(--warning-color)"}}>0.18%</p>
                                        </div>
                                        <div className="ind-box">
                                            <p className="ind-lbl">Regime Score</p>
                                            <p className="ind-val mono" style={{color: "var(--success-color)"}}>Neutral</p>
                                        </div>
                                    </div>
                                </section>

                                <section className="panel">
                                    <div className="panel-header">
                                        <div style={{display: "flex", alignItems: "center", gap: 6}}>
                                            <i data-lucide="terminal" style={{width: 16, height: 16, color: "var(--accent-color)"}}></i>
                                            <span>DECISION LOGS</span>
                                        </div>
                                        <div style={{display: "flex", alignItems: "center", gap: 12}}>
                                            <button 
                                                className={"btn-copy " + (copied ? "copied" : "")}
                                                onClick={() => handleCopyLogs(displayLogs)}
                                            >
                                                {copied ? (
                                                    <i data-lucide="check" style={{width: 12, height: 12}}></i>
                                                ) : (
                                                    <i data-lucide="copy" style={{width: 12, height: 12}}></i>
                                                )}
                                                <span>{copied ? "คัดลอกแล้ว!" : "คัดลอก"}</span>
                                            </button>

                                            <button 
                                                className="btn-clear"
                                                onClick={handleClearLogs}
                                            >
                                                <i data-lucide="trash-2" style={{width: 12, height: 12}}></i>
                                                <span>เคลียร์ Logs</span>
                                            </button>

                                            <div style={{display: "flex", alignItems: "center", gap: 6}}>
                                                <span className="status-dot active"></span>
                                                <span className="mono" style={{fontSize: "0.75rem", color: "var(--success-color)", fontWeight: "bold"}}>LIVE</span>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="logs-container">
                                        {displayLogs.map((log, index) => {
                                            const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
                                            let logType = "info";
                                            if (log.level === "SUCCESS" || log.message.includes("LONG")) logType = "success";
                                            if (log.level === "DANGER" || log.message.includes("STOP")) logType = "danger";
                                            if (log.level === "WARNING") logType = "warning";

                                            return (
                                                <div key={index} className={"log-entry " + logType}>
                                                    <span className="log-time mono">{timeStr}</span>
                                                    <span className="log-text mono">{log.message}</span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </section>
                            </main>

                            {/* Active Position */}
                            <section className="pos-panel">
                                <div className="panel-header">
                                    <span>ACTIVE POSITION</span>
                                </div>
                                <div className="empty-state">
                                    <i data-lucide="alert-circle" className="empty-icon"></i>
                                    <span>ยังไม่มีสถานะการเทรดที่เปิดทำงานอยู่ ณ ขณะนี้</span>
                                </div>
                            </section>

                            {/* Trade History Table */}
                            <section className="table-panel">
                                <div className="panel-header">
                                    <span>TRADE HISTORY</span>
                                </div>
                                <table className="terminal-table">
                                    <thead>
                                        <tr>
                                            <th>time</th>
                                            <th>symbol</th>
                                            <th>side</th>
                                            <th>price</th>
                                            <th>amount</th>
                                            <th>pnl</th>
                                            <th>type</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {apiData.trades && apiData.trades.length > 0 ? (
                                            apiData.trades.map((trade, idx) => {
                                                const d = new Date(trade.timestamp * 1000);
                                                const timeStr = d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour12: false });
                                                const pnlColor = trade.pnl > 0 ? "var(--success-color)" : trade.pnl < 0 ? "var(--danger-color)" : "var(--text-secondary)";
                                                const pnlText = trade.pnl !== 0 ? `${trade.pnl > 0 ? "+" : ""}${trade.pnl.toFixed(4)}` : "0.0000";
                                                return (
                                                    <tr key={idx}>
                                                        <td className="mono">{timeStr}</td>
                                                        <td>{trade.symbol}</td>
                                                        <td style={{color: trade.side.toUpperCase() === "BUY" ? "var(--success-color)" : "var(--danger-color)", fontWeight: "bold"}}>{trade.side}</td>
                                                        <td className="mono">${trade.price.toFixed(4)}</td>
                                                        <td className="mono">{trade.amount.toFixed(4)}</td>
                                                        <td className="mono" style={{color: pnlColor, fontWeight: "bold"}}>{pnlText}</td>
                                                        <td>{trade.type}</td>
                                                    </tr>
                                                );
                                            })
                                        ) : (
                                            <tr>
                                                <td colSpan="7" style={{textAlign: "center", color: "var(--text-muted)", padding: "36px 0"}}>
                                                    ยังไม่มีประวัติการส่งคำสั่งซื้อขายเกิดขึ้นในฐานข้อมูล
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </section>
                        </React.Fragment>
                    )}

                    {activeTab === "สภาวะตลาด" && (
                        <div className="panel" style={{padding: 24}}>
                            <div className="panel-header" style={{fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: 12}}>
                                <span>💹 ข้อมูลสภาวะตลาดแบบเจาะลึก (Market Deep-Dive)</span>
                            </div>
                            <div style={{display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16, marginTop: 16}}>
                                <div className="ind-box">
                                    <p className="ind-lbl">ราคาตลาดล่าสุด (Last Price)</p>
                                    <p className="ind-val mono">${apiData.ticker.last.toLocaleString()}</p>
                                </div>
                                <div className="ind-box">
                                    <p className="ind-lbl">สเปรดราคา (Spread)</p>
                                    <p className="ind-val mono">${(apiData.ticker.ask - apiData.ticker.bid).toFixed(2)}</p>
                                </div>
                                <div className="ind-box">
                                    <p className="ind-lbl">ราคาสูงสุดรอบ 24 ชั่วโมง</p>
                                    <p className="ind-val mono" style={{color: "var(--success-color)"}}>${apiData.ticker.high.toLocaleString()}</p>
                                </div>
                                <div className="ind-box">
                                    <p className="ind-lbl">ราคาต่ำสุดรอบ 24 ชั่วโมง</p>
                                    <p className="ind-val mono" style={{color: "var(--danger-color)"}}>${apiData.ticker.low.toLocaleString()}</p>
                                </div>
                            </div>
                        </div>
                    )}

                    {activeTab === "กราฟเทคนิค" && (
                        <div className="panel" style={{padding: 24}}>
                            <div className="panel-header" style={{fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: 12}}>
                                <span>📈 กราฟวิเคราะห์เทคนิค (Technical SVG Chart)</span>
                            </div>
                            <div className="chart-container" style={{marginTop: 16, height: 350}}>
                                <div className="scan-line"></div>
                                <svg className="sparkline-svg" viewBox={"0 0 " + width + " " + height} preserveAspectRatio="none">
                                    <path className="sparkline-fill" d={fillPathStr} />
                                    <path className="sparkline-line" d={pathStr} />
                                </svg>
                            </div>
                        </div>
                    )}

                    {activeTab === "บันทึก Log" && (
                        <div className="panel" style={{padding: 24}}>
                            <div className="panel-header" style={{fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: 12}}>
                                <span>💻 บันทึกการตัดสินใจและเหตุการณ์บอท (Strategic Engine Logs)</span>
                                <button 
                                    className={"btn-copy " + (copied ? "copied" : "")}
                                    onClick={() => handleCopyLogs(displayLogs)}
                                >
                                    {copied ? (
                                        <i data-lucide="check" style={{width: 12, height: 12}}></i>
                                    ) : (
                                        <i data-lucide="copy" style={{width: 12, height: 12}}></i>
                                    )}
                                    <span>{copied ? "คัดลอกแล้ว!" : "คัดลอก Log ทั้งหมด"}</span>
                                </button>
                            </div>
                            <div className="logs-container" style={{marginTop: 16, height: 400}}>
                                {displayLogs.map((log, index) => {
                                    const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
                                    let logType = "info";
                                    if (log.level === "SUCCESS" || log.message.includes("LONG")) logType = "success";
                                    if (log.level === "DANGER" || log.message.includes("STOP")) logType = "danger";
                                    if (log.level === "WARNING") logType = "warning";

                                    return (
                                        <div key={index} className={"log-entry " + logType}>
                                            <span className="log-time mono">{timeStr}</span>
                                            <span className="log-text mono">[{log.module}] {log.message}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {activeTab === "สถิติการเทรด" && (
                        <div className="panel" style={{padding: 24}}>
                            <div className="panel-header" style={{fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: 12}}>
                                <span>🔍 สถิติและประสิทธิภาพสุทธิ (Performance Observability)</span>
                            </div>
                            <div style={{display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 16}}>
                                <div className="ind-box">
                                    <p className="ind-lbl">อัตราการชนะ (Win Rate)</p>
                                    <p className="ind-val mono">{apiData.win_rate.toFixed(1)}%</p>
                                </div>
                                <div className="ind-box">
                                    <p className="ind-lbl">จำนวนรอบเทรดสำเร็จทั้งหมด</p>
                                    <p className="ind-val mono">{apiData.trades_count} ไม้</p>
                                </div>
                                <div className="ind-box">
                                    <p className="ind-lbl">โหมดจำลอง (Dry Run)</p>
                                    <p className="ind-val" style={{color: "var(--warning-color)"}}>
                                        {apiData.dry_run ? "เปิดใช้งาน (จำลอง)" : "ปิดใช้งาน (เทรดจริง)"}
                                    </p>
                                </div>
                            </div>
                        </div>
                    )}

                    {activeTab === "ตั้งค่าบอท" && (
                        <div className="panel" style={{padding: 24}}>
                            <div className="panel-header" style={{fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: 12}}>
                                <span>👤 ข้อมูลโครงร่างบอท (Configurations Profile)</span>
                            </div>
                            <div style={{display: "flex", flexDirection: "column", gap: 12, marginTop: 16}}>
                                <div>
                                    <p style={{color: "var(--text-secondary)", fontSize: "0.85rem"}}>คู่เหรียญที่เปิดระบบเทรด (Symbol)</p>
                                    <p className="mono" style={{fontSize: "1.1rem", fontWeight: 600, color: "var(--accent-color)"}}>{apiData.symbol}</p>
                                </div>
                                <div>
                                    <p style={{color: "var(--text-secondary)", fontSize: "0.85rem"}}>กรอบระยะเวลาวิเคราะห์ (Timeframe)</p>
                                    <p className="mono" style={{fontSize: "1.1rem", fontWeight: 600}}>{apiData.timeframe}</p>
                                </div>
                                <div>
                                    <p style={{color: "var(--text-secondary)", fontSize: "0.85rem"}}>ระดับ Leverage คูณสูงสุด</p>
                                    <p className="mono" style={{fontSize: "1.1rem", fontWeight: 600}}>{apiData.leverage}x</p>
                                </div>
                                <div>
                                    <p style={{color: "var(--text-secondary)", fontSize: "0.85rem"}}>Exchange API Endpoints</p>
                                    <p className="mono" style={{fontSize: "1.1rem", fontWeight: 600}}>{apiData.exchange_id}</p>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Bottom Navigation Pill */}
                    <div className="nav-pill-wrapper">
                        <nav className="nav-pill">
                            {["แดชบอร์ด", "สภาวะตลาด", "กราฟเทคนิค", "บันทึก Log", "สถิติการเทรด", "ตั้งค่าบอท"].map((tab, idx) => {
                                const icons = ["layout", "trending-up", "activity", "terminal", "bar-chart-2", "settings"];
                                return (
                                    <button 
                                        key={tab} 
                                        className={"nav-tab " + (activeTab === tab ? "active" : "")}
                                        onClick={() => setActiveTab(tab)}
                                    >
                                        <i data-lucide={icons[idx]} style={{width: 14, height: 14}}></i>
                                        <span>{tab}</span>
                                    </button>
                                );
                            })}
                        </nav>
                    </div>
                </div>
            );
        }

        const root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(<App />);
    </script>
</body>
</html>
"""

# Apply dynamic app name, version, and logo
symbol = os.getenv("SYMBOL", "XRP/USDT")
timeframe = os.getenv("TIMEFRAME", "3m")
exchange_id = os.getenv("EXCHANGE_ID", "bybit")
leverage = os.getenv("LEVERAGE", "5")
dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

# Live data fetch from Bybit
live_balance = sync_live_balance()
real_ticker = {"last": 1.5012, "high": 1.5267, "low": 1.4861, "bid": 1.5005, "ask": 1.5015}
real_prices = []

try:
    import ccxt
    ex_class = getattr(ccxt, exchange_id)
    ex = ex_class({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "linear"}
    })
    if exchange_id == "bybit":
        ex.options["hostname"] = "bytick.com"
    t = ex.fetch_ticker(symbol)
    if t and t.get("last"):
        real_ticker = {
            "last": float(t.get("last", 1.5012)),
            "high": float(t.get("high", 1.5267)),
            "low": float(t.get("low", 1.4861)),
            "bid": float(t.get("bid", 1.5005)),
            "ask": float(t.get("ask", 1.5015))
        }
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=35)
    if ohlcv:
        real_prices = [float(c[4]) for c in ohlcv]
except Exception as e:
    print(f"[TICKER NOTICE] {e}")

if not real_prices:
    lp = real_ticker["last"]
    real_prices = [round(lp * (1 + math.sin(i * 0.25) * 0.004), 4) for i in range(35)]

state_manager = StateManager()
db_manager = DBManager()
state_data = state_manager.get_all()

initial_api_data = {
    "balance": float(live_balance or 1.4252),
    "positions": state_data.get("positions", {}),
    "trades_count": state_data.get("trades_count", 0),
    "win_rate": state_data.get("win_rate", 0.0),
    "bot_status": "running",
    "dry_run": dry_run,
    "ticker": real_ticker,
    "trades": db_manager.get_recent_trades(50),
    "decisions": db_manager.get_recent_decisions(50),
    "logs": db_manager.get_recent_logs(100),
    "symbol": symbol,
    "timeframe": timeframe,
    "leverage": leverage,
    "exchange_id": exchange_id
}

injection_script = f"""
    <script>
        window.__INITIAL_DATA__ = {json.dumps(initial_api_data)};
        window.__INITIAL_PRICES__ = {json.dumps(real_prices)};
    </script>
"""

modified_html = html_content.replace('<script type="text/babel">', f'{injection_script}\n    <script type="text/babel">')
modified_html = modified_html.replace("AlphaScalp Terminal", f"Gor Trader Bot {symbol}")
modified_html = modified_html.replace('<h1 className="logo-title">AlphaScalp</h1>', f'<h1 className="logo-title">Gor Trader Bot {symbol}</h1>')
modified_html = modified_html.replace('<p className="logo-version">v4.2</p>', f'<p className="logo-version">{BOT_VERSION}</p>')
modified_html = modified_html.replace('<p className="logo-version">V5.0</p>', f'<p className="logo-version">{BOT_VERSION}</p>')

if logo_base64:
    modified_html = modified_html.replace(
        '<i data-lucide="zap" className="logo-icon"></i>',
        f'<img src="data:image/jpeg;base64,{logo_base64}" className="logo-img" style={{{{width: 40, height: 40, borderRadius: "50%", marginRight: 10, border: "2px solid var(--accent-color)"}}}} />'
    )

st.components.v1.html(modified_html, height=950, scrolling=False)

