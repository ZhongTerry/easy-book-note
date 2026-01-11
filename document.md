# 🏗️ Smart NoteDB 技术架构深度解析

本项目采用 **“云端大脑 + 本地外壳” (Cloud-Brain & Local-Shell)** 的混合架构模式，旨在平衡“核心数据安全性”与“原生桌面交互体验”。

---

## 一、 整体系统架构图

```text
┌────────────────────────────────┐       ┌────────────────────────────────┐
│      客户端 (Client/Shell)      │       │      云端服务器 (Cloud/API)     │
│   (Electron + Vanilla JS)      │       │      (Python Flask + SQL)      │
├────────────────────────────────┤       ├────────────────────────────────┤
│ 1. 窗口管理 (Tray, BossKey)     │       │ 1. 爬虫引擎 (Impersonate TLS)  │
│ 2. UI 渲染 (PureUI, Tomato)     │ ◀───▶ │ 2. 数据存储 (Unified SQLite)   │
│ 3. 凭证同步 (Cookie Syncing)    │       │ 3. 聚合搜索 (Multi-threaded)   │
│ 4. 离线缓存 (Service Worker)    │       │ 4. 智能同步 (ID Parsing)       │
└────────────────────────────────┘       └────────────────────────────────┘
```

---

## 二、 核心文件目录与职责

### 1. 后端核心逻辑 (Back-end)

*   **`dbserver.py` (应用入口)**
    *   项目的总开关。负责加载环境变量 (`.env`)、初始化 Flask 实例、注册蓝图、启动后台定时任务（如自动追更检查）。
*   **`managers.py` (数据管理层)**
    *   **核心职责**：所有数据库 I/O 的封装。它不直接关心 Web 请求，只关心数据的增删改查。
    *   **关键类**：`IsolatedDB` (KV 存储)、`UpdateManager` (追更状态)、`StatsManager` (阅读洞察统计)。
*   **`spider_core.py` (爬虫引擎核心)**
    *   **NovelCrawler 类**：通用的 HTML 解析逻辑、智能分页缝合技术（Vertical Flow）。
    *   **SearchHelper 类**：聚合搜索助手，支持并发请求不同搜索引擎并解密真实跳转链接。
    *   **核心依赖**：`curl_cffi` 用于模拟浏览器 TLS 指纹，对抗高级 WAF（防火墙）。
*   **`shared.py` (公共权限与工具)**
    *   定义了装饰器（`login_required`, `admin_required`），处理基于 Session 的权限校验和 SSRF 安全过滤。

### 2. 路由分发层 (Routing/Blueprints)

*   **`routes/core_bp.py`**：处理核心业务，如阅读页渲染、书架列表、书单管理、章节序号识别逻辑。
*   **`routes/admin_bp.py`**：管理员后台接口。负责全站活跃度统计、用户角色管理、系统缓存清理。
*   **`routes/pro_bp.py`**：高级会员功能，如全本离线下载任务的异步调度。

### 3. 前端展示层 (Front-end)

*   **`templates/reader_pc.html`**：PC 端专用阅读模板，深度参考“番茄小说”视觉规范，实现了 900px 宽度、羊皮纸背景及侧边栏。
*   **`templates/index.html`**：主控中心（书架页），包含标签过滤、搜索弹窗、GitHub 风格热力图。
*   **`static/purecss/pure2.1.css`**：本项目自研的轻量级 UI 框架，锁死亮色模式，确保移动端与 PC 端组件外观一致。

### 4. 桌面客户端层 (Desktop Shell)

*   **`app_shell/main.js`**：Electron 主进程。负责系统级 API 调用（托盘、全局快捷键、窗口拦截、本地设置持久化）。
*   **`app_shell/preload.js`**：IPC 桥梁。将受限的 Electron 功能（如获取快捷键列表）安全地注入到网页环境中。

---

## 三、 关键技术机制讲解

### 1. “写时存储”智能同步 (Smart Sync)
不同于传统的对比 URL 字符串，本项目在 `/update` 接口中引入了 `calculate_real_chapter_id`。
*   **原理**：当用户保存进度时，后端立即利用正则提取标题中的数字（如“第 49 章” -> `49`）。
*   **优势**：即使不同源站的 URL 规则千奇百怪，系统始终以“真实章节序号”为准，有效避免了多端同步时的覆盖冲突。

### 2. 适配器模式爬虫 (Adapter Pattern)
为了应对结构极其特殊的站点，项目在 `adapters/` 目录下支持插件化扩展。
*   **逻辑**：`spider_core` 在抓取前会扫描所有 Adapter。如果 URL 匹配到某个 Adapter 的正则，则由该 Adapter 接管解析逻辑，支持高度自定义的 HTML 提取和清洗规则。

### 3. PWA & 缓存控制
*   **`sw.js`**：Service Worker 实现了“网络优先”策略。
*   **离线能力**：核心 CSS/JS 资源被持久化在浏览器 Cache 中，确保在网络波动时阅读器架构依然秒开。

---

## 四、 开发工具箱 (Utility Tools)

在项目中，你会经常看到以下辅助脚本：

*   **`migrate.py`**：由于项目从多库架构进化到单库架构，该脚本负责将零散的 JSON 和旧用户数据无缝迁移到统一的 SQL 库中。
*   **`convert_to_ico.py` (及类似脚本)**：基于 Pillow 库，将单一高清 PNG 自动重采样并打包为多尺寸的 Windows `.ico` 容器，确保安装包在不同 DPI 下都清晰可见。

---

## 五、 环境要求

*   **Runtime**: Python 3.10+ / Node.js 18+
*   **Backend Framework**: Flask 3.x
*   **Main Database**: SQLite 3
*   **Crawler Engine**: curl_cffi (mimic Chrome 110+)

---
**本架构文档随版本更新动态调整。如对核心逻辑有疑问，请参考源代码中的 Docstring 注释。**