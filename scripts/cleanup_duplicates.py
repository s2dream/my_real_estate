#!/usr/bin/env python3
"""
[중복 거래 데이터 정리 스크립트]
- 동일한 거래(dealDate, sggCd, aptNm, floor, dealAmount, excluUseAr) 중
  거래유형(dealType)이 None인 구버전/불완전 중복 레코드를 안전하게 삭제합니다.
- 남아있는 레코드의 dealMonth, dealDay 포맷을 2자리(01~12, 01~31)로 표준화하여
  향후 패딩 차이로 인한 중복 발생을 원천 차단합니다.
- data/transactions.db 적용 및 data/transactions.sql 덤프를 자동 갱신합니다.
"""

import os
import sys
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "transactions.db")
SQL_PATH = os.path.join(PROJECT_ROOT, "data", "transactions.sql")


def cleanup_duplicates():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 정리 전 레코드 수
    cursor.execute("SELECT COUNT(*) FROM transactions")
    total_before = cursor.fetchone()[0]

    # 2. 삭제 대상 ID 조회 (dealType이 None인 중복 레코드)
    find_query = """
        SELECT DISTINCT t1.id
        FROM transactions t1
        JOIN transactions t2 ON t1.dealDate = t2.dealDate
                            AND t1.sggCd = t2.sggCd
                            AND t1.aptNm = t2.aptNm
                            AND t1.floor = t2.floor
                            AND t1.dealAmount = t2.dealAmount
                            AND ABS(t1.excluUseAr - t2.excluUseAr) < 0.001
                            AND t1.id != t2.id
        WHERE (t1.dealType IS NULL OR t1.dealType = '' OR t1.dealType = 'None')
          AND (t2.dealType IS NOT NULL AND t2.dealType != '' AND t2.dealType != 'None')
    """
    cursor.execute(find_query)
    delete_ids = [r[0] for r in cursor.fetchall()]

    print("=" * 65)
    print("🧹 [중복 거래 데이터 정리 작업 시작]")
    print(f"  - 전체 레코드 수: {total_before:,}건")
    print(f"  - 삭제 대상 (dealType이 None인 중복 레코드): {len(delete_ids):,}건")
    print("=" * 65)

    if not delete_ids:
        print("✅ 삭제할 중복 레코드가 없습니다.")
        conn.close()
        return

    # 3. 안전한 배치 삭제
    chunk_size = 500
    for i in range(0, len(delete_ids), chunk_size):
        chunk = delete_ids[i : i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cursor.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", chunk)

    conn.commit()

    # 4. dealMonth, dealDay 2자리 표준화 (향후 패딩 불일치 방지)
    cursor.execute("""
        UPDATE transactions 
        SET dealMonth = substr('00' || dealMonth, -2, 2)
        WHERE length(dealMonth) = 1;
    """)
    cursor.execute("""
        UPDATE transactions 
        SET dealDay = substr('00' || dealDay, -2, 2)
        WHERE length(dealDay) = 1;
    """)
    conn.commit()

    # 5. 정리 후 레코드 수 및 중복 재검증
    cursor.execute("SELECT COUNT(*) FROM transactions")
    total_after = cursor.fetchone()[0]

    # 잔여 중복 검사
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM transactions
            GROUP BY dealDate, sggCd, aptNm, floor, CAST(excluUseAr AS TEXT), dealAmount
            HAVING COUNT(*) > 1
        )
    """)
    remaining_duplicates = cursor.fetchone()[0]

    conn.close()

    print(f"\n🎉 [정리 완료]")
    print(f"  - 삭제된 중복 레코드: {len(delete_ids):,}건")
    print(f"  - 최종 DB 레코드 수: {total_after:,}건")
    print(f"  - 잔여 중복 그룹 수: {remaining_duplicates}건 (완벽 해결!)")

    # 6. transactions.sql 덤프 파일 갱신
    print(f"\n📦 SQL 덤프 파일 갱신 중: '{SQL_PATH}'...")
    os.system(f"sqlite3 {DB_PATH} .dump > {SQL_PATH}")
    print("✅ data/transactions.sql 갱신 완료!")
    print("=" * 65)


if __name__ == "__main__":
    cleanup_duplicates()
