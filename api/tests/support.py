"""测试共用的内存库与 get_db 依赖替身。

`_engine` 与 `_override_db` 此前在 14 个测试文件里各有一份，彼此只在 SQLite URI 的
写法（StaticPool 下 `sqlite://` 与 `sqlite+pysqlite:///:memory:` 等价）和内部闭包函数名
上不同，属于纯粹的复制粘贴，因此合并到此处。

`_user` 故意**没有**合并：它在各文件里的语义确实不同——id 是固定 1 还是显式传入、
email 用 `role.value` 还是枚举 repr、display_name 三种拼法。强行统一要改几十处调用点，
并可能改变依赖 email 的断言，收益不抵风险。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def make_engine():
    """创建一个进程内共享的内存 SQLite 引擎。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def make_session_override(engine):
    """生成覆盖 get_db 用的依赖函数，让请求与断言共用同一个内存库会话。"""

    def override_db():
        with Session(engine) as db:
            yield db

    return override_db
