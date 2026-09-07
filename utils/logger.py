import logging
import sys
import os
from datetime import datetime

# 获取应用根目录 (假设 utils 文件夹在根目录下)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "debug.txt")

# 从环境变量或配置文件读取日志级别，默认为 INFO
# 可选值: DEBUG, INFO, WARNING, ERROR, CRITICAL
DEFAULT_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# 创建一个自定义的日志记录器
class NoteDBLogger:
    def __init__(self, name="noteDB"):
        self.logger = logging.getLogger(name)
        
        # 转换并设置日志级别
        level = getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO)
        self.logger.setLevel(level)
        
        # 防止重复添加 handler
        if not self.logger.handlers:
            # 格式化器: 2024-01-01 12:00:00 [INFO] [Module] Message
            # 添加模块名和行号方便深度调试
            full_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            
            # 终端输出: 只显示级别和消息
            console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

            # 文件输出: 显示完整时间戳和模块
            file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
            file_handler.setFormatter(full_formatter)
            self.logger.addHandler(file_handler)

    def _format_msg(self, module, msg):
        return f"[{module}] {msg}"

    def debug(self, module, msg):
        self.logger.debug(self._format_msg(module, msg))

    def info(self, module, msg):
        self.logger.info(self._format_msg(module, msg))

    def warn(self, module, msg):
        self.logger.warning(self._format_msg(module, msg))

    def error(self, module, msg):
        self.logger.error(self._format_msg(module, msg))

    def set_level(self, level_name):
        level = getattr(logging, level_name.upper(), logging.INFO)
        self.logger.setLevel(level)
        self.info("Logger", f"Log level changed to {level_name.upper()}")

# 单例模式
logger = NoteDBLogger()

# 导出便捷函数
def debug(module, msg): logger.debug(module, msg)
def info(module, msg): logger.info(module, msg)
def warn(module, msg): logger.warn(module, msg)
def error(module, msg): logger.error(module, msg)
