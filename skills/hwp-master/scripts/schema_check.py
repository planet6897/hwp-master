#!/usr/bin/env python3
"""표준 라이브러리만 쓰는 최소 JSON Schema 검사기 (Extract 결과 형식 확인용).

지원: type(목록 가능)·required·properties·additionalProperties(false)·items·enum·const·pattern·minimum·
minItems·anyOf·$ref("#/$defs/이름"). 그 밖의 키워드는 무시한다.

    python3 schema_check.py <exports/extract/문서 폴더>   # 그 폴더의 결과 파일을 schemas/extract/ 로 검사
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas" / "extract"
FILES = {"summary.json": "summary", "document.json": "document", "outline.json": "outline", "tables.json": "tables",
         "data.json": "data", "kv.json": "kv", "fields.json": "fields", "pages.json": "pages",
         "structure.json": "structure", "headers_footers.json": "headers_footers", "charts.json": "charts"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def validate(value, schema: dict, root: dict = None, path: str = "$") -> List[str]:
    root = root or schema
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return validate(value, root["$defs"][name], root, path)
    errors: List[str] = []
    if "anyOf" in schema:
        if all(validate(value, option, root, path) for option in schema["anyOf"]):
            errors.append(f"{path}: anyOf 의 어느 형식에도 맞지 않음")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return errors + [f"{path}: {'/'.join(types)} 이어야 하는데 {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: {schema['const']!r} 이어야 함")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {schema['enum']} 가운데 하나여야 하는데 {value!r}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: 형식 {schema['pattern']} 에 맞지 않음 ({value[:40]!r})")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema \
            and value < schema["minimum"]:
        errors.append(f"{path}: {schema['minimum']} 이상이어야 함")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: 필수 키 {key!r} 없음")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors += validate(value[key], sub, root, f"{path}.{key}")
        extra = schema.get("additionalProperties")
        if extra is False:
            errors += [f"{path}: 정의되지 않은 키 {k!r}" for k in value if k not in props]
        elif isinstance(extra, dict):
            for key in value:
                if key not in props:
                    errors += validate(value[key], extra, root, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: 항목이 {schema['minItems']}개 이상이어야 함")
        if "items" in schema:
            for k, item in enumerate(value):
                errors += validate(item, schema["items"], root, f"{path}[{k}]")
                if len(errors) > 50:
                    return errors
    return errors


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def check_dir(doc_dir: Path) -> List[str]:
    """추출 결과 폴더 하나를 검사 → 오류 목록(파일: 경로: 내용)."""
    errors = []
    for file_name, schema_name in FILES.items():
        path = doc_dir / file_name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            errors += [f"{file_name} {e}" for e in validate(data, load_schema(schema_name))]
    chunks = doc_dir / "chunks.jsonl"
    if chunks.exists():
        schema = load_schema("chunk")
        for n, line in enumerate(chunks.read_text(encoding="utf-8").splitlines(), 1):
            errors += [f"chunks.jsonl:{n} {e}" for e in validate(json.loads(line), schema)]
    return errors


if __name__ == "__main__":
    problems = []
    for arg in sys.argv[1:]:
        problems += [f"{arg}: {e}" for e in check_dir(Path(arg))]
    print("\n".join(problems) or "ok")
    sys.exit(1 if problems else 0)
