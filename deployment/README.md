# NoteDB 服务器代码更新器

Python 标准库实现，Linux / Python 3.9+。用生产环境相同的 Python 版本执行。
默认只预演，不停服、不覆盖应用代码。无需第三方 GitHub 镜像，不修改 origin。

## 腾讯云部署情况

2026-10-24 只读核实：主站目录 `/www/wwwroot/book.ztrztr.top/src`，分支
`master`，Gunicorn 配置 `gunicorn_conf.py`，PID 文件 `gunicorn.pid`，
Python `/www/server/pyporject_evn/versions/3.12.3/bin/python3.12`，端口 5000。
`tencent-bj.example.json` 基于此快照，安装前应重新核对。
`node1` 是另一份部署，不会连带更新；先单独配置节点的停止、启动和健康检查。

端口 5000 只供本机 Nginx 反向代理使用。线上 `gunicorn_conf.py` 必须绑定
`127.0.0.1:5000`，防火墙不得放行 5000；公网入口只有 Nginx 的 80/443。
该配置属于受保护的运行时文件，更新器不会从 Git 覆盖它。每次维护后应确认
5000 没有公网监听，避免外部扫描连接耗尽 Gunicorn worker。

## 安装（不执行更新）

将 `update.py`、`service_control.py` 安装在代码目录外的
`/www/wwwroot/book.ztrztr.top/notedb-updater/`，避免正在运行的更新工具被候选版本替换。
将示例配置复制为该目录下的 `config.json`。该配置不包含密码。
工具和配置应仅允许部署管理员修改；状态目录由部署管理员持有，权限 0700。
不要将状态目录暴露成静态站点；它含数据库备份。

原 `smart_pull.sh` 应先保留带时间戳的副本，再改为以下入口。不要继续使用旧的
`reset --hard` / 覆盖还原 `user_data` 流程。入口可以放在主站 src 目录，也可单独命名。

```bash
#!/usr/bin/env bash
set -euo pipefail
exec /www/server/pyporject_evn/versions/3.12.3/bin/python3.12 \
  /www/wwwroot/book.ztrztr.top/notedb-updater/update.py \
  --config /www/wwwroot/book.ztrztr.top/notedb-updater/config.json "$@"
```

## 日常使用

```bash
# 联网拉取并预演：显示完整目标 SHA、增加/修改/删除的代码清单
bash smart_pull.sh --fetch

# 审核预演清单后，应用同一个完整 SHA（不是 main/master 等可变名字）
bash smart_pull.sh --apply --revision <预演显示的完整SHA>

# 预演回退到一个指定的旧提交，再按同样方式 --apply
bash smart_pull.sh --revision <旧提交完整SHA>

# 对中断、回滚失败的事务，先检查 manifest 和数据库兼容性，再执行恢复
bash smart_pull.sh --apply --recover release-YYYYMMDD-HHMMSS-xxxxxxxx
```

`--fetch` 受 Git 命令超时限制。失败即停止，不自动改源或切换镜像。
不自动安装依赖：requirements.txt 变更默认中止；须先人工安装、检查新旧版本兼容性，
再对该 SHA 使用 `--dependencies-reviewed`。这不是自动维护独立依赖环境的蓝绿部署。

## 实际保障与边界

- 隔离解包候选代码，拒绝符号链接/越界路径；编译检查不导入应用，不启动后台线程。
- 未提交的受管代码修改、未跟踪文件冲突、分支不符、磁盘不足均拒绝更新。
- 即使 Git 跟踪过运行数据，也跳过 `user_data*`、cache、library、downloads、
  `.env*`、`config.env*`、数据库、日志、PID 和本机 Gunicorn/uWSGI 配置。
- 新版本若更改配置要求，需另行审核；跳过配置文件不代表新版本不需要新配置。
- 部署前锁覆盖整个流程。停服命令完成后要求监听端口释放，随后用 SQLite backup API
  保存并校验数据库；只备份本次变更/删除的旧代码，不复制下载文件或大日志。
- 更新时按文件原子替换，但整个目录不是一次原子切换：服务在全部文件更换期间停止。
  manifest 记录事务；中断事务阻止后续更新，提供显式恢复命令。断电时需先检查恢复材料。
- 启动后直连本机 JSON `/healthz`，要求 HTTP 200 且 `status=ok`。
  它是应用就绪检查，不是完整的正文识别或多节点端到端验收。
- 健康失败尝试回滚代码并再次验证。数据库结构变化时中止自动回滚、保持停服。
  不自动回写数据库备份，避免覆盖用户更新的数据。非 schema 数据迁移仍需发布前人工审核。
- 其他进程仍可能写数据库，尤其不能让 node1 与主站共用同一运行数据目录；
  schema 迁移前必须另行停掉所有相关写入者。
- 成功后保留最近 3 份已完成快照；失败/未完成快照保留待人工处理，不自动删除唯一恢复材料。
  日志约 2 MiB 上限。历史数据库备份占用大致为数据库大小乘保留份数，预演不产生数据库备份。
- `service_control.py` 只向 PID 文件指定且 cwd 匹配的 Gunicorn 发 SIGTERM，不按名称批量杀进程，
  不升级为 SIGKILL。若站点改为 systemd/宝塔其他托管方式，应改用对应管理器的停止/启动命令。

状态目录内：`last-plan.json` 为最后预演，`update.log` 为阶段日志，
`release-*/manifest.json` 为版本/阶段，`code/` 为旧代码，`databases/` 为 SQLite 备份。
更新器不修改本地开发仓库、不提交或推送代码；服务器只能更新到已可获取的提交。
