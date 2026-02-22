import time
import logging
import pandas as pd
from datetime import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from database.models import SessionLocal, Base, engine, VirtualPosition
from core.data_engine import DataEngine
from core.analyzer import Analyzer
from core.risk_manager import RiskManager
from core.paper_engine import PaperEngine
from strategies.strategies_full import TrendFollowingStrategy
from config import API_KEY, API_SECRET, SYMBOLS_COUNT

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TradingSystem")

class TradingSystem:
    def __init__(self, is_paper_trading=True):
        # 初始化数据库
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        
        # 初始化核心组件
        self.data_engine = DataEngine(API_KEY, API_SECRET)
        self.data_engine.start_websocket()
        
        self.analyzer = Analyzer()
        self.risk_manager = RiskManager(None)
        self.strategy = TrendFollowingStrategy(None)
        
        # 模拟盘引擎
        self.is_paper_trading = is_paper_trading
        if self.is_paper_trading:
            self.paper_engine = PaperEngine(self.db)
            logger.info(">>> 1000U 模拟交易模式已启动 <<<")

    def _process_symbol(self, symbol, current_equity):
        """
        处理单个币种的逻辑
        """
        from database.models import SessionLocal
        
        # 为每个线程创建独立的数据库会话
        thread_db = SessionLocal()
        
        try:
            df_1h = self.data_engine.get_kline(symbol, interval='1h', limit=200)
            df_15m = self.data_engine.get_kline(symbol, interval='15m', limit=100)
            df_4h = self.data_engine.get_kline(symbol, interval='4h', limit=50) # 顺手拉取4H供雷达展示
            
            if df_15m is None or df_1h is None:
                return None
            
            # 计算指标 (极其消耗CPU的操作，现在每分钟只做一次)
            df_1h = self.analyzer.add_indicators(df_1h)
            df_15m = self.analyzer.add_indicators(df_15m)
            if df_4h is not None:
                df_4h = self.analyzer.add_indicators(df_4h)
            
            curr_15m = df_15m.iloc[-1]
            latest_price = curr_15m['close']
            
            # 检查持仓
            current_position_size = 0
            is_reduced = False
            try:
                open_positions = thread_db.query(VirtualPosition).filter_by(symbol=symbol, status="OPEN").all()
                if open_positions:
                    current_position_size = sum(pos.quantity for pos in open_positions)
                    is_reduced = any(pos.is_reduced for pos in open_positions)
            except Exception as db_error:
                logger.error(f"查询持仓失败 {symbol}: {db_error}")
            
            # 策略决策
            decision, details = self.strategy.get_decision(symbol, df_1h, df_15m, current_position_size, is_reduced)
            
            # [核心修复] 切断跨线程调用，将开仓信号打包返回给主线程执行
            trade_signal = None
            if decision == 'LONG_ENTRY':
                trade_signal = {
                    'symbol': symbol,
                    'side': 'LONG',
                    'price': latest_price,
                    'atr': curr_15m.get('atr', 0),
                    'details': details
                }
            
            # ==========================================
            # [方案B核心] 提取雷达指标并存入列表
            # ==========================================
            m15_change = round(((curr_15m['close'] - df_15m.iloc[-2]['close']) / df_15m.iloc[-2]['close']) * 100, 2) if len(df_15m) >= 2 else 0
            h1_change = round(((df_1h.iloc[-1]['close'] - df_1h.iloc[-2]['close']) / df_1h.iloc[-2]['close']) * 100, 2) if df_1h is not None and len(df_1h) >= 2 else m15_change * 4
            h4_change = round(((df_4h.iloc[-1]['close'] - df_4h.iloc[-2]['close']) / df_4h.iloc[-2]['close']) * 100, 2) if df_4h is not None and len(df_4h) >= 2 else m15_change * 16
            d1_change = round(((df_1h.iloc[-1]['close'] - df_1h.iloc[-24]['close']) / df_1h.iloc[-24]['close']) * 100, 2) if df_1h is not None and len(df_1h) >= 24 else h4_change * 6

            radar_item = {
                "symbol": symbol,
                "price": float(curr_15m['close']),
                "m15": float(m15_change),
                "h1": float(h1_change),
                "h4": float(h4_change),
                "d1": float(d1_change),
                "score": int(details.get('total_score', details.get('score', 0))),
                "rsi": round(float(curr_15m.get('rsi', 50)), 1),
                "vol_ratio": round(float(curr_15m.get('vol_ratio', 100)), 0),
                "bb_pct": round(float(curr_15m.get('bb_pct', 50)), 1),
                "dist_ema20": round(float(curr_15m.get('dist_ema20', 0)), 2),
                "adx": round(float(curr_15m.get('adx', 0)), 1),
                "special_engine": details.get('special_engine', None)
            }
            
            # 将 trade_signal 一并返回
            return (symbol, latest_price, radar_item, trade_signal)
            
        except Exception as e:
            logger.error(f"处理 {symbol} 失败: {e}")
            return None
        finally:
            # 确保数据库会话被关闭
            try:
                thread_db.close()
            except:
                pass

    def run_cycle(self):
        """
        单次市场扫描与逻辑处理循环
        """
        start_time = time.time()
        logger.info("--- 开启新一轮市场扫描 ---")
        
        # 1. 获取账户净值
        acc_info = self.paper_engine.get_balance()
        current_equity = acc_info['totalEquity']
        
        # 2. 获取目标币种（涨幅榜 + 持仓币种）
        top_symbols = self.data_engine.get_top_gainers(limit=SYMBOLS_COUNT)
        latest_prices = {}
        
        # [方案B核心]: 准备装载雷达数据的容器
        radar_data_list = []
        
        # 获取所有持仓的交易对，确保它们也会被加到雷达
        open_positions = self.db.query(VirtualPosition).filter_by(status="OPEN").all()
        holding_symbols = set(pos.symbol for pos in open_positions)
        
        # 合并涨幅榜和持仓，去重
        all_symbols = []
        seen = set()
        
        # 先加持仓，确保持仓在前面
        for symbol in holding_symbols:
            if symbol not in seen:
                seen.add(symbol)
                all_symbols.append(symbol)
        
        # 再加涨幅榜
        for symbol in top_symbols:
            if symbol not in seen:
                seen.add(symbol)
                all_symbols.append(symbol)
        
        # 3. 并行处理多个币种
        trade_signals_to_execute = [] # 新增：用于收集主线程执行的订单
        
        if all_symbols:
            # 使用线程池并行处理
            max_workers = min(5, len(all_symbols))  # 最多5个线程
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                futures = {
                    executor.submit(self._process_symbol, symbol, current_equity): symbol 
                    for symbol in all_symbols
                }
                
                # 收集结果
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        # [核心修复] 适配新的返回值长度 4
                        if result and isinstance(result, tuple) and len(result) == 4:
                            symbol, price, radar_item, trade_signal = result
                            latest_prices[symbol] = price
                            radar_data_list.append(radar_item)
                            
                            # 收集需要开仓的信号
                            if trade_signal:
                                trade_signals_to_execute.append(trade_signal)
                        else:
                            logger.error(f"处理结果格式错误: {result}")
                    except Exception as e:
                        logger.error(f"处理future结果失败: {e}")
        
        logger.info(f"并行处理完成，处理了 {len(radar_data_list)} 个币种，耗时 {time.time() - start_time:.2f} 秒")

        # ==========================================
        # [核心修复] 回到主线程，安全执行所有下单动作
        # ==========================================
        for signal in trade_signals_to_execute:
            self.execute_paper_trade(
                symbol=signal['symbol'], 
                side=signal['side'], 
                price=signal['price'], 
                atr=signal['atr'], 
                current_equity=current_equity, 
                details=signal['details']
            )

        # ==========================================
        # [方案B核心] 写入雷达缓存 JSON 文件
        # ==========================================
        try:
            with open('radar_cache.json', 'w', encoding='utf-8') as f:
                json.dump(radar_data_list, f, ensure_ascii=False)
            # logger.info(f"成功将 {len(radar_data_list)} 个币种写入雷达缓存。")
        except Exception as e:
            logger.error(f"写入雷达缓存失败: {e}")

        # 3. 获取所有当前持仓的币种的最新价格 (用于止盈止损)
        open_positions = self.db.query(VirtualPosition).filter_by(status="OPEN").all()
        position_symbols = set(pos.symbol for pos in open_positions)
        
        if position_symbols:
            all_tickers = self.data_engine.get_all_tickers()
            for symbol in position_symbols:
                if symbol not in latest_prices:
                    try:
                        if symbol in all_tickers:
                            latest_prices[symbol] = all_tickers[symbol]['price']
                        else:
                            df_15m = self.data_engine.get_kline(symbol, interval='15m', limit=2)
                            if df_15m is not None and len(df_15m) > 0:
                                latest_prices[symbol] = df_15m['close'].iloc[-1]
                    except Exception as e:
                        logger.error(f"获取持仓币种 {symbol} 价格失败: {e}")
                        continue

        # 4. 同步盈亏
        if self.is_paper_trading:
            self.paper_engine.sync_positions(latest_prices)
        
        logger.info(f"循环结束。当前净值: {current_equity:.2f} USDT")

    # 调整入参，直接接收 price, atr 和 details
    def execute_paper_trade(self, symbol, side, price, atr, current_equity, details):
        # 严格保留 RiskManager 的资金管理与仓位计算逻辑
        qty = self.risk_manager.calculate_position_size(current_equity, price, atr)
        
        if qty > 0:
            # [核心修复] 废除 RiskManager 的死板出口，直接提取策略大脑的"结构防守止损"
            initial_sl = details.get('suggested_sl')
            trailing_sl = details.get('trailing_sl')
            
            # 计算移动止损的安全距离
            trailing_distance = (price - trailing_sl) if trailing_sl else None
            
            # 计算止盈价格 (5倍ATR)
            take_profit = price + (atr * 3.0)
            
            self.paper_engine.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                score=details.get('score', 0),
                stop_loss=initial_sl,
                take_profit=take_profit, # 设置止盈价格，供前端显示
                trend_stop_line=None, # 已废弃
                trailing_distance=trailing_distance
            )

def main():
    """供 run.py 调用的入口函数"""
    bot = TradingSystem(is_paper_trading=True)
    while True:
        try:
            bot.run_cycle()
            time.sleep(15)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"系统崩溃重试: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
