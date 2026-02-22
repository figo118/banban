#!/usr/bin/env python3
"""API连接诊断工具"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import ccxt.async_support as ccxt

async def test_connection():
    print("=" * 60)
    print("币安API连接诊断")
    print("=" * 60)
    print()
    
    try:
        print("1. 初始化ccxt binanceusdm...")
        exchange = ccxt.binanceusdm({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        print("   ✓ 初始化成功")
        print()
        
        print("2. 测试基础网络连接...")
        try:
            markets = await exchange.load_markets()
            print(f"   ✓ 成功加载 {len(markets)} 个市场")
        except Exception as e:
            print(f"   ✗ 加载市场失败: {type(e).__name__}: {str(e)}")
            return False
        print()
        
        print("3. 测试获取tickers...")
        try:
            tickers = await exchange.fetch_tickers()
            print(f"   ✓ 成功获取 {len(tickers)} 个交易对")
            
            # 显示几个示例
            usdt_tickers = {k: v for k, v in tickers.items() if '/USDT' in k}
            print(f"   ✓ 找到 {len(usdt_tickers)} 个USDT交易对")
            
            if usdt_tickers:
                first_5 = list(usdt_tickers.keys())[:5]
                print(f"   示例: {', '.join(first_5)}")
        except Exception as e:
            print(f"   ✗ 获取tickers失败: {type(e).__name__}: {str(e)}")
            import traceback
            print()
            print("详细错误:")
            print(traceback.format_exc())
            return False
        print()
        
        print("4. 测试单个交易对...")
        try:
            if usdt_tickers:
                symbol = list(usdt_tickers.keys())[0]
                print(f"   测试交易对: {symbol}")
                ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=10)
                print(f"   ✓ 成功获取 {len(ohlcv)} 条K线数据")
        except Exception as e:
            print(f"   ✗ 获取K线失败: {type(e).__name__}: {str(e)}")
        print()
        
        await exchange.close()
        print("=" * 60)
        print("✓ API连接诊断完成 - 一切正常!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"✗ 诊断失败: {type(e).__name__}: {str(e)}")
        print("=" * 60)
        print()
        print("可能的解决方案:")
        print("1. 检查网络连接")
        print("2. 检查防火墙设置")
        print("3. 尝试使用代理")
        print("4. 确认Binance API在您的地区可用")
        print()
        import traceback
        print("详细错误信息:")
        print(traceback.format_exc())
        return False

if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
