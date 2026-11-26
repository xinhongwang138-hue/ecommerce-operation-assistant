import os
import sys
from pathlib import Path

# 直接执行 scripts/bootstrap_user.py 时，Python 默认只搜索 scripts/。
# 把 api/ 项目根目录加入搜索路径后，下面的 from app... 导入才能找到同级 app/。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models import UserRole
from app.services.bootstrap import ensure_bootstrap_user


def main() -> int:
    email = os.getenv("BOOTSTRAP_EMAIL", "").strip()
    password = os.getenv("BOOTSTRAP_PASSWORD", "")
    display_name = os.getenv("BOOTSTRAP_DISPLAY_NAME", "Owner").strip()
    if not email or not password:
        print("请设置 BOOTSTRAP_EMAIL 和 BOOTSTRAP_PASSWORD 后再初始化首个账号。")
        return 1
    with SessionLocal() as db:
        user, created = ensure_bootstrap_user(
            db,
            email=email,
            display_name=display_name,
            password=password,
            role=UserRole.OWNER,
        )
        # 会话关闭后实例即脱离 Session，先取出打印所需字段，避免已存在路径触发 DetachedInstanceError。
        result_email = user.email
    print(f"{'已创建' if created else '已存在'}初始化账号：{result_email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
