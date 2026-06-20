"""
init_db.py
───────────
로컬 DB 초기화 스크립트.
실행: python init_db.py
"""
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "db/kbeauty.db")

os.makedirs("db", exist_ok=True)

# 기존 DB 삭제 후 새 스키마로 재생성
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print(f"기존 DB 삭제: {DB_PATH}")

with open("db/schema.sql", encoding="utf-8") as f:
    schema = f.read()

conn = sqlite3.connect(DB_PATH)
conn.executescript(schema)
conn.commit()

tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()]
brands = conn.execute("SELECT COUNT(*) FROM dim_brand").fetchone()[0]
conn.close()

print(f"✅ DB 생성 완료: {DB_PATH}")
print(f"   테이블 {len(tables)}개: {tables}")
print(f"   AP 브랜드 {brands}개 등록됨")
