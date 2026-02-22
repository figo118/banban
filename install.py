import os
import sys
import subprocess

# 所有文件的内容（移除了emoji）
FILES = {
    "requirements.txt": """ccxt>=4.0.0
pandas>=1.5.0
pandas-ta>=0.3.14b0
python-dotenv>=1.0.0
aiohttp>=3.8.0
apscheduler>=3.10.0
numpy>=1.24.0
termcolor>=2.3.0
fastapi>=0.100.0
uvicorn>=0.22.0
jinja2>=3.1.0
python-multipart>=0.0.6""",

    ".env": """# 交易所配置 (可选)
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Telegram配置 (可选)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 系统配置
CHECK_INTERVAL=300
TOP_GAINERS_LIMIT=30
ACCOUNT_BALANCE=10000
RISK_PER_TRADE=0.02""",

    "config.py": """import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 交易所配置
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
    
    # Telegram配置
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # 交易系统通用配置
    TIMEFRAMES = ['15m', '1h', '4h', '1d']
    TARGET_COINS_COUNT = int(os.getenv("TOP_GAINERS_LIMIT", 30))
    
    # 风险管理
    DEFAULT_RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", 0.02))
    ACCOUNT_BALANCE_FIXED = float(os.getenv("ACCOUNT_BALANCE", 10000))
    LEVERAGE_DEFAULT = 5
    
    # 策略阈值
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    ADX_TREND_MIN = 25
    VOLUME_SPIKE_FACTOR = 2.0

    # 数据库
    DB_PATH = "trading_signals.db"
""",

    "main.py": """import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.data_engine import DataEngine
from strategies.strategies_full import FullStrategyEvaluator
from core.risk_manager import RiskManager
from notification.telegram_bot import TelegramNotifier
from database.models import DatabaseManager
from config import Config
import traceback

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TradingSystem:
    def __init__(self):
        self.engine = DataEngine()
        self.db = DatabaseManager()
        self.market_scan_result = []
        self.signals_history = []
        self.latest_scan_time = None
        self.running = True

    async def run_analysis_cycle(self):
        try:
            self.latest_scan_time = datetime.now()
            logger.info("开始市场扫描...")
            
            top_list = await self.engine.get_top_gainers()
            if not top_list:
                logger.warning("无法获取涨幅榜数据")
                return
            
            logger.info(f"获取到 {len(top_list)} 个热门币种")
            
            results = []
            signals = []
            
            for ticker in top_list:
                try:
                    symbol = ticker['symbol']
                    data = await self.engine.get_full_market_data(symbol)
                    if not data:
                        continue
                    
                    evaluator = FullStrategyEvaluator(symbol, data)
                    analysis = evaluator.evaluate_all()
                    
                    if analysis:
                        if 'signal' in analysis and analysis['signal']:
                            entry = analysis['signal']['entry']
                            sl = analysis['signal']['stop_loss']
                            tp = analysis['signal']['take_profit']
                            
                            rr = RiskManager.check_risk_reward(entry, sl, tp)
                            pos_size, pos_value = RiskManager.calculate_position_size(entry, sl)
                            
                            analysis['risk'] = {
                                'risk_reward': rr,
                                'position_size': pos_size,
                                'position_value': pos_value
                            }
                            
                            self.db.save_signal(analysis)
                            
                            if analysis['total_score'] >= 70:
                                await TelegramNotifier.send_signal(analysis)
                                signals.append(analysis)
                    
                    results.append({
                        "symbol": symbol,
                        "price": data['15m'].iloc[-1]['close'] if data['15m'] is not None else 0,
                        "change_24h": ticker.get('percentage', 0),
                        "score": analysis['total_score'] if analysis else 0,
                        "signal": analysis.get('signal', {}).get('name', '无') if analysis else '无'
                    })
                    
                except Exception as e:
                    logger.error(f"分析 {ticker.get('symbol', '未知')} 失败: {e}")
                    continue
            
            self.market_scan_result = sorted(results, key=lambda x: x['score'], reverse=True)
            self.signals_history.extend(signals)
            logger.info(f"扫描完成，发现 {len(signals)} 个高质量信号")
            
        except Exception as e:
            logger.error(f"分析周期失败: {e}")

    async def daily_summary(self):
        try:
            stats = self.db.get_performance_stats(days=1)
            await TelegramNotifier.send_daily_summary(stats)
        except Exception as e:
            logger.error(f"每日总结失败: {e}")

    def start_scheduler(self):
        scheduler = AsyncIOScheduler()
        scheduler.add_job(self.run_analysis_cycle, 'interval', minutes=15)
        scheduler.add_job(self.daily_summary, 'cron', hour=0, minute=5)
        scheduler.start()
        logger.info("调度器已启动")

    async def cleanup(self):
        self.running = False
        await self.engine.close()
        logger.info("系统已关闭")

async def main():
    system = TradingSystem()
    try:
        await system.run_analysis_cycle()
        system.start_scheduler()
        while system.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    finally:
        await system.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
""",

    "core/__init__.py": "# Core package\n",
    
    "core/data_engine.py": """import ccxt.async_support as ccxt
import asyncio
import pandas as pd
from config import Config
import logging

logger = logging.getLogger(__name__)

class DataEngine:
    def __init__(self):
        self.exchange = ccxt.binanceusdm({
            'apiKey': Config.BINANCE_API_KEY,
            'secret': Config.BINANCE_API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

    async def close(self):
        await self.exchange.close()

    async def get_top_gainers(self, limit=None):
        if limit is None:
            limit = Config.TARGET_COINS_COUNT
        try:
            tickers = await self.exchange.fetch_tickers()
            usdt_futures = [
                symbol for symbol in tickers.keys() 
                if '/USDT' in symbol
            ]
            valid_tickers = []
            for s in usdt_futures:
                t = tickers[s]
                if t.get('percentage') is not None:
                    valid_tickers.append(t)
            
            sorted_tickers = sorted(valid_tickers, key=lambda x: x['percentage'], reverse=True)
            return sorted_tickers[:limit]
        except Exception as e:
            logger.error(f"Error fetching top gainers: {e}")
            return []

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return None

    async def fetch_funding_and_oi(self, symbol):
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            try:
                oi = await self.exchange.fetch_open_interest(symbol)
            except:
                oi = {'openInterestAmount': 0}
            return {
                'fundingRate': funding['fundingRate'],
                'openInterest': oi.get('openInterestAmount', 0)
            }
        except Exception as e:
            logger.error(f"Error fetching funding/OI for {symbol}: {e}")
            return {'fundingRate': 0, 'openInterest': 0}

    async def get_full_market_data(self, symbol):
        tasks = {
            '15m': self.fetch_ohlcv(symbol, '15m'),
            '1h': self.fetch_ohlcv(symbol, '1h'),
            '4h': self.fetch_ohlcv(symbol, '4h'),
            '1d': self.fetch_ohlcv(symbol, '1d'),
            'meta': self.fetch_funding_and_oi(symbol)
        }
        
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data_map = {}
        
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching {key} for {symbol}: {result}")
                return None
            data_map[key] = result
        
        for tf in ['15m', '1h', '4h', '1d']:
            if data_map[tf] is None or len(data_map[tf]) < 50:
                return None
        
        return data_map
""",

    "core/analyzer.py": """import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger(__name__)

class TechnicalAnalyzer:
    @staticmethod
    def add_indicators(df: pd.DataFrame):
        if df is None or len(df) < 50:
            return df
        
        try:
            df['EMA_7'] = ta.ema(df['close'], length=7)
            df['EMA_20'] = ta.ema(df['close'], length=20)
            df['EMA_50'] = ta.ema(df['close'], length=50)
            df['EMA_200'] = ta.ema(df['close'], length=200)
            
            bb = ta.bbands(df['close'], length=20, std=2)
            if bb is not None:
                df['BB_UPPER'] = bb.iloc[:, 0]
                df['BB_MIDDLE'] = bb.iloc[:, 1]
                df['BB_LOWER'] = bb.iloc[:, 2]
            
            df['RSI'] = ta.rsi(df['close'], length=14)
            
            macd = ta.macd(df['close'])
            if macd is not None:
                df['MACD'] = macd.iloc[:, 0]
                df['MACD_SIGNAL'] = macd.iloc[:, 1]
                df['MACD_HIST'] = macd.iloc[:, 2]
            
            adx = ta.adx(df['high'], df['low'], df['close'])
            df['ADX'] = adx['ADX_14']
            
            df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            df['VOL_SMA_20'] = ta.sma(df['volume'], length=20)
            
        except Exception as e:
            logger.error(f"添加指标出错: {e}")
        
        return df

    @staticmethod
    def detect_divergence(df, lookback=10):
        if len(df) < lookback:
            return 0
        
        try:
            current = df.iloc[-1]
            past = df.iloc[-lookback:-1]
            
            if current['close'] > past['close'].max() and current['RSI'] < past['RSI'].max():
                return -1
            if current['close'] < past['close'].min() and current['RSI'] > past['RSI'].min():
                return 1
        except:
            pass
        
        return 0
""",

    "core/volume_analyzer.py": """import pandas as pd

class VolumeAnalyzer:
    @staticmethod
    def analyze_volume(df, period=20):
        if df is None or len(df) < period:
            return {'signal': 'neutral', 'score': 0, 'desc': '数据不足'}
        
        try:
            current_vol = df['volume'].iloc[-1]
            avg_vol = df['volume'].iloc[-period:-1].mean()
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1
            
            result = {
                'current_volume': float(current_vol),
                'avg_volume': float(avg_vol),
                'volume_ratio': round(float(vol_ratio), 2)
            }
            
            if vol_ratio > 3:
                result['signal'] = 'extreme'
                result['score'] = 30
                result['desc'] = '天量成交'
            elif vol_ratio > 2:
                result['signal'] = 'high'
                result['score'] = 20
                result['desc'] = '显著放量'
            elif vol_ratio > 1.5:
                result['signal'] = 'moderate'
                result['score'] = 10
                result['desc'] = '温和放量'
            else:
                result['signal'] = 'normal'
                result['score'] = 0
                result['desc'] = '成交量正常'
            
            return result
        except:
            return {'signal': 'neutral', 'score': 0, 'desc': '分析失败'}

    @staticmethod
    def calculate_obv(df):
        obv = [0]
        for i in range(1, len(df)):
            if df['close'].iloc[i] > df['close'].iloc[i-1]:
                obv.append(obv[-1] + df['volume'].iloc[i])
            elif df['close'].iloc[i] < df['close'].iloc[i-1]:
                obv.append(obv[-1] - df['volume'].iloc[i])
            else:
                obv.append(obv[-1])
        df['OBV'] = obv
        return df
""",

    "core/market_sentiment.py": """import logging

logger = logging.getLogger(__name__)

class MarketSentiment:
    @staticmethod
    def get_funding_rate_analysis(current_funding):
        if current_funding is None:
            return {'signal': 'neutral', 'score': 0, 'desc': '无资金费率数据'}
        
        funding_pct = current_funding * 100
        
        if funding_pct > 0.05:
            return {'signal': 'overheated', 'score': -20, 'desc': f'资金费率{funding_pct:.3f}%过高'}
        elif funding_pct > 0.01:
            return {'signal': 'bullish', 'score': 10, 'desc': f'资金费率{funding_pct:.3f}%偏多'}
        elif funding_pct < -0.01:
            return {'signal': 'bearish', 'score': -10, 'desc': f'资金费率{funding_pct:.3f}%偏空'}
        else:
            return {'signal': 'neutral', 'score': 0, 'desc': f'资金费率{funding_pct:.3f}%正常'}
    
    @staticmethod
    def analyze_open_interest(oi_data, price_data):
        if oi_data is None or price_data is None:
            return {'signal': 'neutral', 'score': 0, 'desc': '数据不足'}
        
        try:
            price_current = price_data['close'].iloc[-1]
            price_prev = price_data['close'].iloc[-2]
            oi_current = oi_data.get('openInterest', 0)
            
            if price_current > price_prev and oi_current > 0:
                return {'signal': 'strong_trend', 'score': 15, 'desc': '价涨OI正常'}
            elif price_current > price_prev:
                return {'signal': 'weak_trend', 'score': 5, 'desc': '价涨但OI不足'}
            else:
                return {'signal': 'neutral', 'score': 0, 'desc': 'OI无明显异常'}
        except:
            return {'signal': 'neutral', 'score': 0, 'desc': '分析失败'}
""",

    "core/support_resistance.py": """import numpy as np

class SupportResistance:
    @staticmethod
    def calculate_pivot_points(high, low, close):
        pp = (high + low + close) / 3
        r1 = 2 * pp - low
        r2 = pp + (high - low)
        s1 = 2 * pp - high
        s2 = pp - (high - low)
        
        return {
            'pivot': pp,
            'resistance': [r1, r2],
            'support': [s1, s2]
        }
    
    @staticmethod
    def fibonacci_levels(high, low):
        diff = high - low
        return {
            '0.382': high - diff * 0.382,
            '0.5': high - diff * 0.5,
            '0.618': high - diff * 0.618
        }
""",

    "core/risk_manager.py": """from config import Config

class RiskManager:
    @staticmethod
    def calculate_position_size(entry, sl, balance=None):
        balance = balance or Config.ACCOUNT_BALANCE_FIXED
        risk_amt = balance * Config.DEFAULT_RISK_PER_TRADE
        diff = abs(entry - sl)
        if diff == 0:
            return 0, 0
        qty = risk_amt / diff
        return round(qty, 4), round(qty * entry, 2)

    @staticmethod
    def check_risk_reward(entry, sl, tp):
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        return round(reward / risk, 2) if risk != 0 else 0
""",

    "strategies/__init__.py": "# Strategies package\n",
    
    "strategies/strategies_full.py": """import pandas as pd
from core.analyzer import TechnicalAnalyzer
from core.volume_analyzer import VolumeAnalyzer
from core.market_sentiment import MarketSentiment
from core.support_resistance import SupportResistance
from config import Config

class FullStrategyEvaluator:
    def __init__(self, symbol, data_map):
        self.symbol = symbol
        self.data_map = data_map
        self.timeframes = {}
        
        for tf in Config.TIMEFRAMES:
            if tf in data_map and data_map[tf] is not None and not data_map[tf].empty:
                df = data_map[tf].copy()
                df = TechnicalAnalyzer.add_indicators(df)
                df = VolumeAnalyzer.calculate_obv(df)
                self.timeframes[tf] = df
        
        if '15m' in self.timeframes and self.timeframes['15m'] is not None and not self.timeframes['15m'].empty:
            self.current_price = self.timeframes['15m'].iloc[-1]['close']
        else:
            self.current_price = 0
        
        self.meta = data_map.get('meta', {})
        self.score_components = {}
        self.signals = []
        
    def evaluate_all(self):
        try:
            self._score_trend()
            self._score_momentum()
            self._score_volume()
            self._score_sentiment()
            self._score_patterns()
            
            self._check_pullback_strategy()
            self._check_breakout_strategy()
            
            total_score = sum([v.get('score', 0) for v in self.score_components.values()])
            total_score = min(100, max(0, total_score))
            
            sr = self._calculate_support_resistance()
            
            result = {
                "symbol": self.symbol,
                "price": self.current_price,
                "total_score": total_score,
                "score_breakdown": self.score_components,
                "support_resistance": sr,
                "meta": self.meta
            }
            
            if self.signals:
                result['signal'] = max(self.signals, key=lambda x: x.get('confidence', 0))
            
            return result
        except Exception as e:
            return None

    def _score_trend(self):
        score = 0
        reasons = []
        
        for tf in ['1d', '4h', '1h']:
            if tf not in self.timeframes:
                continue
            df = self.timeframes[tf]
            if df is None or len(df) < 2:
                continue
            
            latest = df.iloc[-1]
            
            if latest['close'] > latest.get('EMA_20', latest['close']):
                score += 5
                reasons.append(f'{tf}趋势向上')
            
            if latest.get('ADX', 0) > 25:
                score += 3
        
        self.score_components['trend'] = {
            'score': min(30, score),
            'reasons': reasons[:3]
        }
    
    def _score_momentum(self):
        score = 0
        reasons = []
        
        if '15m' in self.timeframes:
            df = self.timeframes['15m']
            rsi = df.iloc[-1].get('RSI', 50)
            if 50 < rsi < 70:
                score += 8
                reasons.append('RSI健康')
            elif rsi > 80:
                score -= 5
                reasons.append('RSI超买')
        
        self.score_components['momentum'] = {
            'score': max(-10, min(25, score)),
            'reasons': reasons
        }
    
    def _score_volume(self):
        if '15m' in self.timeframes:
            vol_analysis = VolumeAnalyzer.analyze_volume(self.timeframes['15m'])
            self.score_components['volume'] = {
                'score': vol_analysis.get('score', 0),
                'reasons': [vol_analysis.get('desc', '')]
            }
        else:
            self.score_components['volume'] = {'score': 0, 'reasons': []}
    
    def _score_sentiment(self):
        if self.meta and 'fundingRate' in self.meta:
            funding_analysis = MarketSentiment.get_funding_rate_analysis(self.meta['fundingRate'])
            self.score_components['sentiment'] = {
                'score': funding_analysis.get('score', 0),
                'reasons': [funding_analysis.get('desc', '')]
            }
        else:
            self.score_components['sentiment'] = {'score': 0, 'reasons': []}
    
    def _score_patterns(self):
        self.score_components['patterns'] = {'score': 0, 'reasons': []}
    
    def _check_pullback_strategy(self):
        if '4h' not in self.timeframes or '15m' not in self.timeframes:
            return
        
        h4 = self.timeframes['4h'].iloc[-1]
        m15 = self.timeframes['15m'].iloc[-1]
        
        if (h4['close'] > h4.get('EMA_20', 0) and 
            h4.get('ADX', 0) > 20 and
            m15['RSI'] < 45):
            
            atr = m15.get('ATR', m15['close'] * 0.02)
            
            self.signals.append({
                "type": "LONG",
                "name": "Pullback",
                "entry": float(m15['close']),
                "stop_loss": float(m15['close'] - 1.5 * atr),
                "take_profit": float(h4['close'] * 1.03),
                "confidence": 70,
                "desc": "4H上升趋势中的15M回调"
            })
    
    def _check_breakout_strategy(self):
        if '1h' not in self.timeframes or '15m' not in self.timeframes:
            return
        
        h1 = self.timeframes['1h']
        high_1h = h1['high'].iloc[-5:].max()
        current = self.timeframes['15m'].iloc[-1]
        
        if current['close'] > high_1h:
            atr = current.get('ATR', current['close'] * 0.02)
            self.signals.append({
                "type": "LONG",
                "name": "Breakout",
                "entry": float(current['close']),
                "stop_loss": float(high_1h * 0.99),
                "take_profit": float(current['close'] * 1.05),
                "confidence": 75,
                "desc": "放量突破1H阻力"
            })
    
    def _calculate_support_resistance(self):
        if '1d' not in self.timeframes:
            return {}
        
        df = self.timeframes['1d']
        high = df['high'].iloc[-5:].max()
        low = df['low'].iloc[-5:].min()
        close = df['close'].iloc[-1]
        
        return {
            'pivot': SupportResistance.calculate_pivot_points(high, low, close),
            'fibonacci': SupportResistance.fibonacci_levels(high, low)
        }
""",

    "database/__init__.py": "# Database package\n",
    
    "database/models.py": """import sqlite3
import pandas as pd
from datetime import datetime
from config import Config
import json

class DatabaseManager:
    def __init__(self, db_path=Config.DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    symbol TEXT,
                    signal_type TEXT,
                    signal_name TEXT,
                    price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence INTEGER,
                    total_score INTEGER,
                    metadata TEXT
                )
            ''')
    
    def save_signal(self, signal_data):
        with sqlite3.connect(self.db_path) as conn:
            signal = signal_data.get('signal', {})
            conn.execute('''
                INSERT INTO signals 
                (timestamp, symbol, signal_type, signal_name, price, 
                 stop_loss, take_profit, confidence, total_score, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(),
                signal_data['symbol'],
                signal.get('type', ''),
                signal.get('name', ''),
                signal_data['price'],
                signal.get('stop_loss', 0),
                signal.get('take_profit', 0),
                signal.get('confidence', 0),
                signal_data['total_score'],
                json.dumps(signal_data.get('score_breakdown', {}))
            ))
    
    def get_performance_stats(self, days=7):
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query('''
                SELECT 
                    COUNT(*) as total_signals,
                    AVG(confidence) as avg_confidence,
                    AVG(total_score) as avg_score,
                    MAX(total_score) as max_score,
                    COUNT(DISTINCT symbol) as unique_symbols
                FROM signals 
                WHERE timestamp > datetime('now', ?)
            ''', conn, params=(f'-{days} days',))
            return df.iloc[0].to_dict() if not df.empty else {}
""",

    "notification/__init__.py": "# Notification package\n",
    
    "notification/telegram_bot.py": """import aiohttp
from config import Config
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class TelegramNotifier:
    @staticmethod
    async def send_signal(signal_data):
        if not Config.TELEGRAM_BOT_TOKEN:
            return
        
        signal = signal_data.get('signal', {})
        scores = signal_data.get('score_breakdown', {})
        
        msg = f\"\"\"
【信号】 {signal_data['symbol']} {signal.get('type', '')}
评分: {signal_data['total_score']}/100
策略: {signal.get('name', '未知')}
置信度: {signal.get('confidence', 0)}%

【操作建议】
• 入场: {signal.get('entry', 0):.4f}
• 止损: {signal.get('stop_loss', 0):.4f}
• 目标: {signal.get('take_profit', 0):.4f}
• 风报比: {signal_data.get('risk', {}).get('risk_reward', 0)}:1

时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
\"\"\"
        
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id": Config.TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML"
                })
        except Exception as e:
            logger.error(f"发送Telegram消息失败: {e}")

    @staticmethod
    async def send_daily_summary(stats):
        if not Config.TELEGRAM_BOT_TOKEN:
            return
        
        msg = f\"\"\"
【每日总结】
日期: {datetime.now().strftime('%Y-%m-%d')}

• 总信号数: {stats.get('total_signals', 0)}
• 平均评分: {stats.get('avg_score', 0):.1f}
• 最高评分: {stats.get('max_score', 0)}
• 涉及币种: {stats.get('unique_symbols', 0)}个
\"\"\"
        
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id": Config.TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML"
                })
        except Exception as e:
            logger.error(f"发送每日总结失败: {e}")
"""
}

def create_project():
    """创建所有项目文件和目录"""
    print("=" * 50)
    print("币安期货交易决策系统 - 一键安装脚本")
    print("=" * 50)
    
    # 创建目录
    dirs = ['core', 'strategies', 'database', 'notification']
    for dir_name in dirs:
        os.makedirs(dir_name, exist_ok=True)
        print(f"创建目录: {dir_name}/")
    
    # 创建文件
    for file_path, content in FILES.items():
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"创建文件: {file_path}")
    
    print("\n" + "=" * 50)
    print("文件创建完成！")
    print("\n下一步操作:")
    print("1. 安装依赖: pip install -r requirements.txt")
    print("2. 修改配置: 编辑 .env 文件填入你的API密钥")
    print("3. 运行系统: python main.py")
    print("=" * 50)

def install_dependencies():
    """安装依赖包"""
    print("\n正在安装依赖包...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("依赖安装完成！")
    except Exception as e:
        print(f"依赖安装失败: {e}")
        print("请手动运行: pip install -r requirements.txt")

if __name__ == "__main__":
    # 创建项目文件
    create_project()
    
    # 询问是否安装依赖
    response = input("\n是否现在安装依赖包？(y/n): ")
    if response.lower() == 'y':
        install_dependencies()
    
    print("\n安装完成！现在可以运行: python main.py")