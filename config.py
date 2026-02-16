"""
应用配置常量
统一管理应用中使用的常量，避免魔法数字和硬编码
"""
import os

# ==========================================
# 基础配置
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LIB_DIR = os.path.join(BASE_DIR, "library")
DL_DIR = os.path.join(BASE_DIR, "downloads")

# ==========================================
# 数据库配置
# ==========================================
DB_PATH = os.path.join(USER_DATA_DIR, "data.sqlite")
DOMAIN_CACHE_FILE = os.path.join(USER_DATA_DIR, 'domain_verification_cache.json')

# ==========================================
# 缓存配置
# ==========================================
CACHE_EXPIRY_SECONDS = 30 * 24 * 3600  # 30天
HISTORY_MAX_RECORDS = 50  # 历史记录最大保存数量
VERSION_HISTORY_KEEP = 10  # 版本历史保留数量

# ==========================================
# 爬虫配置
# ==========================================
DEFAULT_CRAWLER_TIMEOUT = 10  # 默认超时时间(秒)
DEFAULT_CRAWLER_IMPERSONATE = "chrome110"  # 默认浏览器指纹
MAX_RETRY_ATTEMPTS = 3  # 最大重试次数
CHAPTER_CONTENT_MIN_LENGTH = 100  # 章节内容最小长度
CHAPTER_LIST_MIN_COUNT = 3  # 目录最小章节数
TOC_MIN_CHAPTERS = 20  # 完整目录最小章节数

# ==========================================
# 并发配置
# ==========================================
DEFAULT_MAX_WORKERS = 12  # 默认最大并发数
DOWNLOAD_INTERVAL = 0.5  # 下载间隔(秒)
CLUSTER_TASK_TIMEOUT = 25  # 集群任务超时(秒)
SPEEDTEST_TIMEOUT = 5.5  # 测速超时(秒)

# ==========================================
# 会话配置
# ==========================================
SESSION_LIFETIME_DAYS = 30  # 会话有效期(天)
SESSION_COOKIE_NAME = 'simplenote_session'

# ==========================================
# 定时任务配置
# ==========================================
CACHE_CLEANUP_INTERVAL = 86400  # 缓存清理间隔(秒): 1天
AUTO_CHECK_INTERVAL = 18000  # 自动追更检查间隔(秒): 5小时
AUTO_CHECK_RANDOM_SLEEP_MIN = 3  # 自动追更随机休眠最小值(秒)
AUTO_CHECK_RANDOM_SLEEP_MAX = 8  # 自动追更随机休眠最大值(秒)

# ==========================================
# Redis 配置
# ==========================================
REDIS_TASK_QUEUE_KEY = "crawler:queue:pending"
REDIS_NODE_TTL = 60  # 节点信息过期时间(秒)
REDIS_RESULT_TTL = 60  # 结果过期时间(秒)
REDIS_SPEEDTEST_TTL = 300  # 测速结果保留时间(秒)

# ==========================================
# 黑名单配置
# ==========================================
DOMAIN_BLACKLIST = {
    'baidu.com', 'tieba.baidu.com', 'zhidao.baidu.com', 'wenku.baidu.com',
    'zhihu.com', 'douban.com', '163.com', 'qq.com', 'sina.com.cn',
    'amazon.cn', 'dangdang.com', 'jd.com', 'tmall.com', 'taobao.com',
    'facebook.com', 'twitter.com', 'youtube.com', 'bilibili.com'
}

# 白名单配置
TRUSTED_DOMAINS = [
    '22biqu.com', 'sxgread.com', 'fanqienovel.com',
    'xbqg77.com', 'qidian.com', 'zongheng.com', 'ciweimao.com',
]

# ==========================================
# HTTP 配置
# ==========================================
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 10  # HTTP 请求超时(秒)

# ==========================================
# 权限配置
# ==========================================
ROLE_GUEST = "guest"
ROLE_USER = "user"
ROLE_PRO = "pro"
ROLE_ADMIN = "admin"

# ==========================================
# OAuth 配置
# ==========================================
DEFAULT_AUTH_SERVER = 'https://auth.ztrztr.top'
DEFAULT_CALLBACK = 'https://book.ztrztr.top/callback'

# ==========================================
# 日志配置
# ==========================================
LOG_FILE = os.path.join(BASE_DIR, "debug.txt")
DEFAULT_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
