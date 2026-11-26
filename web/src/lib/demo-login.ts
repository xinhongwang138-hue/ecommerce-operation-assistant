/**
 * 演示快捷登录用到的账号与口令。
 *
 * 仓库里不存任何明文口令：口令只从环境变量 `DEMO_LOGIN_PASSWORD` 读，
 * 读不到就不渲染入口、服务端也拒绝。这与 `seed_demo_data.py` 的约定一致——
 * 建账号脚本同样只认环境变量，不在代码里内置口令。
 */

/** 演示账号。改 `DEMO_LOGIN_EMAIL` 可指向自己建的账号。 */
export const DEMO_ACCOUNT_EMAIL = (process.env.DEMO_LOGIN_EMAIL ?? "demo.owner@example.com")
  .trim()
  .toLowerCase();

/** 演示账号口令；未配置时返回 null。 */
export function demoLoginPassword(): string | null {
  const value = process.env.DEMO_LOGIN_PASSWORD?.trim();
  return value ? value : null;
}

/** 是否展示快捷登录入口：只有配了口令才展示，避免给出点了会失败的按钮。 */
export function demoLoginEnabled(): boolean {
  return demoLoginPassword() !== null;
}
