import base64
import cv2
import sqlite3
import os
import re
import json
import asyncio
import requests
import numpy as np
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware

from apps.query_app import ensure_medical_record_tables, register_query_app

# ===================== 配置区 =====================
# 配置读取优先级：环境变量 > model_config.json > 内置默认值
# 换模型不用改代码：bash ops/switch_model.sh <模型名>，或编辑 model_config.json 后重启服务
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_config.json")

def _load_model_config() -> dict:
    defaults = {
        "ollama_base_url": "http://127.0.0.1:11434",
        "model": "qwen3-vl:8b",   # 必须是支持图片的视觉语言模型，纯文本模型无法识别病历图片
        "num_predict": 2048,
        "num_ctx": 8192,
        "classify_timeout": 300,
        "name_timeout": 180,
        "max_concurrent": 2,
    }
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            for k, v in loaded.items():
                if k in defaults and v not in (None, ""):
                    defaults[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[配置] model_config.json 解析失败，使用默认配置: {e}")
    return defaults

_CFG = _load_model_config()
# 环境变量覆盖，适配 Docker / 云端 / 远程 Ollama 等场景
OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL", _CFG["ollama_base_url"])
MODEL_NAME = os.environ.get("OLLAMA_MODEL", _CFG["model"])
# 推理参数（在 model_config.json 中调整，随所选模型特性配置）
CLASSIFY_NUM_PREDICT = int(_CFG["num_predict"])
CLASSIFY_NUM_CTX = int(_CFG["num_ctx"])
CLASSIFY_TIMEOUT = int(_CFG["classify_timeout"])
NAME_TIMEOUT = int(_CFG["name_timeout"])
CLASS_LIST = [
    "1-胸部CT影像",
    "2-血常规化验单",
    "3-纸质手写门诊病历",
    "4-口腔影像片",
    "5-检验报告单",
    "6-无有效医疗图像",
    "7-处方单据",
    "8-导引单",
    "9-检查申请单",
    "10-缴费票据",
]
# 基于脚本所在目录的绝对路径，避免工作目录变化导致文件散落各处
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_IMG_DIR = os.path.join(_BASE_DIR, "case_images_temp")
OUTPUT_ROOT = os.path.join(_BASE_DIR, "case_output")
DB_PATH = os.path.join(_BASE_DIR, "case_record.db")
# 并发调用Ollama的最大数量（需与 OLLAMA_NUM_PARALLEL 匹配，建议2-4）
MAX_CONCURRENT = int(_CFG["max_concurrent"])
# 单张图片最长边（姓名等小字需要更高分辨率，1280px兼顾识别率与速度）
MAX_LONG_SIDE = 1280
JPEG_QUALITY = 85
# 姓名二次提取使用的更高分辨率
MAX_LONG_SIDE_HI = 1536
os.makedirs(SAVE_IMG_DIR, exist_ok=True)
os.makedirs(OUTPUT_ROOT, exist_ok=True)

# 全局并发信号量（控制对Ollama的并发请求数）
_ollama_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
# =====================================================================

app = FastAPI(title="局域网病例图片分类Agent")
register_query_app(app)

# 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 托管静态前端页面
app.mount("/static", StaticFiles(directory="static"), name="static")

# 访问根地址直接打开前端
@app.get("/")
async def index_page():
    return FileResponse("static/index.html")


@app.get("/medical-records")
async def medical_records_page():
    return FileResponse("static/medical_records.html")


@app.get("/medical-record-detail")
async def medical_record_detail_page():
    return FileResponse("static/medical_record_detail.html")


@app.get("/import")
async def import_page():
    return FileResponse("static/import.html")

# 当前模型运行配置（运维脚本 ops/switch_model.sh 靠它校验服务是否已加载新配置）
@app.get("/model/status")
async def model_status():
    return {
        "model": MODEL_NAME,
        "ollama_base_url": OLLAMA_HOST,
        "num_predict": CLASSIFY_NUM_PREDICT,
        "num_ctx": CLASSIFY_NUM_CTX,
        "classify_timeout": CLASSIFY_TIMEOUT,
        "name_timeout": NAME_TIMEOUT,
        "max_concurrent": MAX_CONCURRENT,
    }

# 应用启动时自动初始化数据库（uvicorn命令行启动不会执行__main__块，
# 必须用startup事件保证表一定被创建）
@app.on_event("startup")
def _startup_init_db():
    init_db()
    ensure_medical_record_tables()

# 初始化数据库
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS case_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_path TEXT NOT NULL,
            dst_path TEXT NOT NULL,
            name TEXT,
            dept TEXT,
            date TEXT,
            classify_result TEXT NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medical_record_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            case_name TEXT,
            record_date TEXT,
            dept TEXT,
            document_type TEXT,
            chief_complaint TEXT,
            diagnosis TEXT,
            treatment_opinion TEXT,
            prescription_text TEXT,
            examination_text TEXT,
            instructions_text TEXT,
            order_text TEXT,
            guide_text TEXT,
            medication_text TEXT,
            dosage_text TEXT,
            raw_content TEXT,
            source_path TEXT,
            remark TEXT,
            quality_flag TEXT DEFAULT '',
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(medical_record_entries)")]
    for col in ["dept", "chief_complaint", "diagnosis", "treatment_opinion",
                "prescription_text", "examination_text", "instructions_text",
                "quality_flag"]:
        if col not in columns:
            cursor.execute(f"ALTER TABLE medical_record_entries ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()

# 图片预处理：仅提亮+缩放（去掉模糊，模糊会损害OCR识别率）
def preprocess_image(img: Image.Image) -> Image.Image:
    img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    alpha = 1.2
    beta = 10
    img_cv = cv2.convertScaleAbs(img_cv, alpha=alpha, beta=beta)
    img_pil = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
    # 缩小到适合OCR的尺寸，过大反而拖慢推理
    img_pil.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE))
    return img_pil

# PIL图片转base64
def image_to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

# 从模型输出中稳健提取JSON对象
# 依次尝试：整体解析 -> 去markdown代码围栏 -> 平衡花括号扫描(支持嵌套与字符串内花括号) -> 正则兜底
# qwen3-vl 在 think:false 时常把JSON输出到thinking字段，且长单据明细偶发被截断，必须多种方式兜底
def _extract_json(text) -> dict:
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    # 1) 整体解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    # 2) 去掉markdown代码围栏后整体解析
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    if stripped != text:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    # 3) 平衡花括号扫描：从每个'{'起找配对的'}'，跳过字符串字面量内的花括号
    for start in range(len(text)):
        if text[start] != '{':
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
    # 4) 正则兜底（仅匹配扁平对象）
    m = re.search(r'\{[^{}]*\}', text)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return None

# 清洗粗分类：把模型输出归一到CLASS_LIST中的合法值，无法识别时归为"6-无有效医疗图像"
def _clean_classify(value) -> str:
    invalid = "6-无有效医疗图像"
    if not value or not isinstance(value, str):
        return invalid
    v = value.strip()
    if v in CLASS_LIST:
        return v
    # "7-处方单据"带空格等变体：按数字前缀归一
    m = re.match(r'^(\d+)\s*-\s*(.*)$', v)
    if m:
        num, label = m.group(1), m.group(2).strip()
        for item in CLASS_LIST:
            if item.split('-', 1)[0] == num:
                return item
        for item in CLASS_LIST:
            if item.split('-', 1)[1] == label:
                return item
    # 纯数字
    if v.isdigit():
        for item in CLASS_LIST:
            if item.split('-', 1)[0] == v:
                return item
    # 纯文字标签精确/包含匹配（如模型只输出"处方"）
    for item in CLASS_LIST:
        if v == item.split('-', 1)[1]:
            return item
    if len(v) >= 2:
        for item in CLASS_LIST:
            if v in item.split('-', 1)[1]:
                return item
    return invalid

# 清洗日期：提取YYYY-MM-DD格式，兼容"2026/05/08""2026年5月8日"等写法，无效则返回"未知日期"
def _clean_date(value) -> str:
    if not value:
        return "未知日期"
    m = re.search(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})', str(value))
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    return "未知日期"

# 同步核心：调用Ollama（在后台线程中执行，不阻塞事件循环）
def _ollama_extract_info_sync(base64_img: str, candidate_names: list = None) -> dict:
    # 根据是否有候选名单，采用不同策略：
    # - 有名单：把"OCR识别"转化为"名单选择"任务（封闭集合选择，错别字率极低）
    # - 无名单：纯OCR识别，加强字形辨认指引
    if candidate_names:
        name_section = f"""
【姓名识别任务（从名单中选择）】
图片上有一个患者姓名。候选名单如下：
{json.dumps(candidate_names, ensure_ascii=False)}

请按以下步骤判断：
1. 先在图片上定位姓名位置（通常在"姓名:"、"患者:"字段后，或表格首行、印章附近）
2. 仔细辨认图片上姓名的每个字
3. 将辨认结果与候选名单逐一比对，选出与图片上姓名最匹配的一个
   - 完全一致：直接选该名字
   - 仅个别字差异（字形相近如"日/曰"、"己/已"、印刷模糊、手写潦草）：选名单中的那个
   - 只有当图片上姓名与名单中所有名字都明显不同（差异字数≥2）时，才输出图片上实际看到的姓名
4. name字段只输出最终选定的纯姓名（2-4个汉字），不含"姓名:"等前缀"""
    else:
        name_section = """
【姓名识别任务（OCR辨认）】
请仔细辨认图片上的患者姓名：
1. 定位姓名位置（"姓名:"、"患者:"字段后，或表格首行、印章附近）
2. 逐字辨认，注意区分形近字：日/曰、己/已/巳、戊/戌/戍、未/末、土/士、人/入、千/干
3. 中文姓名通常为2-4个汉字
4. name字段只输出纯姓名，不含"姓名:"等前缀
5. 必须输出图片上实际看到的姓名，不要输出空字符串"""

    prompt = f"""分析这张医疗图片，完成以下任务：

任务一【分类】：classify从以下选项选最匹配的一项：[{', '.join(CLASS_LIST)}]
注意："6-无有效医疗图像"仅用于与医疗无关的图片或完全无法辨认的图片；处方/药品单据必须选"7-处方单据"；医疗缴费凭条/收据选"10-缴费票据"
任务二【信息提取】：提取患者姓名name、就诊日期date(格式YYYY-MM-DD)、科室dept
{name_section}

【日期提取】格式YYYY-MM-DD，找不到则输出"未知日期"
【科室提取】输出图片上明确标注的科室，找不到则输出"普通门诊"

任务三【医疗单详细内容提取】：逐项如实提取图片上的文字信息，没有对应内容的字段输出空字符串""
- document_type：记录类型，从["医嘱","导引单","处方","检查申请单","检验报告","用药","病例","缴费票据"]中选最匹配的一项（缴费凭条/收费票据/收据选"缴费票据"；化验单/检验报告/检查报告/筛查报告单选"检验报告"）
- chief_complaint：主诉/病情描述（患者主要不适、症状、病史，如"反复咳嗽3天，伴发热"）
- diagnosis：诊断结论（如"急性支气管炎"）
- treatment_opinion：处理意见（医生的治疗方案、处置建议，如"抗感染、对症治疗"）
- prescription_text：处方/用药详情，逐项记录"药品名称+规格+剂量+用法"（如"阿莫西林胶囊 0.5g，每次1粒，每日3次，口服"），多个药品用分号"；"分隔
- examination_text：检查申请内容/描述（如"胸部CT平扫""血常规"）
- instructions_text：嘱托/注意事项（如"注意休息""清淡饮食""定期复查"）
- order_text：医嘱内容（如"门诊复查""按医嘱服药"）
- guide_text：导引单内容
- medication_text：主要药品名称（如"阿莫西林胶囊"）
- dosage_text：用药用量（如"0.5g，每日2次"）
- raw_content：图片上医疗单主要内容的简要文字记录（200字以内）
- confidence：0-10整数，你对本次识别结果的整体置信度（图片模糊/反光/关键字段无法确认时给低分）
- name_source：姓名来源，"seen"=图片上清晰可见患者姓名，"guessed"=图上看不清或没有姓名，是从候选名单/上下文猜的

只输出JSON，格式：{{"classify":"...","name":"...","date":"...","dept":"...","document_type":"...","chief_complaint":"...","diagnosis":"...","treatment_opinion":"...","prescription_text":"...","examination_text":"...","instructions_text":"...","order_text":"...","guide_text":"...","medication_text":"...","dosage_text":"...","raw_content":"...","confidence":0,"name_source":"seen"}}"""
    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [base64_img]
        }],
        "stream": False,
        "format": "json",       # 强制JSON输出
        "think": False,         # 关闭思考模式，大幅减少token消耗（结果在thinking字段，下方兜底提取）
        "options": {
            "temperature": 0.1,      # 降低随机性，OCR类任务需要确定性输出
            "num_predict": CLASSIFY_NUM_PREDICT,   # 长单据明细JSON可达900+token，过小会截断导致解析失败
            "num_ctx": CLASSIFY_NUM_CTX             # prompt约1900token+明细输出，留足余量
        }
    }
    # 最多尝试2次：qwen3-vl 在 think:false 时JSON可能输出到thinking字段，
    # 且长单据明细偶发被num_predict截断导致解析失败；失败时降温度重试一次
    result = None
    got_ok_response = False
    for attempt in range(2):
        if attempt == 1:
            payload["options"]["temperature"] = 0.0
        try:
            resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=CLASSIFY_TIMEOUT)
        except requests.RequestException as e:
            print(f"[识别异常] 第{attempt+1}次请求失败: {e}")
            continue
        if resp.status_code != 200:
            print(f"[识别异常] 第{attempt+1}次 HTTP {resp.status_code}: {resp.text[:200]}")
            continue
        got_ok_response = True
        msg = resp.json().get("message", {})
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        # 先从content提取JSON，提取不到再从thinking提取（qwen3-vl常把JSON写进thinking）
        result = _extract_json(content) or _extract_json(thinking)
        if result:
            break
        print(f"[识别失败] 第{attempt+1}次未解析出JSON content_len={len(content)} thinking_len={len(thinking)}")
        print(f"[识别失败] content片段: {content[:200]!r}")
        print(f"[识别失败] thinking片段: {thinking[:200]!r}")
    # 两次都失败：宁可让该图片报失败，也不落一条全空脏数据进台账
    if result is None:
        if got_ok_response:
            raise Exception("AI识别结果JSON解析失败（已重试）")
        raise Exception("Ollama接口异常: 两次尝试均无有效响应")

    if result:
        raw_name = result.get("name")
        # 如果提供了候选名单，对识别结果做模糊匹配校正
        if candidate_names:
            matched_name = _match_candidate_name(raw_name, candidate_names)
        else:
            matched_name = _clean_name(raw_name)
        # 清洗：如果值是模板文字或空字符串，替换为默认值
        info = {
            "classify": _clean_classify(result.get("classify")),
            "name": matched_name,
            "date": _clean_date(result.get("date")),
            "dept": result.get("dept") if result.get("dept") and "推断" not in str(result.get("dept")) else "普通门诊",
            "document_type": _clean_document_type(result.get("document_type")),
            "chief_complaint": _clean_detail_text(result.get("chief_complaint")),
            "diagnosis": _clean_detail_text(result.get("diagnosis")),
            "treatment_opinion": _clean_detail_text(result.get("treatment_opinion")),
            "prescription_text": _clean_detail_text(result.get("prescription_text")),
            "examination_text": _clean_detail_text(result.get("examination_text")),
            "instructions_text": _clean_detail_text(result.get("instructions_text")),
            "order_text": _clean_detail_text(result.get("order_text")),
            "guide_text": _clean_detail_text(result.get("guide_text")),
            "medication_text": _clean_detail_text(result.get("medication_text")),
            "dosage_text": _clean_detail_text(result.get("dosage_text")),
            "raw_content": _clean_detail_text(result.get("raw_content")),
            "confidence": result.get("confidence"),
            "name_source": str(result.get("name_source") or ""),
        }
        # 分类一致性校正：明细证据与粗分类矛盾时以明细为准
        # （处方/导引单/缴费凭条等在旧6类中没有对应类别，模型曾被迫选"6-无有效医疗图像"）
        if info["document_type"] == "处方" and (info["prescription_text"] or info["medication_text"]):
            info["classify"] = "7-处方单据"
        elif info["document_type"] == "检查申请单" and info["examination_text"]:
            info["classify"] = "9-检查申请单"
        elif info["document_type"] == "检验报告" and info["examination_text"]:
            info["classify"] = "5-检验报告单"
        elif info["document_type"] == "导引单" and info["guide_text"]:
            info["classify"] = "8-导引单"
        elif info["document_type"] == "缴费票据" and info["raw_content"]:
            info["classify"] = "10-缴费票据"
        return info

    # 回退默认值
    return {
        "classify": "6-无有效医疗图像",
        "name": "未知患者",
        "date": "未知日期",
        "dept": "普通门诊",
        "document_type": "病例",
        "chief_complaint": "",
        "diagnosis": "",
        "treatment_opinion": "",
        "prescription_text": "",
        "examination_text": "",
        "instructions_text": "",
        "order_text": "",
        "guide_text": "",
        "medication_text": "",
        "dosage_text": "",
        "raw_content": ""
    }

# 候选姓名模糊匹配：把模型识别的姓名校正到候选名单中最接近的一个
# 匹配不上时保留原始识别结果（不丢弃），仅做清洗
def _match_candidate_name(raw_name, candidate_names: list) -> str:
    if not raw_name or not isinstance(raw_name, str):
        return "未知患者"
    name = raw_name.strip()
    # 去除常见前缀
    name = re.sub(r'^(姓名|患者|病人|名)\s*[:：]?\s*', '', name)
    # 只保留汉字
    name = re.sub(r'[^\u4e00-\u9fa5]', '', name)
    if not name:
        return "未知患者"
    # 去除"未知"、"无法识别"等无效值
    if any(kw in name for kw in ("未知", "无法识别", "提取", "空")):
        return "未知患者"
    # 完全匹配
    if name in candidate_names:
        return name
    # 模糊匹配：综合相似度与编辑距离
    best_match = None
    best_score = 0.0
    for cand in candidate_names:
        # 完全包含关系
        if name in cand or cand in name:
            score = min(len(name), len(cand)) / max(len(name), len(cand))
        else:
            score = SequenceMatcher(None, name, cand).ratio()
        if score > best_score:
            best_score = score
            best_match = cand
    # 相似度阈值提高到0.7：只有高度相似才校正（避免把不同姓名误判为错别字）
    # 同时要求编辑距离≤1（仅允许1个字的差异，符合"个别错别字"的语义）
    if best_match and best_score >= 0.7:
        edit_dist = _edit_distance(name, best_match)
        if edit_dist <= 1:
            return best_match
        # 编辑距离=2但相似度很高（如3字名错2字），仍可校正
        if edit_dist == 2 and best_score >= 0.85:
            return best_match
    # 匹配不上：保留原始识别结果（只做长度校验）
    if 2 <= len(name) <= 4:
        return name
    return "未知患者"

# 编辑距离（Levenshtein）：用于姓名校正时判断差异字数
def _edit_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]

# 姓名清洗：去除前缀、只保留汉字、校验长度
def _clean_name(raw_name) -> str:
    if not raw_name or not isinstance(raw_name, str):
        return "未知患者"
    name = raw_name.strip()
    # 去除常见前缀
    name = re.sub(r'^(姓名|患者|病人|名)\s*[:：]?\s*', '', name)
    # 去除"未知"、"无法识别"等无效值
    if any(kw in name for kw in ("未知", "无法识别", "提取", "无", "空")):
        return "未知患者"
    # 只保留汉字（过滤掉标点、字母、数字、空格）
    hanzi_only = re.sub(r'[^\u4e00-\u9fa5]', '', name)
    # 中文姓名通常2-4字，超出范围视为异常
    if 2 <= len(hanzi_only) <= 4:
        return hanzi_only
    # 如果清洗后长度异常，但原始值较短，保留原始值
    if 2 <= len(name) <= 6 and re.search(r'[\u4e00-\u9fa5]', name):
        return name
    return "未知患者"

# 清洗医疗单详细内容：去掉无效占位值，返回纯文本
def _clean_detail_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in ("...", "无", "没有", "无内容", "None", "null", "空"):
        return ""
    return text

# 清洗记录类型：只保留合法枚举，其余归为"病例"
def _clean_document_type(value) -> str:
    valid_types = {"医嘱", "导引单", "处方", "检查申请单", "检验报告", "用药", "病例", "缴费票据"}
    if value in valid_types:
        return value
    return "病例"

# 姓名二次提取：用更高分辨率+专门prompt重新识别姓名
def _ollama_extract_name_sync(base64_img: str, candidate_names: list = None) -> str:
    if candidate_names:
        prompt = f"""任务：从候选名单中选出图片上患者的姓名。

候选名单：{json.dumps(candidate_names, ensure_ascii=False)}

判断步骤：
1. 在图片上定位姓名（"姓名:"、"患者:"字段后，或表格首行、印章附近）
2. 仔细辨认图片上姓名的每个字，注意区分形近字（日/曰、己/已/巳、未/末、土/士等）
3. 将辨认结果与候选名单逐一比对，选出最匹配的一个：
   - 完全一致或仅个别字形相近/印刷模糊：选名单中的那个
   - 与所有候选都明显不同（差异字数≥2）：输出图片上实际看到的姓名
4. 只输出纯姓名（2-4个汉字），不含任何前缀或解释

只输出JSON：{{"name":"..."}}"""
    else:
        prompt = f"""任务：辨认图片上患者的姓名。

要求：
1. 定位姓名位置（"姓名:"、"患者:"、"Name"字段后，或表格首行、印章附近）
2. 逐字辨认，注意区分形近字：日/曰、己/已/巳、戊/戌/戍、未/末、土/士、人/入、千/干
3. 只输出纯姓名（2-4个汉字），不含任何前缀、标点或解释
4. 必须输出图片上实际看到的姓名，不要输出空字符串

只输出JSON：{{"name":"..."}}"""
    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [base64_img]
        }],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0.0,      # 姓名提取需要最高确定性
            "num_predict": 50,
            "num_ctx": 1024
        }
    }
    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=NAME_TIMEOUT)
        if resp.status_code != 200:
            return "未知患者"
        msg = resp.json().get("message", {})
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        # 先从content提取JSON，提取不到再从thinking提取
        result = _extract_json(content) or _extract_json(thinking)
        if result:
            raw_name = result.get("name")
            if candidate_names:
                return _match_candidate_name(raw_name, candidate_names)
            return _clean_name(raw_name)
    except Exception:
        pass
    return "未知患者"

# 异步封装：在线程池中执行，不阻塞事件循环；用信号量控制并发数
async def ollama_extract_info(base64_img: str, raw_img: Image.Image = None, candidate_names: list = None) -> dict:
    async with _ollama_semaphore:
        info = await asyncio.to_thread(_ollama_extract_info_sync, base64_img, candidate_names)
    # 姓名识别失败时，用更高分辨率+专门prompt二次提取
    if info.get("name") == "未知患者" and raw_img is not None:
        # 重新用更高分辨率预处理
        hi_img = raw_img.copy()
        hi_img.thumbnail((MAX_LONG_SIDE_HI, MAX_LONG_SIDE_HI))
        hi_b64 = image_to_base64(hi_img)
        async with _ollama_semaphore:
            retry_name = await asyncio.to_thread(_ollama_extract_name_sync, hi_b64, candidate_names)
        if retry_name != "未知患者":
            info["name"] = retry_name
            print(f"  [二次提取] 姓名识别成功: {retry_name}")
    return info

# 构建分层归档路径：输出根/姓名/日期/科室
def build_target_dir(name: str, dept: str, date: str) -> str:
    target_dir = os.path.join(OUTPUT_ROOT, name, date, dept)
    os.makedirs(target_dir, exist_ok=True)
    return target_dir

# 运行时自愈：数据库文件或临时/归档目录被删除后，无需重启服务即可自动重建
# （目录只在模块加载时创建、表只在startup事件创建，运行时删掉必须在此兜底）
def ensure_runtime_env():
    os.makedirs(SAVE_IMG_DIR, exist_ok=True)
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    init_db()
    ensure_medical_record_tables()

# 质检规则引擎：入库前对识别结果做确定性自检，可疑记录打上 quality_flag 供人工抽查
# 规则来自实战误报复盘：空明细/兜底值/机构名当科室/分类矛盾/缺类型特征词/模型低自信/姓名靠猜
def _to_confidence(value) -> int:
    try:
        return max(0, min(10, int(float(value))))
    except (TypeError, ValueError):
        return 10  # 模型未输出置信度时不视为可疑

def _quality_check(info: dict) -> str:
    flags = []
    dtype = info.get("document_type") or ""
    detail_filled = any(info.get(k) for k in (
        "chief_complaint", "diagnosis", "treatment_opinion", "prescription_text",
        "examination_text", "instructions_text", "order_text", "guide_text",
        "medication_text", "raw_content"))
    if not detail_filled:
        flags.append("all_detail_empty")       # 明细全空
    detail_len = sum(len(str(info.get(k) or "")) for k in (
        "chief_complaint", "diagnosis", "treatment_opinion", "prescription_text",
        "examination_text", "instructions_text", "order_text", "guide_text",
        "medication_text", "dosage_text"))
    if len(info.get("raw_content") or "") < 40 and detail_len < 60:
        flags.append("content_underread")      # 原文与明细总量都过短，疑似没读全
    if info.get("name") == "未知患者":
        flags.append("name_unknown")
    if info.get("date") == "未知日期":
        flags.append("date_unknown")
    dept = info.get("dept") or ""
    if any(w in dept for w in ("医院", "大学", "学院", "凭证", "票据", "单据", "清单", "自助", "药房")):
        flags.append("dept_suspicious")        # 机构名/单据名被当成科室
    cls = str(info.get("classify") or "")
    expect = {"处方": "7", "导引单": "8", "检查申请单": "9", "缴费票据": "10", "检验报告": "5"}
    if dtype in expect and not cls.startswith(expect[dtype]):
        flags.append("dtype_class_mismatch")   # 细分类与粗分类矛盾
    if dtype == "处方" and not re.search(
            r"(mg|毫克|\d+\s*(片|盒|粒|支|瓶|袋)|口服|每日|每晚|次/日|/日)",
            info.get("prescription_text") or ""):
        flags.append("prescription_no_dose")   # 处方缺剂量/用法特征词
    if dtype == "缴费票据" and not re.search(r"(元|￥|¥)", info.get("raw_content") or ""):
        flags.append("payment_no_amount")      # 缴费票据缺金额特征
    if dtype == "检验报告":
        exam = info.get("examination_text") or ""
        if exam and not re.search(r"\d|通过|未见|正常|异常|阳性|阴性|提示|参考", exam):
            flags.append("exam_no_result")     # 检验报告缺数值/结论词
    if _to_confidence(info.get("confidence")) < 7:
        flags.append("low_confidence")         # 模型自评低置信
    if str(info.get("name_source") or "").lower().startswith("guess"):
        flags.append("name_guessed")           # 模型自认姓名是猜的
    return ",".join(flags)

# 将分类结果写入医疗记录台账表（供查询/台账页面使用）
def insert_medical_record_entry(cur, name: str, date: str, dept: str, info: dict, dst_path: str) -> None:
    quality = _quality_check(info)
    if quality:
        print(f"  [质检可疑] {info.get('name')}/{info.get('document_type')}: {quality}")
    cur.execute('''
        INSERT INTO medical_record_entries
        (patient_name, case_name, record_date, dept, document_type,
         chief_complaint, diagnosis, treatment_opinion, prescription_text,
         examination_text, instructions_text,
         order_text, guide_text, medication_text, dosage_text,
         raw_content, source_path, remark, quality_flag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        name,
        "",                       # 病例/就诊编号：分类流程不单独提取，留空
        date,
        dept,
        info.get("document_type", "病例"),
        info.get("chief_complaint", ""),
        info.get("diagnosis", ""),
        info.get("treatment_opinion", ""),
        info.get("prescription_text", ""),
        info.get("examination_text", ""),
        info.get("instructions_text", ""),
        info.get("order_text", ""),
        info.get("guide_text", ""),
        info.get("medication_text", ""),
        info.get("dosage_text", ""),
        info.get("raw_content", ""),
        dst_path,
        "分类归档",
        quality,
    ))

# 单张图片上传接口（单文件测试）
@app.post("/upload_case")
async def upload_case(
    file: UploadFile = File(...),
    candidate_names: str = Form("", description="候选姓名列表，逗号分隔，用于校正识别结果")
):
    try:
        ensure_runtime_env()
        content = await file.read()
        raw_img = Image.open(BytesIO(content)).convert("RGB")
        proc_img = preprocess_image(raw_img)

        # 临时保存
        temp_name = f"{len(os.listdir(SAVE_IMG_DIR)) + 1}.jpg"
        temp_full_path = os.path.join(SAVE_IMG_DIR, temp_name)
        proc_img.save(temp_full_path)

        # 解析候选姓名列表（兼容中英文逗号、顿号分隔）
        candidates = [n.strip() for n in re.split(r"[,，、]", candidate_names) if n.strip()] if candidate_names else None

        # 调用模型提取信息（分类+姓名+日期+科室）
        b64_data = image_to_base64(proc_img)
        info = await ollama_extract_info(b64_data, raw_img, candidates)
        res_class = info.get("classify", "6-无有效医疗图像")
        final_name = info.get("name", "未知患者")
        final_date = info.get("date", "未知日期")
        final_dept = info.get("dept", "普通门诊")

        # 创建归档目录并复制图片
        target_dir = build_target_dir(final_name, final_dept, final_date)
        dst_filename = f"{final_name}_{res_class.split('-')[0]}_{temp_name}"
        dst_full_path = os.path.join(target_dir, dst_filename)
        proc_img.save(dst_full_path)

        # 入库记录（分类归档表 + 医疗记录台账，供查询页面使用）
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO case_records 
            (src_path, dst_path, name, dept, date, classify_result) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (temp_full_path, dst_full_path, final_name, final_dept, final_date, res_class))
        insert_medical_record_entry(cur, final_name, final_date, final_dept, info, dst_full_path)
        conn.commit()
        conn.close()

        return JSONResponse({
            "code": 0,
            "msg": "分类归档成功",
            "model_used": MODEL_NAME,
            "temp_path": temp_full_path,
            "archive_path": dst_full_path,
            "classify": res_class,
            "extracted_name": final_name,
            "extracted_date": final_date,
            "extracted_dept": final_dept
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 批量图片上传接口（前端批量选择文件夹图片使用）
# 使用并发调用Ollama（MAX_CONCURRENT控制并发数），不再串行逐张处理
@app.post("/batch_upload")
async def batch_upload(
    files: List[UploadFile] = File(..., description="选择图片文件（JPG/PNG），支持多选"),
    candidate_names: str = Form("", description="候选姓名列表，逗号分隔，用于校正识别结果")
):
    ensure_runtime_env()
    total = len(files)
    print(f"[batch] 收到 {total} 张图片，并发数={MAX_CONCURRENT}，开始处理...")

    # 解析候选姓名列表（兼容中英文逗号、顿号分隔）
    candidates = [n.strip() for n in re.split(r"[,，、]", candidate_names) if n.strip()] if candidate_names else None
    if candidates:
        print(f"[batch] 候选姓名名单: {candidates}")

    # 复用单个数据库连接（线程安全：sqlite3 连接在创建它的线程使用，
    # 这里在主事件循环线程中统一写入）
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 单张图片的完整处理流程（预处理+AI识别+归档+入库）
    async def process_one(global_idx: int, file: UploadFile) -> dict:
        print(f"  [{global_idx+1}/{total}] 开始: {file.filename}")
        try:
            content = await file.read()
            raw_img = Image.open(BytesIO(content)).convert("RGB")
            proc_img = preprocess_image(raw_img)

            # 临时存储
            temp_name = f"batch_{global_idx}_{file.filename}"
            temp_full_path = os.path.join(SAVE_IMG_DIR, temp_name)
            proc_img.save(temp_full_path)

            # AI提取信息（分类+姓名+日期+科室）——并发调用
            b64_data = image_to_base64(proc_img)
            info = await ollama_extract_info(b64_data, raw_img, candidates)
            res_class = info.get("classify", "6-无有效医疗图像")
            final_name = info.get("name", "未知患者")
            final_date = info.get("date", "未知日期")
            final_dept = info.get("dept", "普通门诊")

            # 归档
            target_dir = build_target_dir(final_name, final_dept, final_date)
            dst_filename = f"{final_name}_{res_class.split('-')[0]}_{file.filename}"
            dst_full_path = os.path.join(target_dir, dst_filename)
            proc_img.save(dst_full_path)

            # 入库（在主线程串行写入，避免锁冲突）
            cur.execute('''
                INSERT INTO case_records
                (src_path, dst_path, name, dept, date, classify_result)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (temp_full_path, dst_full_path, final_name, final_dept, final_date, res_class))
            insert_medical_record_entry(cur, final_name, final_date, final_dept, info, dst_full_path)

            print(f"  [{global_idx+1}/{total}] 完成: {file.filename} -> {res_class}")
            return {
                "filename": file.filename,
                "classify": res_class,
                "archive_path": dst_full_path,
                "extracted_name": final_name,
                "extracted_date": final_date,
                "extracted_dept": final_dept,
                "status": "success"
            }
        except Exception as e:
            print(f"  [{global_idx+1}/{total}] 失败: {file.filename} -> {e}")
            return {
                "filename": file.filename,
                "status": "fail",
                "error": str(e)
            }

    # 并发处理所有图片（信号量已限制对Ollama的并发请求数）
    tasks = [process_one(i, f) for i, f in enumerate(files)]
    result_list = await asyncio.gather(*tasks)

    conn.commit()
    conn.close()

    success_count = sum(1 for r in result_list if r.get("status") == "success")
    print(f"[batch] 全部完成：总数{total}，成功{success_count}张")

    return JSONResponse({
        "code": 0,
        "msg": f"批量处理完成，总数{total}，成功{success_count}张",
        "data": result_list
    })

# 质检报告：列出所有带质量标记的可疑记录，供人工抽查与规则命中率统计
@app.get("/quality/report")
async def quality_report():
    ensure_runtime_env()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, patient_name, record_date, dept, document_type, quality_flag, source_path "
        "FROM medical_record_entries WHERE quality_flag IS NOT NULL AND quality_flag != '' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    stats = {}
    for r in rows:
        for f in (r["quality_flag"] or "").split(","):
            if f:
                stats[f] = stats.get(f, 0) + 1
    return JSONResponse({
        "code": 0,
        "suspect_count": len(rows),
        "flag_stats": stats,
        "data": [dict(r) for r in rows],
    })

# 查询历史记录
@app.get("/list_records")
async def list_records():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM case_records ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    data = []
    for r in rows:
        data.append({
            "id": r[0],
            "src_path": r[1],
            "dst_path": r[2],
            "name": r[3],
            "dept": r[4],
            "date": r[5],
            "classify": r[6],
            "time": r[7]
        })
    return {"code": 0, "data": data}

if __name__ == "__main__":
    init_db()
    import uvicorn
    # 开放局域网所有设备访问
    uvicorn.run(app, host="0.0.0.0", port=8000)