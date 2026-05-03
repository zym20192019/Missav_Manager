# Missav Manager — JableTV & MissAV 视频下载器

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-005571?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/M3U8%2FHLS-Streaming-green" alt="M3U8">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

> 基于 M3U8/HLS 流媒体协议的 JableTV & MissAV 视频下载器，液态玻璃 UI 设计，WebSocket 实时进度推送，支持转存路径管理与自动转存。

---

## ✨ 功能特性

### 下载能力
- **M3U8/HLS 流下载**：多线程分片下载，支持 AES-128 加密串流自动解密
- **自动合并**：TS 片段自动合并为完整 MP4，无需 FFmpeg
- **断点续传**：已完成的片段不会重复下载
- **多站点支持**：JableTV、MissAV 自动识别（MissAV 支持 JS 解混淆）

### 实时体验
- **WebSocket 进度推送**：百分比、下载速度、剩余时间、片段进度实时更新
- **液态玻璃 UI**：磨砂玻璃卡片 + 浮动渐变光晕 + 丝滑动画

### 文件管理
- **云端转存**：一键移动到自定义路径（FUSE 安全模式：先复制 → 校验大小 → 删除源文件）
- **自动转存**：开启后下载完成自动移动到指定路径
- **批量操作**：多选批量转存、批量删除
- **批量导入**：textarea 多行粘贴 URL，一键批量下载

### 安全与部署
- **Token 鉴权**：Bearer Token 认证
- **systemd 服务化**：开机自启，崩溃自动重启

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────┐
│  Frontend — static/index.html (Liquid Glass UI) │
│  Vanilla JS + CSS / WebSocket / 响应式           │
└────────────────────┬────────────────────────────┘
                     │ HTTP + WebSocket
┌────────────────────▼────────────────────────────┐
│  Backend — FastAPI (app/main.py)                 │
│  REST API / Auth / Path Management / Queue       │
├─────────────────────────────────────────────────┤
│  Downloader — app/downloader.py                  │
│  M3U8 Parser / AES Decryption / Thread Pool      │
│  Site Parsers: JableTV / MissAV                  │
├─────────────────────────────────────────────────┤
│  Task Manager — app/tasks.py                     │
│  Thread-safe / JSON Persistence / Pub-Sub        │
└─────────────────────────────────────────────────┘
```

### 核心技术
- **M3U8 解析**：`m3u8` 库解析 master/media playlist，自动选择最高画质
- **AES-128 解密**：`pycryptodome` 实现，每个分片独立 cipher（线程安全）
- **多线程下载**：`ThreadPoolExecutor`（8 workers），支持失败重试（最多 5 轮）
- **站点解析**：`cloudscraper` 绕过 Cloudflare，正则 + JS 解混淆提取 M3U8 URL
- **连接池**：`requests.Session` 全局复用（32 连接 / 64 最大并发）

---

## 🚀 快速开始

### 环境要求
- Python 3.11+

### 安装

```bash
git clone https://github.com/zym20192019/Missav_Manager.git
cd Missav_Manager

# 安装依赖
pip3 install -r requirements.txt

# 启动
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8025
```

### systemd 服务（推荐）

```bash
sudo cp jable-downloader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable jable-downloader
sudo systemctl start jable-downloader
```

### 访问
打开浏览器访问 `http://<your-ip>:8025`，默认账号 `admin` / `jable2026`。

---

## 📡 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/login` | 登录获取 Token |
| `POST` | `/api/logout` | 登出销毁 Token |
| `POST` | `/api/download` | 创建单个下载任务 |
| `POST` | `/api/downloads/batch` | 批量创建下载任务 |
| `GET` | `/api/tasks` | 获取任务列表（支持搜索/状态过滤） |
| `DELETE` | `/api/tasks/{id}` | 删除任务及文件 |
| `POST` | `/api/tasks/{id}/retry` | 重试失败任务 |
| `POST` | `/api/move` | 移动文件到指定路径 |
| `POST` | `/api/tasks/batch-move` | 批量转存 |
| `POST` | `/api/tasks/batch-delete` | 批量删除 |
| `GET/POST/DELETE` | `/api/paths` | 自定义转存路径 CRUD |
| `POST` | `/api/paths/{id}/auto-move` | 切换自动转存 |
| `GET` | `/api/paths/auto-move` | 获取自动转存配置 |
| `WS` | `/ws/{task_id}` | WebSocket 进度订阅 |

---

## 📁 项目结构

```
Missav_Manager/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 路由 + 鉴权 + 队列
│   ├── models.py        # Pydantic 数据模型
│   ├── tasks.py         # 任务管理器（线程安全 + JSON 持久化）
│   └── downloader.py    # M3U8 引擎 + 站点解析器
├── static/
│   └── index.html       # 液态玻璃 UI 前端
├── downloads/           # 下载文件存储目录
├── path_config.json     # 转存路径配置
├── task_history.json    # 任务历史记录
├── requirements.txt     # Python 依赖
└── .gitignore
```

---

## 🔑 支持的网站

| 网站 | 解析方式 | 状态 |
|------|----------|------|
| [Jable.tv](https://jable.tv) | 正则提取 M3U8 URL | ✅ |
| [MissAV](https://missav.ai) | JS 解混淆（Dean Edwards Packer） | ✅ |
| 其他 M3U8 网站 | 直接使用 M3U8 URL | ✅ |

---

## 🛡️ 免责声明

本工具仅供学习与技术研究用途。使用者应遵守当地法律法规，尊重内容版权。

---

## 📄 License

MIT
