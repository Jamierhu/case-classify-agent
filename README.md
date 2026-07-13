# 病例图片分类小工具

基于 [Ollama](https://ollama.com/) + [qwen3-vl:8b](https://ollama.com/library/qwen3-vl) 视觉语言模型 + FastAPI 的局域网病例图片自动分类归档工具。

上传医疗图片（化验单、CT 片、病历等），AI 自动识别**图片类型**、**患者姓名**、**就诊日期**、**科室**，并按 `姓名/日期/科室/` 层级归档存储。

## ✨ 功能特性

- 🖼️ **批量分类**：支持多选图片一次性上传，并发处理
- 🏷️ **6 类分类**：胸部CT、血常规、门诊病历、口腔影像、检验报告、无效图像
- 📝 **信息提取**：自动识别患者姓名、就诊日期、科室
- 🔧 **姓名校正**：支持输入候选姓名名单，自动校正识别结果中的错别字
- 📁 **自动归档**：按 `case_output/姓名/日期/科室/` 层级存储
- 🗄️ **记录入库**：SQLite 存储所有分类记录，支持历史查询
- 🌐 **局域网共享**：部署后局域网内所有设备可通过浏览器访问
- ⚡ **GPU 加速**：支持 NVIDIA GPU，单张图片识别约 1-3 秒

## 📂 项目结构

```
case_classify_agent/
├── case_classify_agent.py   # 后端主程序（FastAPI + Ollama 调用）
├── static/
│   └── index.html           # 前端页面
├── deploy.sh                # 一键部署脚本
├── uninstall.sh             # 卸载脚本
├── requirements.txt         # Python 依赖（部署时自动生成）
├── case_images_temp/        # 临时图片（自动创建）
├── case_output/             # 归档输出目录（自动创建）
└── case_record.db           # SQLite 数据库（自动创建）
```

## 🚀 一键部署

### 环境要求

- **操作系统**：Ubuntu / Debian / CentOS / Arch 等 Linux 发行版
- **Python**：3.8+
- **内存**：建议 16GB+（模型推理需要）
- **GPU**：可选，有 NVIDIA GPU 速度更快（推荐 RTX 3060 12GB+）
- **网络**：需要能访问 `ollama.com` 拉取模型

### 部署步骤

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/case-classify.git
cd case_classify_agent

# 2. 赋予执行权限
chmod +x deploy.sh

# 3. 一键部署（脚本会自动安装 Ollama、拉取模型、配置服务）
./deploy.sh
```

部署脚本会自动完成：
1. ✅ 安装系统依赖（Python、curl、防火墙工具）
2. ✅ 安装 Ollama 并拉取 `qwen3-vl:8b` 视觉模型
3. ✅ 配置 Ollama 性能优化（并发推理、模型常驻显存、Flash Attention）
4. ✅ 创建 Python 虚拟环境并安装依赖
5. ✅ 注册 systemd 服务（开机自启 + 崩溃自动重启）
6. ✅ 配置防火墙放行端口

### 访问使用

部署完成后，根据脚本输出的地址访问：

- **本机访问**：`http://127.0.0.1:8000`
- **局域网访问**：`http://你的服务器IP:8000`（局域网内任何设备的浏览器均可访问）

## 📖 使用说明

1. 打开浏览器访问部署地址
2. （可选）在"候选姓名名单"输入框填写可能的患者姓名（逗号分隔），用于校正识别结果
3. 点击"选择文件（多选）"，选择需要分类的图片
4. 点击"开始批量分类"，等待处理完成
5. 查看分类结果和归档路径

### 分类类别

| 编号 | 类别 |
|------|------|
| 1 | 胸部CT影像 |
| 2 | 血常规化验单 |
| 3 | 纸质手写门诊病历 |
| 4 | 口腔影像片 |
| 5 | 检验报告单 |
| 6 | 无有效医疗图像 |

## 🛠️ 常用命令

```bash
# 查看服务状态
sudo systemctl status case-classify

# 查看实时日志
sudo journalctl -u case-classify -f

# 查看应用日志
tail -f /var/log/case-classify.log

# 重启服务
sudo systemctl restart case-classify

# 停止服务
sudo systemctl stop case-classify

# 查看 Ollama 模型
ollama list

# 重启 Ollama
sudo systemctl restart ollama
```

## 🗑️ 卸载

```bash
./uninstall.sh
```

卸载脚本会停止并移除 systemd 服务、清理防火墙规则，可选卸载 Ollama。代码、数据库和已归档的图片会被保留。

## ⚙️ 配置说明

主要配置项在 `case_classify_agent.py` 顶部的配置区：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MODEL_NAME` | `qwen3-vl:8b` | Ollama 视觉模型名称 |
| `MAX_CONCURRENT` | `4` | 并发调用 Ollama 的数量 |
| `MAX_LONG_SIDE` | `1280` | 图片预处理最长边像素 |
| `JPEG_QUALITY` | `85` | 图片压缩质量 |
| `MAX_LONG_SIDE_HI` | `1536` | 姓名二次提取的更高分辨率 |

修改配置后需重启服务：`sudo systemctl restart case-classify`

## 🔒 隐私说明

- 所有图片处理和 AI 推理均在**本地完成**，不会上传到任何云端
- 数据存储在本地 SQLite 数据库和文件系统中
- 仅在局域网内开放访问，不暴露到公网

## 📄 License

MIT License
