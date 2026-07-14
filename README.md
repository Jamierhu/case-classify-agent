# 病例图片智能分类归档工具

> 上传医疗图片，AI 自动识别图片类型、患者姓名、就诊日期、科室，并按规范归档存储。

基于 **Ollama + qwen3-vl 视觉语言模型 + FastAPI** 构建，所有 AI 推理在本地完成，数据不出内网，适合医院内网环境使用。

---

## 这个工具解决什么问题？

医院每天产生大量医疗图片（化验单、CT 片、病历等），手动分类归档费时费力且易出错。本工具利用 AI 视觉模型，**一键批量识别并自动归档**：

| 手动操作 | 本工具 |
|----------|--------|
| 逐张查看图片判断类型 | AI 自动识别 6 种医疗图片类型 |
| 手动输入患者姓名、日期、科室 | AI 自动提取关键信息 |
| 手动创建文件夹、移动文件 | 自动按 `姓名/日期/科室/` 归档 |
| 容易分类错误、归档混乱 | 支持姓名候选名单自动校正错别字 |

**处理速度**：GPU 模式下单张图片 1-3 秒，支持多图并发处理。

---

## 核心功能

- **批量上传**：多选图片一次性上传，并发处理
- **AI 识别**：自动识别 6 类医疗图片（胸部CT、血常规、门诊病历、口腔影像、检验报告、无效图像）
- **信息提取**：自动提取患者姓名、就诊日期、科室
- **姓名校正**：支持输入候选姓名名单，AI 自动校正识别中的错别字
- **自动归档**：按 `case_output/姓名/日期/科室/` 层级自动存储
- **数据入库**：所有分类记录存入 SQLite 数据库，可查历史
- **局域网共享**：部署后局域网内所有设备通过浏览器即可使用
- **隐私安全**：所有图片处理和 AI 推理在本地完成，不上传云端

---

## 技术架构

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   浏览器前端  │ ←→ │  FastAPI 后端     │ ←→ │  Ollama AI 引擎  │
│  (HTML/JS)  │     │  (Python)        │     │  (qwen3-vl:8b)  │
└─────────────┘     └────────┬─────────┘     └─────────────────┘
                             │
                    ┌────────┴─────────┐
                    │  SQLite 数据库    │
                    │  + 文件系统归档    │
                    └──────────────────┘
```

**技术栈**：

| 层级 | 技术 | 说明 |
|------|------|------|
| AI 模型 | Ollama + qwen3-vl:8b | 视觉语言模型，识别图片内容并提取结构化信息 |
| 后端 | Python + FastAPI | 异步 Web 框架，支持高并发图片处理 |
| 前端 | HTML + JavaScript | 原生实现，无需 Node.js 构建，零依赖 |
| 数据库 | SQLite | 轻量级嵌入式数据库，无需额外安装 |
| 部署 | Bash / PowerShell / Docker | 全平台一键部署脚本 |

---

## 项目结构

```
case_classify_agent/
├── case_classify_agent.py     # 后端主程序（FastAPI + AI 调用逻辑）
├── static/
│   └── index.html             # 前端页面（上传、展示结果）
├── deploy.sh                  # 部署脚本 - Linux / macOS
├── deploy.ps1                 # 部署脚本 - Windows
├── uninstall.sh               # 卸载脚本 - Linux / macOS
├── uninstall.ps1              # 卸载脚本 - Windows
├── Dockerfile                 # Docker 镜像构建文件
├── docker-compose.yml         # Docker Compose 编排
├── docker-entrypoint.sh       # Docker 容器启动脚本
├── requirements.txt           # Python 依赖清单
├── case_output/               # 归档输出目录（运行后自动创建）
└── case_record.db             # SQLite 数据库（运行后自动创建）
```

---

## 快速开始

### 环境要求

- **操作系统**：Linux / macOS / Windows 均可
- **Python**：3.8+（部署脚本会自动检测）
- **内存**：建议 16GB+（AI 模型推理需要）
- **GPU**：可选，有 NVIDIA GPU 会大幅加速（macOS 自动使用 Metal 加速）
- **网络**：首次部署需联网下载 AI 模型（约 5GB），之后离线可用

### 三种部署方式（任选其一）

#### 方式一：Linux / macOS（推荐服务器部署）

```bash
# 1. 进入项目目录
cd case_classify_agent

# 2. 赋予执行权限
chmod +x deploy.sh

# 3. 一键部署（自动安装 Ollama、下载模型、配置开机自启）
./deploy.sh
```

部署脚本自动完成：安装 Ollama → 下载 AI 模型 → 创建 Python 环境 → 注册系统服务 → 配置防火墙。

#### 方式二：Windows

```powershell
# 以管理员身份打开 PowerShell，进入项目目录
cd case_classify_agent

# 一键部署
.\deploy.ps1
```

#### 方式三：Docker（推荐容器化场景）

```bash
# 一行命令启动
docker compose up -d --build
```

Docker 模式无需在宿主机安装任何依赖，环境完全自包含。

### 部署后访问

部署完成后，用浏览器打开：

| 访问方式 | 地址 |
|----------|------|
| 本机 | `http://127.0.0.1:8000` |
| 局域网其他设备 | `http://服务器IP:8000` |

> **云服务器用户注意**：除了系统防火墙，还需在云控制台（阿里云/腾讯云/AWS 等）的安全组中放行端口 `8000`。

---

## 使用方法

1. 浏览器打开部署地址
2. （可选）在"候选姓名名单"输入框填写可能的患者姓名（逗号分隔），帮助 AI 校正错别字
3. 点击"选择文件"，选择需要分类的图片（可多选）
4. 点击"开始批量分类"，等待处理
5. 查看分类结果和归档路径

### AI 识别的 6 种类别

| 编号 | 类别 | 示例 |
|------|------|------|
| 1 | 胸部CT影像 | 胸部 CT 扫描片 |
| 2 | 血常规化验单 | 血常规检验报告 |
| 3 | 门诊病历 | 纸质手写门诊病历 |
| 4 | 口腔影像片 | 口腔全景片/牙片 |
| 5 | 检验报告单 | 生化、免疫等检验报告 |
| 6 | 无效图像 | 非医疗相关图片 |

---

## 常用运维命令

### Linux

```bash
sudo systemctl status case-classify      # 查看服务状态
sudo systemctl restart case-classify     # 重启服务
sudo journalctl -u case-classify -f      # 查看实时日志
ollama list                              # 查看已下载的 AI 模型
```

### macOS

```bash
launchctl list | grep case-classify      # 查看服务状态
tail -f /tmp/case-classify.log           # 查看日志
ollama list                              # 查看已下载的 AI 模型
```

### Windows

```powershell
Get-ScheduledTask -TaskName CaseClassifyAgent   # 查看任务状态
Start-ScheduledTask -TaskName CaseClassifyAgent # 启动任务
Stop-ScheduledTask -TaskName CaseClassifyAgent  # 停止任务
Get-Content .\case-classify.log -Tail 50 -Wait  # 查看实时日志
ollama list                                     # 查看已下载的 AI 模型
```

### Docker

```bash
docker compose logs -f        # 查看实时日志
docker compose ps             # 查看容器状态
docker compose restart        # 重启服务
docker compose down           # 停止并移除容器
```

---

## 卸载

| 系统 | 命令 |
|------|------|
| Linux / macOS | `./uninstall.sh` |
| Windows | `.\uninstall.ps1` |

卸载会停止并移除服务/计划任务、清理防火墙规则。**代码、数据库和已归档的图片会保留**，不会删除。

---

## 配置说明

通过环境变量调整运行参数（也可在 `case_classify_agent.py` 顶部直接修改）：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| AI 模型 | `OLLAMA_MODEL` | `qwen3-vl:8b` | Ollama 视觉模型名称 |
| Ollama 地址 | `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama API 地址 |
| Web 端口 | `APP_PORT` | `8000` | Web 服务端口 |
| 并发数 | `MAX_CONCURRENT` | `4` | 同时处理图片数量 |
| 图片压缩 | `MAX_LONG_SIDE` | `1280` | 预处理最长边像素 |
| JPEG 质量 | `JPEG_QUALITY` | `85` | 压缩质量 |

修改后重启服务生效。

---

## 隐私与安全

- **数据不出内网**：所有图片处理和 AI 推理均在本地完成
- **局域网访问**：仅在局域网内开放，不暴露到公网
- **数据存储**：SQLite 数据库 + 本地文件系统，无第三方依赖

---

## License

MIT License