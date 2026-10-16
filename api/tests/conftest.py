"""课程测试只使用内存数据库，不读取课堂数据库连接。"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "development"
os.environ["AUTH_SECRET_KEY"] = "course-test-secret-not-for-deployment"
