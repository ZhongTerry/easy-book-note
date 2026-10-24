# 更新器验证记录

时间：2026-10-24，Asia/Shanghai。

## 已完成

- 本机完整 unittest：100 项通过，其中新增更新器测试 8 项。
- 腾讯云隔离临时目录：同一组更新器测试 8 项通过，使用生产 Python 3.12.3。
- 真实项目只读预演：候选代码 5,378,193 字节，Git 工作树及 HEAD 不变，无停止/启动命令执行。
- Linux 并发锁：已有锁时第二个更新进程被拒绝。
- 独立临时 Gunicorn 站点：旧版本成功升级到新版本；另一个故意返回 503 的版本被拒绝，
  自动回滚后再次健康，Git HEAD 恢复，SQLite 用户记录保留。临时服务已停止、临时目录已清理。
- 旧的父目录、src 目录 smart_pull.sh 均有时间戳备份，再替换为新更新器入口。
- 新入口的 bash 语法检查及安装后预演通过。

## 生产安装

- 工具：`/www/wwwroot/book.ztrztr.top/notedb-updater/`
- 状态：`/www/wwwroot/book.ztrztr.top/notedb-update-state/`
- 旧脚本备份：工具目录下 `legacy/parent-smart_pull-20261024-205847.sh`、
  `legacy/src-smart_pull-20261024-205847.sh`。
- 更新器 SHA-256：`c00772f7fc6b4009922a2477c23b74f52e6447c19e642692ea92bfa064803760`。
- 只配置主站 src；node1 未接管。

生产业务版本保持 `4eb5703bd1c197e7420838277a6ce79eba68aa2e`，Gunicorn master PID
保持 `1359073`，最终健康检查 HTTP 200 / status ok，未产生生产发布快照。
本次安装没有提交/推送本地业务改动，没有执行生产代码更新或重启。

这不是生产升级演练：真实服务停/启和失败回滚只在独立临时站点验证过。
正式应用依赖、配置或数据迁移时仍需按 README 审核兼容性。
