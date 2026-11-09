"""Artifact Tool PSI 예시 파일 생성용으로 현재 데모 프로젝트 PSI를 JSON으로 출력합니다."""

import json
from pathlib import Path

from app.database import SessionLocal
from app.reporting import build_psi
from app.seed import load_orders


with SessionLocal() as session:
    output = Path(__file__).resolve().parents[1] / ".local" / "psi-snapshot.json"
    output.write_text(json.dumps(build_psi(load_orders(session, "project-demo")), ensure_ascii=False), encoding="utf-8")
    print(output)
