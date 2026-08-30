import os
import sqlite3
from typing import Optional, Tuple
import pandas as pd


class RealEstateDB:
    """
    아파트 실거래가 SQLite 데이터베이스를 전담 관리하는 클래스.
    - 테이블 및 복합 UNIQUE 인덱스 자동 초기화
    - INSERT OR REPLACE 기반 멱등성 보장 UPSERT 적재
    - 최적화된 SQL 쿼리 및 통계 조회
    """

    DEFAULT_DB_PATH = "data/transactions.db"
    TABLE_NAME = "transactions"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DEFAULT_DB_PATH
        # DB 파일이 위치할 상위 폴더 생성
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """새로운 SQLite 연결 객체를 반환합니다."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """테이블 스키마 및 복합 유니크 인덱스를 초기화합니다."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. transactions 테이블 생성
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dealDate TEXT,
                    dealYear TEXT,
                    dealMonth TEXT,
                    dealDay TEXT,
                    sggCd TEXT,
                    regionName TEXT,
                    umdNm TEXT,
                    jibun TEXT,
                    aptNm TEXT,
                    floor INTEGER,
                    excluUseAr REAL,
                    areaType TEXT,
                    dealAmount INTEGER,
                    buildYear TEXT,
                    dealType TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 2. 복합 UNIQUE 인덱스 생성 (중복 거래 완벽 방지)
            cursor.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_unique
                ON {self.TABLE_NAME} (
                    dealYear, dealMonth, dealDay, sggCd, umdNm, jibun, aptNm, floor, excluUseAr, dealAmount
                );
                """
            )

            # 3. 조회 성능 향상을 위한 단일 인덱스 생성
            cursor.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_transactions_date_apt
                ON {self.TABLE_NAME} (dealDate, aptNm);
                """
            )
            conn.commit()

    def upsert_transactions(self, df: pd.DataFrame) -> Tuple[int, int]:
        """
        데이터프레임의 실거래가 레코드를 DB에 UPSERT(INSERT OR REPLACE)합니다.
        반환: (최종 전체 레코드 수, 이번에 반영된 레코드 수)
        """
        if df.empty:
            return self.get_count(), 0

        # 필수 컬럼 목록 정의
        columns = [
            "dealDate",
            "dealYear",
            "dealMonth",
            "dealDay",
            "sggCd",
            "regionName",
            "umdNm",
            "jibun",
            "aptNm",
            "floor",
            "excluUseAr",
            "areaType",
            "dealAmount",
            "buildYear",
            "dealType",
        ]

        # 데이터프레임에 없는 컬럼은 None으로 보완
        save_df = df.copy()
        for col in columns:
            if col not in save_df.columns:
                save_df[col] = None

        # 날짜 컬럼을 문자열(YYYY-MM-DD)로 변환
        if "dealDate" in save_df.columns and pd.api.types.is_datetime64_any_dtype(save_df["dealDate"]):
            save_df["dealDate"] = save_df["dealDate"].dt.strftime("%Y-%m-%d")

        # NaN 값을 None(NULL)으로 변환
        records = save_df[columns].to_dict(orient="records")

        placeholders = ", ".join(["?" for _ in columns])
        col_names = ", ".join(columns)
        insert_sql = f"""
            INSERT OR REPLACE INTO {self.TABLE_NAME} ({col_names}, updated_at)
            VALUES ({placeholders}, CURRENT_TIMESTAMP)
        """

        data_tuples = [tuple(r[col] for col in columns) for r in records]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(insert_sql, data_tuples)
            conn.commit()

        total_cnt = self.get_count()
        return total_cnt, len(data_tuples)

    def get_all_transactions(self) -> pd.DataFrame:
        """전체 실거래가 데이터를 최신순으로 정렬하여 DataFrame으로 반환합니다."""
        with self._get_connection() as conn:
            query = f"""
                SELECT 
                    dealDate,
                    dealYear,
                    dealMonth,
                    dealDay,
                    sggCd,
                    regionName,
                    umdNm,
                    jibun,
                    aptNm,
                    floor,
                    excluUseAr,
                    areaType,
                    dealAmount,
                    buildYear,
                    dealType
                FROM {self.TABLE_NAME}
                ORDER BY dealDate DESC, dealAmount DESC
            """
            df = pd.read_sql_query(query, conn)

        if "dealDate" in df.columns:
            df["dealDate"] = pd.to_datetime(df["dealDate"], errors="coerce")

        return df

    def get_count(self) -> int:
        """현재 DB에 저장된 총 거래 건수를 반환합니다."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME}")
            row = cursor.fetchone()
            return row[0] if row else 0

    def has_region_data(self, sgg_cd: str) -> bool:
        """특정 지역 코드(sggCd)의 데이터가 DB에 존재하는지 확인합니다."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM {self.TABLE_NAME} WHERE sggCd = ? LIMIT 1", (str(sgg_cd),))
            return cursor.fetchone() is not None

    def get_region_count(self, sgg_cd: str) -> int:
        """특정 지역 코드(sggCd)의 거래 건수를 반환합니다."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.TABLE_NAME} WHERE sggCd = ?", (str(sgg_cd),))
            row = cursor.fetchone()
            return row[0] if row else 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
