import os
import yaml
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from db_manager import RealEstateDB

# -------------------------------------------------------------
# 페이지 기본 설정 & 스타일
# -------------------------------------------------------------
st.set_page_config(
    page_title="아파트 실거래가 모니터링 대시보드",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 모던한 CSS 스타일
st.markdown(
    """
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .metric-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        border: 1px solid #e9ecef;
        margin-bottom: 15px;
    }
    .metric-title {
        font-size: 14px;
        color: #6c757d;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 26px;
        font-weight: 700;
        color: #1a1e24;
    }
    .metric-sub {
        font-size: 12px;
        color: #8892b0;
        margin-top: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_korean_currency(amount_manwon: float) -> str:
    """만원 단위 숫자를 'X억 Y,YYY만원' 포맷으로 변환"""
    if pd.isna(amount_manwon) or amount_manwon == 0:
        return "-"
    amount = int(round(amount_manwon))
    eok = amount // 10000
    man = amount % 10000
    if eok > 0 and man > 0:
        return f"{eok}억 {man:,}만원"
    elif eok > 0:
        return f"{eok}억원"
    else:
        return f"{man:,}만원"


@st.cache_data
def load_setting():
    """setting.yml 로드"""
    if os.path.exists("setting.yml"):
        with open("setting.yml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


@st.cache_data(ttl=60)
def load_data():
    """RealEstateDB 클래스를 통한 SQLite 데이터 로드"""
    setting = load_setting()
    db_path = setting.get("storage", {}).get("db_path", "data/transactions.db")
    if not os.path.exists(db_path):
        return None
    try:
        db = RealEstateDB(db_path=db_path)
        return db.get_all_transactions()
    except Exception as e:
        st.error(f"데이터베이스 조회 실패: {e}")
        return None


def main():
    setting = load_setting()
    df = load_data()

    # 상단 헤더
    st.title("🏢 아파트 실거래가 모니터링 대시보드")

    # 설정 정보 표시
    cfg_regions = setting.get("collection", {}).get("regions", [])
    region_names = ", ".join([r.get("name", "") for r in cfg_regions]) if cfg_regions else "설정 지역"
    start_ym = setting.get("collection", {}).get("start_year_month", "202601")
    
    st.caption(f"📍 수집 대상: **{region_names}** | 📅 수집 기준: **{start_ym[:4]}년 {start_ym[4:]}월 ~ 현재** | 💾 저장소: **SQLite DB** | 🔄 자동 수집 연동")

    if df is None or df.empty:
        st.warning(
            "⚠️ 현재 적재된 실거래가 데이터(`data/transactions.db`)가 없습니다.\n\n"
            "터미널에서 `python collector.py`를 실행하거나, GitHub Actions 워크플로우를 실행하여 데이터를 수집해주세요."
        )
        return

    # ---------------------------------------------------------
    # 사이드바 필터
    # ---------------------------------------------------------
    st.sidebar.header("🔍 필터 및 검색 옵션")

    # 1. 지역 필터
    available_regions = df["regionName"].dropna().unique().tolist() if "regionName" in df.columns else []
    if available_regions:
        selected_regions = st.sidebar.multiselect("지역 선택", options=available_regions, default=available_regions)
        filtered_df = df[df["regionName"].isin(selected_regions)]
    else:
        filtered_df = df.copy()

    # 2. 단지명 필터
    available_complexes = sorted(filtered_df["aptNm"].dropna().unique().tolist()) if "aptNm" in filtered_df.columns else []
    selected_complexes = st.sidebar.multiselect(
        "아파트 단지명 (전체 선택 시 비워둠)",
        options=available_complexes,
        default=[],
        help="특정 단지를 선택하여 비교할 수 있습니다. 비워둘 경우 전체 단지를 표시합니다.",
    )
    if selected_complexes:
        filtered_df = filtered_df[filtered_df["aptNm"].isin(selected_complexes)]

    # 3. 면적 타입 필터
    if "areaType" in filtered_df.columns and filtered_df["areaType"].notna().any():
        available_types = filtered_df["areaType"].dropna().unique().tolist()
        selected_types = st.sidebar.multiselect("전용면적 타입", options=available_types, default=available_types)
        if selected_types:
            filtered_df = filtered_df[filtered_df["areaType"].isin(selected_types)]

    # 4. 기간 필터
    if "dealDate" in filtered_df.columns and not filtered_df["dealDate"].dropna().empty:
        min_date = filtered_df["dealDate"].min().date()
        max_date = filtered_df["dealDate"].max().date()
        date_range = st.sidebar.date_input("거래 계약 기간", value=(min_date, max_date), min_value=min_date, max_value=max_date)
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_d, end_d = date_range
            filtered_df = filtered_df[
                (filtered_df["dealDate"].dt.date >= start_d) & (filtered_df["dealDate"].dt.date <= end_d)
            ]

    st.sidebar.markdown("---")
    st.sidebar.caption("💡 Tip: 그래프를 드래그하여 확대하거나 더블클릭하여 원래대로 복원할 수 있습니다.")

    if filtered_df.empty:
        st.info("선택된 필터 조건에 해당하는 실거래 데이터가 없습니다.")
        return

    # ---------------------------------------------------------
    # 핵심 메트릭 카드
    # ---------------------------------------------------------
    total_trades = len(filtered_df)
    avg_price = filtered_df["dealAmount"].mean()
    max_price = filtered_df["dealAmount"].max()
    min_price = filtered_df["dealAmount"].min()

    # 최고가 거래 단지
    max_row = filtered_df.loc[filtered_df["dealAmount"].idxmax()] if total_trades > 0 else None
    max_apt_desc = f"{max_row['aptNm']} ({max_row.get('floor', '-')}층)" if max_row is not None else "-"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">총 거래 건수</div>
                <div class="metric-value">{total_trades:,} <span style="font-size:18px;font-weight:normal;">건</span></div>
                <div class="metric-sub">선택된 기간 기준</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">평균 실거래가</div>
                <div class="metric-value">{format_korean_currency(avg_price)}</div>
                <div class="metric-sub">약 {avg_price:,.0f} 만원</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">최고 거래가</div>
                <div class="metric-value" style="color:#d90429;">{format_korean_currency(max_price)}</div>
                <div class="metric-sub">{max_apt_desc}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">최저 거래가</div>
                <div class="metric-value" style="color:#0077b6;">{format_korean_currency(min_price)}</div>
                <div class="metric-sub">약 {min_price:,.0f} 만원</div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.write("")

    # ---------------------------------------------------------
    # 인터랙티브 차트 (Plotly)
    # ---------------------------------------------------------
    tab1, tab2, tab3 = st.tabs(["📈 시계열 가격 추이", "📊 단지별 비교", "📋 상세 실거래 목록"])

    with tab1:
        st.subheader("계약일자별 실거래가 추이")
        fig_trend = px.scatter(
            filtered_df,
            x="dealDate",
            y="dealAmount",
            color="aptNm",
            size="excluUseAr" if "excluUseAr" in filtered_df.columns else None,
            hover_data={
                "aptNm": True,
                "dealAmount": ":,d",
                "floor": True,
                "excluUseAr": ":.2f",
                "dealDate": "|%Y-%m-%d",
            },
            labels={"dealDate": "계약일", "dealAmount": "거래금액 (만원)", "aptNm": "단지명", "floor": "층"},
            title="단지별 실거래가 분포 및 시간 추이",
            template="plotly_white",
        )
        fig_trend.update_layout(
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(tickformat=","),
            height=500,
        )
        st.plotly_chart(fig_trend, use_container_width=True)

        # 월별 거래량 추이
        if "dealYear" in filtered_df.columns and "dealMonth" in filtered_df.columns:
            filtered_df["dealYM"] = filtered_df["dealYear"].astype(str) + "-" + filtered_df["dealMonth"].astype(str).str.zfill(2)
            monthly_vol = filtered_df.groupby("dealYM").size().reset_index(name="거래건수")
            fig_bar = px.bar(
                monthly_vol,
                x="dealYM",
                y="거래건수",
                title="월별 실거래 건수 추이",
                text="거래건수",
                color_discrete_sequence=["#3a86ff"],
                template="plotly_white",
            )
            fig_bar.update_layout(height=350)
            st.plotly_chart(fig_bar, use_container_width=True)

    with tab2:
        st.subheader("단지별 평균 및 최고 실거래가 비교")
        complex_summary = (
            filtered_df.groupby("aptNm")
            .agg(
                평균거래가=("dealAmount", "mean"),
                최고거래가=("dealAmount", "max"),
                최저거래가=("dealAmount", "min"),
                거래건수=("dealAmount", "count"),
                평균전용면적=("excluUseAr", "mean"),
            )
            .reset_index()
            .sort_values(by="평균거래가", ascending=False)
        )

        fig_comp = go.Figure()
        fig_comp.add_trace(
            go.Bar(
                name="평균 거래가 (만원)",
                x=complex_summary["aptNm"],
                y=complex_summary["평균거래가"],
                marker_color="#4361ee",
            )
        )
        fig_comp.add_trace(
            go.Bar(
                name="최고 거래가 (만원)",
                x=complex_summary["aptNm"],
                y=complex_summary["최고거래가"],
                marker_color="#f72585",
            )
        )
        fig_comp.update_layout(
            barmode="group",
            title="단지별 가격 비교 (평균가 vs 최고가)",
            xaxis_title="단지명",
            yaxis_title="금액 (만원)",
            template="plotly_white",
            height=450,
            yaxis=dict(tickformat=","),
        )
        st.plotly_chart(fig_comp, use_container_width=True)

        # 층수별 가격 분포
        if "floor" in filtered_df.columns:
            st.subheader("층수별 거래금액 분포")
            fig_floor = px.box(
                filtered_df,
                x="aptNm",
                y="dealAmount",
                color="aptNm",
                points="all",
                title="단지별 층수/가격 분포 박스플롯",
                template="plotly_white",
            )
            fig_floor.update_layout(height=400, showlegend=False, yaxis=dict(tickformat=","))
            st.plotly_chart(fig_floor, use_container_width=True)

    with tab3:
        st.subheader("실거래 상세 데이터")
        display_cols = [
            "dealDate",
            "regionName",
            "umdNm",
            "aptNm",
            "dealAmount",
            "excluUseAr",
            "floor",
            "buildYear",
            "dealType",
        ]
        available_display_cols = [c for c in display_cols if c in filtered_df.columns]

        # 한글 컬럼명 매핑
        col_rename_map = {
            "dealDate": "계약일",
            "regionName": "지역",
            "umdNm": "법정동",
            "aptNm": "아파트 단지명",
            "dealAmount": "거래금액(만원)",
            "excluUseAr": "전용면적(㎡)",
            "floor": "층",
            "buildYear": "건축년도",
            "dealType": "거래유형",
        }

        table_df = filtered_df[available_display_cols].copy()
        table_df["거래금액(한글)"] = table_df["dealAmount"].apply(format_korean_currency)
        if "dealDate" in table_df.columns:
            table_df["dealDate"] = table_df["dealDate"].dt.strftime("%Y-%m-%d")

        table_df = table_df.rename(columns=col_rename_map)

        st.dataframe(table_df, use_container_width=True, hide_index=True)

        # CSV 다운로드
        csv_data = filtered_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 필터링된 실거래 데이터 CSV 다운로드",
            data=csv_data,
            file_name="real_estate_transactions_filtered.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
