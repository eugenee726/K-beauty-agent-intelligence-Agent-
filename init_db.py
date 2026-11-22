"""
init_db.py
───────────
로컬 DB 초기화 스크립트. 기존 DB를 지우고 db/schema.sql로 새로 만든다.
실행: python init_db.py

주의: 데이터가 모두 지워진다. 다른 모듈에서 import해도 아무 일도 일어나지 않도록
실행 코드는 init_db() 안에 두고, 직접 실행할 때만 호출한다.
"""
import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "db/kbeauty.db")


def init_db(db_path: str = DB_PATH) -> None:
    """기존 DB 삭제 후 새 스키마로 재생성."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"기존 DB 삭제: {db_path}")

    with open("db/schema.sql", encoding="utf-8") as f:
        schema = f.read()

    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    brands = conn.execute("SELECT COUNT(*) FROM dim_brand").fetchone()[0]
    conn.close()

    print(f"✅ DB 생성 완료: {db_path}")
    print(f"   테이블 {len(tables)}개: {tables}")
    print(f"   브랜드 {brands}개 등록됨")


if __name__ == "__main__":
    init_db()
