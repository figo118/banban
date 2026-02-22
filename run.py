import multiprocessing
import time
import logging
import uvicorn
import signal
import sys
from main import TradingSystem

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Launcher")

# 全局进程对象
bot_process = None
web_process = None

def run_bot():
    """运行交易机器人扫描引擎"""
    # [核心优化]: 子进程忽略终端的 Ctrl+C 信号，生命周期完全交由主进程的 terminate() 管理
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    logger.info("正在启动交易机器人扫描引擎...")
    bot = TradingSystem(is_paper_trading=True)
    
    try:
        while True:
            try:
                bot.run_cycle()
                # 使用更小的sleep间隔，以便更快响应主进程的终止命令
                for _ in range(15):
                    time.sleep(1)
            except Exception as e:
                logger.error(f"机器人引擎异常: {e}")
                time.sleep(10)
    except Exception as e:
        logger.info(f"交易机器人进程退出: {e}")

def run_web_api():
    """运行 FastAPI 数据接口 (Port 8000)"""
    # [核心优化]: 子进程忽略终端的 Ctrl+C 信号，防止 uvicorn 和主进程抢夺控制权
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    logger.info("正在启动 FastAPI Web 数据接口 (Port 8000)...")
    # 使用 uvicorn 运行 FastAPI 实例
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, log_level="info")

def signal_handler(signum, frame):
    """处理全局信号 (仅由主进程执行)"""
    logger.info(">>> 收到终止信号，正在退出系统 <<<")
    
    if bot_process and bot_process.is_alive():
        logger.info("终止交易机器人进程...")
        bot_process.terminate()
        
    if web_process and web_process.is_alive():
        logger.info("终止Web API进程...")
        web_process.terminate()
    
    # 等待进程安全回收，防止成为僵尸进程
    if bot_process:
        bot_process.join(timeout=5)
    if web_process:
        web_process.join(timeout=5)
    
    logger.info(">>> 系统已完全退出 <<<")
    sys.exit(0)

if __name__ == "__main__":
    # 仅在主进程设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建进程，设置daemon=True，这样主进程异常崩溃时子进程也会自动死亡
    bot_process = multiprocessing.Process(target=run_bot, daemon=True)
    web_process = multiprocessing.Process(target=run_web_api, daemon=True)

    try:
        bot_process.start()
        web_process.start()

        logger.info(">>> 系统启动成功：FastAPI 正在监听 8000 端口 <<<")
        logger.info(">>> 按 Ctrl+C 可以安全退出整个系统 <<<")
        
        # 主进程保持运行，充当守护者
        while True:
            time.sleep(1)

    except Exception as e:
        logger.error(f"系统异常: {e}")
        signal_handler(signal.SIGTERM, None)