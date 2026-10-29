import csv
import io
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import Body
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "case_record.db")

# 医疗单结构化明细字段：用于记录处方（药品/规格/剂量/用法）、检查申请、病例主诉/诊断等
DETAIL_COLUMNS = [
    "chief_complaint",      # 主诉/病情描述
    "diagnosis",            # 诊断
    "treatment_opinion",    # 处理意见
    "prescription_text",    # 处方详情（药品名称+规格+剂量+用法）
    "examination_text",     # 检查申请内容/描述
    "instructions_text",    # 嘱托/注意事项
]


def ensure_medical_record_tables() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
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
        """
    )
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(medical_record_entries)")]
    for col in ["dept", "quality_flag", *DETAIL_COLUMNS]:
        if col not in columns:
            cursor.execute(f"ALTER TABLE medical_record_entries ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()


class MedicalRecordInput(BaseModel):
    patient_name: Optional[str] = Field(default=None, description="患者姓名")
    case_name: Optional[str] = Field(default=None, description="病例/就诊编号")
    record_date: Optional[str] = Field(default=None, description="记录日期，格式 YYYY-MM-DD")
    dept: Optional[str] = Field(default="", description="就诊科室")
    document_type: str = Field(default="病例", description="记录类型：病例/医嘱/导引单/处方/检查申请单/检验报告/用药/缴费票据")
    chief_complaint: Optional[str] = Field(default="", description="主诉/病情描述")
    diagnosis: Optional[str] = Field(default="", description="诊断")
    treatment_opinion: Optional[str] = Field(default="", description="处理意见")
    prescription_text: Optional[str] = Field(default="", description="处方详情（药品/规格/剂量/用法）")
    examination_text: Optional[str] = Field(default="", description="检查申请内容/描述")
    instructions_text: Optional[str] = Field(default="", description="嘱托/注意事项")
    order_text: Optional[str] = Field(default="", description="医嘱内容")
    guide_text: Optional[str] = Field(default="", description="导引单内容")
    medication_text: Optional[str] = Field(default="", description="药品名称")
    dosage_text: Optional[str] = Field(default="", description="用药用量")
    raw_content: Optional[str] = Field(default="", description="原始医疗单内容")
    source_path: Optional[str] = Field(default="", description="来源文件/图片路径")
    remark: Optional[str] = Field(default="", description="备注")


class MedicalRecordBatchInput(BaseModel):
    records: List[MedicalRecordInput] = Field(default_factory=list, description="批量录入的医疗记录数组")


def _normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _record_query_filters(
    patient_name: Optional[str] = None,
    case_name: Optional[str] = None,
    record_date: Optional[str] = None,
    dept: Optional[str] = None,
    document_type: Optional[str] = None,
    keyword: Optional[str] = None,
    quality_flag: Optional[str] = None,
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []

    if patient_name:
        clauses.append("patient_name LIKE ?")
        params.append(f"%{patient_name}%")
    if case_name:
        clauses.append("case_name LIKE ?")
        params.append(f"%{case_name}%")
    if record_date:
        clauses.append("record_date LIKE ?")
        params.append(f"%{record_date}%")
    if dept:
        clauses.append("dept LIKE ?")
        params.append(f"%{dept}%")
    if document_type:
        clauses.append("document_type = ?")
        params.append(document_type)
    if quality_flag == "suspect":
        clauses.append("quality_flag IS NOT NULL AND quality_flag != ''")
    if keyword:
        clauses.append(
            "(order_text LIKE ? OR guide_text LIKE ? OR medication_text LIKE ? OR dosage_text LIKE ? "
            "OR chief_complaint LIKE ? OR diagnosis LIKE ? OR treatment_opinion LIKE ? "
            "OR prescription_text LIKE ? OR examination_text LIKE ? OR instructions_text LIKE ? "
            "OR raw_content LIKE ? OR remark LIKE ?)"
        )
        like_kw = f"%{keyword}%"
        params.extend([like_kw] * 12)

    where_sql = " AND ".join(clauses)
    return (f"WHERE {where_sql}" if where_sql else "", params)


def _serialize_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "patient_name": row["patient_name"],
        "case_name": row["case_name"],
        "record_date": row["record_date"],
        "dept": row["dept"],
        "document_type": row["document_type"],
        "chief_complaint": row["chief_complaint"],
        "diagnosis": row["diagnosis"],
        "treatment_opinion": row["treatment_opinion"],
        "prescription_text": row["prescription_text"],
        "examination_text": row["examination_text"],
        "instructions_text": row["instructions_text"],
        "order_text": row["order_text"],
        "guide_text": row["guide_text"],
        "medication_text": row["medication_text"],
        "dosage_text": row["dosage_text"],
        "raw_content": row["raw_content"],
        "source_path": row["source_path"],
        "remark": row["remark"],
        "quality_flag": row["quality_flag"] if "quality_flag" in row.keys() else "",
        "create_time": row["create_time"],
    }


def _from_raw_text_block(raw_text: str) -> Dict[str, Any]:
    content = (raw_text or "").strip()
    if not content:
        return {}

    patient_name = ""
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content)
    dept_match = re.search(r"(?:科室|部门|诊室)[:：\s]+([^\n]+)", content)
    patient_match = re.search(r"(?:患者|姓名)[:：\s]+([^\n]+)", content)

    if patient_match:
        patient_name = patient_match.group(1).strip()
    if not patient_name:
        patient_name_match = re.search(r"[\u4e00-\u9fa5]{2,4}", content)
        if patient_name_match is not None:
            patient_name = patient_name_match.group(0)

    record: Dict[str, Any] = {
        "patient_name": patient_name,
        "record_date": date_match.group(1) if date_match else "",
        "dept": dept_match.group(1).strip() if dept_match else "",
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
        "raw_content": content,
        "source_path": "",
        "remark": "批量导入",
    }

    if "处方" in content or "规格" in content:
        record["document_type"] = "处方"
    elif "医嘱" in content or "嘱" in content:
        record["document_type"] = "医嘱"
    elif "导引" in content or "复诊" in content:
        record["document_type"] = "导引单"
    elif "检查" in content or "检验" in content:
        record["document_type"] = "检查申请单"
    elif "药" in content or "服药" in content:
        record["document_type"] = "用药"

    for key, label in {
        "chief_complaint": "主诉",
        "diagnosis": "诊断",
        "treatment_opinion": "处理意见",
        "prescription_text": "处方",
        "examination_text": "检查",
        "instructions_text": "嘱托",
        "order_text": "医嘱",
        "guide_text": "导引单",
        "medication_text": "药品",
        "dosage_text": "用量",
    }.items():
        m = re.search(rf"(?:{label})[:：\s]+([^\n]+)", content)
        if m:
            record[key] = m.group(1).strip()

    return record


async def create_medical_record(payload: MedicalRecordInput = Body(...)) -> Dict[str, Any]:
    ensure_medical_record_tables()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO medical_record_entries (
            patient_name, case_name, record_date, dept, document_type,
            chief_complaint, diagnosis, treatment_opinion, prescription_text,
            examination_text, instructions_text,
            order_text, guide_text, medication_text, dosage_text,
            raw_content, source_path, remark
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _normalize_text(payload.patient_name),
            _normalize_text(payload.case_name),
            _normalize_text(payload.record_date),
            _normalize_text(payload.dept),
            _normalize_text(payload.document_type) or "病例",
            _normalize_text(payload.chief_complaint),
            _normalize_text(payload.diagnosis),
            _normalize_text(payload.treatment_opinion),
            _normalize_text(payload.prescription_text),
            _normalize_text(payload.examination_text),
            _normalize_text(payload.instructions_text),
            _normalize_text(payload.order_text),
            _normalize_text(payload.guide_text),
            _normalize_text(payload.medication_text),
            _normalize_text(payload.dosage_text),
            _normalize_text(payload.raw_content),
            _normalize_text(payload.source_path),
            _normalize_text(payload.remark),
        ),
    )
    conn.commit()
    record_id = cursor.lastrowid
    row = cursor.execute(
        "SELECT * FROM medical_record_entries WHERE id = ?",
        (record_id,),
    ).fetchone()
    conn.close()

    return {
        "code": 0,
        "msg": "医疗记录已保存",
        "data": _serialize_row(row),
    }


async def import_medical_records(payload: MedicalRecordBatchInput = Body(...)) -> Dict[str, Any]:
    ensure_medical_record_tables()
    records = payload.records or []
    if not records:
        return {"code": 1, "msg": "没有需要导入的记录", "count": 0}

    inserted_ids: List[int] = []
    for item in records:
        if not item.patient_name and not item.raw_content:
            continue
        inserted = await create_medical_record(item)
        data = inserted.get("data") or {}
        row_id = data.get("id")
        if row_id:
            inserted_ids.append(row_id)

    return {"code": 0, "msg": "批量导入完成", "count": len(inserted_ids), "ids": inserted_ids}


async def import_raw_text_blocks(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    raw_value = payload.get("raw_text") if isinstance(payload, dict) else ""
    blocks = [block.strip() for block in str(raw_value).split("---") if block.strip()]
    records = [MedicalRecordInput(**_from_raw_text_block(block)) for block in blocks if _from_raw_text_block(block)]
    if not records:
        return {"code": 1, "msg": "未识别到可导入的病例内容", "count": 0}
    return await import_medical_records(MedicalRecordBatchInput(records=records))


async def query_medical_records(
    patient_name: Optional[str] = None,
    case_name: Optional[str] = None,
    record_date: Optional[str] = None,
    dept: Optional[str] = None,
    document_type: Optional[str] = None,
    keyword: Optional[str] = None,
    quality_flag: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_medical_record_tables()

    where_sql, params = _record_query_filters(
        patient_name=patient_name,
        case_name=case_name,
        record_date=record_date,
        dept=dept,
        document_type=document_type,
        keyword=keyword,
        quality_flag=quality_flag,
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    rows = cursor.execute(
        f"SELECT * FROM medical_record_entries {where_sql} ORDER BY id DESC",
        params,
    ).fetchall()
    conn.close()

    return {
        "code": 0,
        "count": len(rows),
        "data": [_serialize_row(r) for r in rows],
    }


async def list_medical_records() -> Dict[str, Any]:
    return await query_medical_records()


async def get_medical_record(record_id: int) -> Dict[str, Any]:
    ensure_medical_record_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM medical_record_entries WHERE id = ?", (record_id,)).fetchone()
    conn.close()
    if row is None:
        return {"code": 1, "msg": "记录不存在"}
    return {"code": 0, "data": _serialize_row(row)}


async def export_medical_records_csv(
    patient_name: Optional[str] = None,
    case_name: Optional[str] = None,
    record_date: Optional[str] = None,
    dept: Optional[str] = None,
    document_type: Optional[str] = None,
    keyword: Optional[str] = None,
    quality_flag: Optional[str] = None,
) -> PlainTextResponse:
    ensure_medical_record_tables()
    where_sql, params = _record_query_filters(
        patient_name=patient_name,
        case_name=case_name,
        record_date=record_date,
        dept=dept,
        document_type=document_type,
        keyword=keyword,
        quality_flag=quality_flag,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM medical_record_entries {where_sql} ORDER BY id DESC",
        params,
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "id", "patient_name", "case_name", "record_date", "dept", "document_type",
        "chief_complaint", "diagnosis", "treatment_opinion", "prescription_text",
        "examination_text", "instructions_text",
        "order_text", "guide_text", "medication_text", "dosage_text",
        "source_path", "remark", "create_time"
    ])
    for row in rows:
        writer.writerow([
            row["id"], row["patient_name"], row["case_name"], row["record_date"], row["dept"],
            row["document_type"], row["chief_complaint"], row["diagnosis"], row["treatment_opinion"],
            row["prescription_text"], row["examination_text"], row["instructions_text"],
            row["order_text"], row["guide_text"], row["medication_text"],
            row["dosage_text"], row["source_path"], row["remark"], row["create_time"],
        ])

    csv_text = output.getvalue()
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=medical_records.csv"},
    )


def register_query_app(app) -> None:
    ensure_medical_record_tables()
    app.add_api_route("/medical_records/record", create_medical_record, methods=["POST"])
    app.add_api_route("/medical_records/import", import_medical_records, methods=["POST"])
    app.add_api_route("/medical_records/import_raw", import_raw_text_blocks, methods=["POST"])
    app.add_api_route("/medical_records/query", query_medical_records, methods=["GET"])
    app.add_api_route("/medical_records/list", list_medical_records, methods=["GET"])
    app.add_api_route("/medical_records/export", export_medical_records_csv, methods=["GET"])
    app.add_api_route("/medical_records/{record_id}", get_medical_record, methods=["GET"])
