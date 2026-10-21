/**
 * 前端自己的版本号（v2.4.0 起，2026-10-15 用户要求 ✓）。
 *
 * 用户原话：「你按照 trader_code 那样顶栏那个版本号『前后端版本不一致就标黄』，这样我就知道
 *   你有没有重启了；还有一层『改版本号忘了同步』的自动检查。」
 *
 * 为什么前端要**自己带一份**（而不是只显示后端 `/api/version` 返回的 ✗）：
 *   只显示后端版本 ⇒ **前端是旧静态资源**时你根本看不出来 ✗（浏览器缓存 / vite 没热更 /
 *   后端没重启 —— 三种情况在界面上长得一模一样 ✗）⇒ 两边各带一份、**不一致就把顶栏标黄** ✓。
 *
 * ⚠ 改版本号时必须四处一致 ✓：
 *   ① 本文件 `FE_VERSION`；② `frontend/package.json` 的 `version`；
 *   ③ `backend/app/__init__.py` 的 `__version__`（**唯一版本来源** ✓）；
 *   ④ `md/change_log.md` 追加该版本小节（Keep a Changelog ✓）。
 *   —— ①/②/③ 由 `backend/tests/test_version_sync.py` 守着（漏同步 CI 直接红 ✓）。
 */
export const FE_VERSION = '2.10.74'
