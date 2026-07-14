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

# ===================== 配置区 =====================
# 支持环境变量覆盖，适配 Docker / 云端 / 远程 Ollama 等场景
OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b")   # 视觉语言模型，识别质量远优于 glm-ocr
CLASS_LIST = [
    "1-胸部CT影像",
    "2-血常规化验单",
    "3-纸质手写门诊病历",
    "4-口腔影像片",
    "5-检验报告单",
    "6-无有效医疗图像"
]
# 基于脚本所在目录的绝对路径，避免工作目录变化导致文件散落各处
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_IMG_DIR = os.path.join(_BASE_DIR, "case_images_temp")
OUTPUT_ROOT = os.path.join(_BASE_DIR, "case_output")
DB_PATH = os.path.join(_BASE_DIR, "case_record.db")
# 并发调用Ollama的最大数量（需与 OLLAMA_NUM_PARALLEL 匹配，建议2-4）
MAX_CONCURRENT = 4
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

# 应用启动时自动初始化数据库（uvicorn命令行启动不会执行__main__块，
# 必须用startup事件保证表一定被创建）
@app.on_event("startup")
def _startup_init_db():
    init_db()

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

    prompt = f"""分析这张医疗图片，完成两项任务：

任务一【分类】：classify从以下选项中选最匹配的一项：[{', '.join(CLASS_LIST)}]
任务二【信息提取】：提取患者姓名name、就诊日期date(格式YYYY-MM-DD)、科室dept
{name_section}

【日期提取】格式YYYY-MM-DD，找不到则输出"未知日期"
【科室提取】输出图片上明确标注的科室，找不到则输出"普通门诊"

只输出JSON，格式：{{"classify":"...","name":"...","date":"...","dept":"..."}}"""
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
            "num_predict": 200,      # 关闭思考后200token足够
            "num_ctx": 2048          # 单图+短prompt，2k上下文足够
        }
    }
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=60)
    if resp.status_code != 200:
        raise Exception(f"Ollama接口异常: {resp.text}")

    msg = resp.json().get("message", {})
    # 优先取 content，为空时从 thinking 中提取（部分模型把JSON输出到thinking字段）
    response_text = (msg.get("content") or "").strip()
    if not response_text:
        thinking = (msg.get("thinking") or "").strip()
        # 从thinking中提取最后一个JSON对象
        json_match = re.search(r'\{[^{}]*\}', thinking)
        if json_match:
            response_text = json_match.group()
    # 尝试从响应中提取JSON
    json_match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}', response_text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            raw_name = result.get("name")
            # 如果提供了候选名单，对识别结果做模糊匹配校正
            if candidate_names:
                matched_name = _match_candidate_name(raw_name, candidate_names)
            else:
                matched_name = _clean_name(raw_name)
            # 清洗：如果值是模板文字或空字符串，替换为默认值
            return {
                "classify": result.get("classify", "6-无有效医疗图像") if result.get("classify") not in ("", "从以下列表选择最匹配的一项", None) else "6-无有效医疗图像",
                "name": matched_name,
                "date": result.get("date") if result.get("date") and re.match(r'\d{4}-\d{2}-\d{2}', str(result.get("date"))) else "未知日期",
                "dept": result.get("dept") if result.get("dept") and "推断" not in str(result.get("dept")) else "普通门诊"
            }
        except json.JSONDecodeError:
            pass

    # 回退默认值
    return {
        "classify": "6-无有效医疗图像",
        "name": "未知患者",
        "date": "未知日期",
        "dept": "普通门诊"
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
        resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=60)
        if resp.status_code != 200:
            return "未知患者"
        msg = resp.json().get("message", {})
        response_text = (msg.get("content") or "").strip()
        if not response_text:
            thinking = (msg.get("thinking") or "").strip()
            json_match = re.search(r'\{[^{}]*\}', thinking)
            if json_match:
                response_text = json_match.group()
        json_match = re.search(r'\{[^{}]*\}', response_text)
        if json_match:
            result = json.loads(json_match.group())
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

# 单张图片上传接口（单文件测试）
@app.post("/upload_case")
async def upload_case(
    file: UploadFile = File(...),
    candidate_names: str = Form("", description="候选姓名列表，逗号分隔，用于校正识别结果")
):
    try:
        content = await file.read()
        raw_img = Image.open(BytesIO(content)).convert("RGB")
        proc_img = preprocess_image(raw_img)

        # 临时保存
        temp_name = f"{len(os.listdir(SAVE_IMG_DIR)) + 1}.jpg"
        temp_full_path = os.path.join(SAVE_IMG_DIR, temp_name)
        proc_img.save(temp_full_path)

        # 解析候选姓名列表
        candidates = [n.strip() for n in candidate_names.split(",") if n.strip()] if candidate_names else None

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

        # 入库记录
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO case_records 
            (src_path, dst_path, name, dept, date, classify_result) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (temp_full_path, dst_full_path, final_name, final_dept, final_date, res_class))
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
    total = len(files)
    print(f"[batch] 收到 {total} 张图片，并发数={MAX_CONCURRENT}，开始处理...")

    # 解析候选姓名列表
    candidates = [n.strip() for n in candidate_names.split(",") if n.strip()] if candidate_names else None
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