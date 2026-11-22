"""
_product_utils.py
──────────────────
dim_product 서로게이트 키(product_id) 발급.

채널별 제품은 각자의 키(oy_prdtno, sephora_pid)를 UNIQUE로 두고 별도 row로 관리한다.
채널 간 같은 제품 연결은 하나의 ID로 합치지 않고, 확인된 것만 별도 연결표로 둘 계획이다
(이름 기반 퍼지 매칭은 부분 일치 오매칭이 확인되어 사용하지 않음).

사용처: oy_global_tool.py, sephora_tool.py
"""

import sqlite3


def next_product_id(conn: sqlite3.Connection) -> str:
    """
    dim_product에서 현재 MAX product_id를 읽어 다음 서로게이트 키 반환.
    형식: 'S00000001', 'S00000002', ...
    """
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(product_id,2) AS INTEGER)) FROM dim_product "
        "WHERE product_id LIKE 'S%'"
    ).fetchone()
    last_num = row[0] or 0
    return f"S{last_num + 1:08d}"
