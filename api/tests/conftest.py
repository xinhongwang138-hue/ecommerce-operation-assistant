"""课程测试只使用内存数据库，不读取课堂数据库连接。"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "development"
os.environ["AUTH_SECRET_KEY"] = "course-test-secret-not-for-deployment"
# 花费配额默认关闭：否则整套测试会往真实 Redis 累加当日计数器，跨次运行互相污染并偶发 429。
# 配额本身有专门的测试（test_spend_quota.py），那里按需显式打开。
os.environ["LLM_DAILY_CALLS_PER_WORKSPACE"] = "0"
os.environ["LLM_DAILY_CALLS_TOTAL"] = "0"
