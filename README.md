# 病例图片智能分类归档工具

> 上传医疗图片，AI 自动识别图片类型、患者姓名、就诊日期、科室并归档，同时逐项提取医疗单明细，形成可查询的医疗记录台账。

基于 **Ollama + qwen3-vl 视觉语言模型 + FastAPI** 构建，所有 AI 推理在本地完成，数据不出内网，适合医院内网环境使用。

---

## 这个工具解决什么问题？

医院每天产生大量医疗图片（化验单、CT 片、病历等），手动分类归档费时费力且易出错。本工具利用 AI 视觉模型，**一键批量识别并自动归档**：

| 手动操作 | 本工具 |
|----------|--------|
| 逐张查看图片判断类型 | AI 自动识别 10 种医疗图片类型 |
| 手动输入患者姓名、日期、科室 | AI 自动提取关键信息 |
| 手动创建文件夹、移动文件 | 自动按 `姓名/日期/科室/` 归档 |
| 容易分类错误、归档混乱 | 支持姓名候选名单自动校正错别字 |

**处理速度**：单张数秒至数十秒（取决于模型规模与 GPU），支持多图并发处理。

---

## 核心功能

- **批量上传**：多选图片一次性上传，并发处理，识别失败自动重试
- **AI 识别**：自动识别 10 类医疗图片（胸部CT、血常规、门诊病历、口腔影像、检验报告、处方、导引单、检查申请单、缴费票据、无效图像）
- **明细提取**：逐项提取医疗单内容（主诉、诊断、处方用药、检验项目、医嘱、导引单内容等 14 项字段）
- **台账查询**：内置查询页面，按患者姓名、日期、科室、记录类型组合筛选，支持导出
- **自动质检**：11 条规则对识别结果自动体检（明细全空、分类矛盾、模型低自信、姓名靠猜等），可疑记录打标集中待审，无需人工逐条排查
- **姓名校正**：候选名单模糊匹配 + 高分辨率二次提取，自动校正识别中的错别字
- **自动归档**：按 `case_output/姓名/日期/科室/` 层级自动存储
- **模型可换**：模型与推理参数集中在 `model_config.json`，一键切换脚本自动校验视觉能力并冒烟测试
- **数据入库**：分类记录与医疗台账明细双表存入 SQLite，可查历史
- **局域网共享**：部署后局域网内所有设备通过浏览器即可使用
- **隐私安全**：所有图片处理和 AI 推理在本地完成，不上传云端

---

## 技术架构

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   浏览器前端  │ ←→ │  FastAPI 后端         │ ←→ │  Ollama AI 引擎  │
│  (4 个页面)  │     │  主程序 + 台账查询模块  │     │  (视觉模型)      │
└─────────────┘     └────────┬─────────────┘     └─────────────────┘
                             │
                    ┌────────┴─────────┐
                    │  SQLite 双表      │
                    │  分类记录+医疗台账 │
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
├── case_classify_agent.py     # 后端主程序（AI 识别 + 归档 + 质检）
├── apps/
│   └── query_app.py           # 医疗台账查询模块（筛选 / 导出 / 质检报告）
├── static/
│   ├── index.html             # 上传分类页
│   ├── import.html            # 台账导入页
│   ├── medical_records.html   # 台账查询页（含可疑待审筛选）
│   └── medical_record_detail.html  # 记录明细页
├── model_config.json          # 模型与推理参数配置（换模型只改这里）
├── ops/                       # 内部运维脚本（不随仓库分发：模型切换/提交自检/服务器运维）
├── deploy.sh / deploy.ps1     # 部署脚本（Linux/macOS / Windows）
├── uninstall.sh / uninstall.ps1
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

### AI 识别的 10 种类别

| 编号 | 类别 | 示例 |
|------|------|------|
| 1 | 胸部CT影像 | 胸部 CT 扫描片 |
| 2 | 血常规化验单 | 血常规检验报告 |
| 3 | 纸质手写门诊病历 | 门诊病历、病历本 |
| 4 | 口腔影像片 | 口腔全景片/牙片 |
| 5 | 检验报告单 | 生化、免疫、筛查等检验报告 |
| 6 | 无有效医疗图像 | 非医疗相关或无法辨认的图片 |
| 7 | 处方单据 | 处方笺、用药单 |
| 8 | 导引单 | 检查导引单、地点指引单 |
| 9 | 检查申请单 | CT/超声等检查申请单 |
| 10 | 缴费票据 | 门诊缴费凭条、收费票据 |

### 医疗台账查询

识别入库后，浏览器打开 `http://服务器IP:8000/medical-records` 查询台账：

- 按患者姓名、日期范围、科室、记录类型组合筛选，支持导出
- 点击记录可查看明细：主诉、诊断、处方用药、检验项目等逐项内容
- 「可疑待审」筛选：自动质检标记的可疑记录集中展示，优先人工复核
- 质检统计接口：`GET /quality/report` 返回各规则命中情况

---

## 常用运维命令

### Linux

```bash
sudo systemctl status case-classify      # 查看服务状态
sudo systemctl restart case-classify     # 重启服务
sudo journalctl -u case-classify -f      # 查看实时日志
tail -f /var/log/case-classify.log       # 应用日志（识别明细/质检标记）
ollama list                              # 查看已下载的 AI 模型
curl http://127.0.0.1:8000/quality/report   # 质检可疑记录统计
bash ops/switch_model.sh                 # 模型一键切换（内部脚本，见下节说明）
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

模型与推理参数统一在项目根目录 `model_config.json` 配置；环境变量可覆盖，适配 Docker / 远程 Ollama 等场景：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| AI 模型 | `OLLAMA_MODEL` | `qwen3-vl:8b` | Ollama 视觉模型名称 |
| Ollama 地址 | `OLLAMA_HOST_URL` | `http://127.0.0.1:11434` | Ollama API 地址 |
| Web 端口 | `APP_PORT` | `8000` | Web 服务端口 |
| 最大输出 token | `num_predict` | `2048` | 单张识别输出上限（model_config.json） |
| 上下文窗口 | `num_ctx` | `8192` | 模型上下文 token 数（model_config.json） |
| 识别超时 | `classify_timeout` | `300` | 单张识别超时秒数（model_config.json） |
| 并发数 | `max_concurrent` | `2` | 同时处理图片数量（model_config.json） |
| 图片压缩 | `MAX_LONG_SIDE` | `1280` | 预处理最长边像素（代码常量） |
| JPEG 质量 | `JPEG_QUALITY` | `85` | 压缩质量（代码常量） |

修改后重启服务生效。

### 切换模型

换模型不用改代码：编辑 `model_config.json` 的 `model` 字段后重启服务即可，`GET /model/status` 接口可查看当前生效配置：

```bash
ollama show <模型名>    # 先确认 Capabilities 含 vision（支持图片识别）
sudo systemctl restart case-classify    # 修改配置后重启生效（Linux）
```

> 注意：本系统把图片发给模型识别，**必须使用支持视觉的模型**（`ollama show <模型名>`
> 输出的 Capabilities 含 `vision`）。纯文本模型（如 qwen3 系列 27B 文本量化版）无法
> 识别图片，服务将全部识别失败。

> 内部另有 `ops/switch_model.sh` 一键切换脚本（自动校验视觉能力、写配置、重启、冒烟测试），
> 属内部运维工具，不随仓库分发。

---

## 隐私与安全

- **数据不出内网**：所有图片处理和 AI 推理均在本地完成
- **局域网访问**：仅在局域网内开放，不暴露到公网
- **数据存储**：SQLite 数据库 + 本地文件系统，无第三方依赖

---

## License

MIT License