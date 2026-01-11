# ⚡ Smart NoteDB

**Smart NoteDB** 是一款专为极致阅读体验打造的沉浸式、私人阅读中枢。它打破了传统浏览器书签的局限，通过“云端大脑+本地外壳”的架构，集成了高效采集、全网聚合搜索、跨端智能同步、深度数据分析和原生桌面交互，为老书虫提供一个无广告、不被打断的阅读圣地。

[![Version](https://img.shields.io/badge/version-v1.1.2--patch-6366f1)](https://github.com/yourname/notedb)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Web%20%7C%20Windows%20%7C%20PWA-brightgreen)](#)

## ✨ 核心特性

- 🚫 **绝对纯净**：全网自动化采集，动态清洗广告干扰与无效元素，还原最纯净的文字排版。
- 🔍 **聚合搜索**：内置高效搜索助手，支持多线程并发检索多个主流源站，彻底规避搜索引擎 IP 风控。
- ☁️ **智能同步**：独家“写时存储”算法，基于章节真实序号（而非易变的 URL）进行逻辑比对，多端断点续读不再误报。
- 📊 **阅读洞察**：可视化数据看板。内置 GitHub 风格的年度活跃热力图、阅读时长趋势及字数统计，量化你的每一份专注。
- 🖥️ **原生桌面体验**：基于 Electron 的专属客户端。支持系统托盘、自定义全局老板键、朗读控制快捷键，深度集成系统原生功能。
- 🍅 **番茄风格重构**：PC 端采用 900px 黄金阅读宽度，配合精心调校的羊皮纸背景与字间距，打造极致护眼的沉浸环境。
- 🛠️ **插件化适配**：采用适配器设计模式，通过简单的 Python 脚本扩展即可完美兼容任何结构奇葩的小说站点。

## 🛠️ 技术栈

- **后端**：Python 3.12 + Flask (RESTful API 设计)
- **数据库**：SQLite 3 (采用单一 `data.sqlite` 架构，高效管理用户模块)
- **前端**：Vanilla JS + **PureUI** (自定义轻量级 UI 框架) + Chart.js
- **外壳**：Electron (原生桌面化与跨域凭证同步)
- **爬虫**：`curl_cffi` (指纹级模拟) + BeautifulSoup4 + lxml

## 🚀 快速开始

### 1. 服务端部署 (Server)
服务端负责数据存储、爬虫解析与聚合搜索。
```bash
# 克隆仓库
git clone https://github.com/yourname/noteDB.git
cd noteDB

# 安装 Python 依赖
pip install -r requirements.txt

# 启动服务
python dbserver.py
```

### 2. 桌面客户端启动 (Electron)
客户端提供原生窗口体验，并实现与云端凭证的自动同步。
```bash
cd app_shell
npm install
npm start
```

## 📂 项目结构
```text
Project_Root/
├── src/                # 后端逻辑层
│   ├── routes/         # Flask 蓝图 (Core 业务, Admin 管理, Pro 特权)
│   ├── managers.py     # 数据库事务与用户模块管理
│   └── spider_core.py  # 爬虫引擎与聚合搜索助手
├── templates/          # 响应式模板 (PC 番茄风 / 移动端卡片化分流)
├── static/             # 静态资源 (PWA 配置, 热力图组件, ChangeLog)
├── adapters/           # 站点专用适配器插件
└── app_shell/          # Electron 壳程序代码
```

## 🛡️ 安全与架构
本项目采用 **“云端大脑 + 本地壳子”** 的架构设计。所有的敏感密钥 (`CLIENT_SECRET`)、核心爬虫逻辑以及 `SQLite` 数据库均保留在服务器端。本地客户端仅作为 UI 渲染层与交互入口，通过安全的凭证同步机制进行访问，极大程度地保证了个人阅读数据的安全性与私密性。

## 📄 开源协议
本项目采用 [MIT License](LICENSE) 开源。

---
**由书虫开发，为书虫而生。**