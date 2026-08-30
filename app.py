import os
import yaml
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from db_manager import RealEstateDB

# -------------------------------------------------------------
# 페이지 기본 설정 & 스타일
# -------------------------------------------------------------
st.set_page_config(
    page_title="스마트 아파트 실거래가 분석 대시보드",
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
        font-size: 13px;
        color: #6c757d;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 24px;
        font-weight: 700;
        color: #1a1e24;
    }
    .metric-sub {
        font-size: 12px;
        color: #8892b0;
        margin-top: 4px;
    }
    .badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        background-color: #e3f2fd;
        color: #1976d2;
        margin-right: 6px;
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
    """RealEstateDB 클래스를 통한 SQLite 데이터 로드 및 파생변수 생성"""
    setting = load_setting()
    db_path = setting.get("storage", {}).get("db_path", "data/transactions.db")
    if not os.path.exists(db_path):
        return None
    try:
        db = RealEstateDB(db_path=db_path)
        df = db.get_all_transactions()
        if df.empty:
            return df

        # 파생변수: 평당 가격 (3.3㎡당 만원)
        if "dealAmount" in df.columns and "excluUseAr" in df.columns:
            pyeong = df["excluUseAr"] / 3.305785
            df["pyeongPrice"] = (df["dealAmount"] / pyeong).round(1)

        # 파생변수: 취소 여부 불리언
        if "cdealType" in df.columns:
            df["isCanceled"] = df["cdealType"].isin(["O", "0", "취소", "해제"])
        else:
            df["isCanceled"] = False

        return df
    except Exception as e:
        st.error(f"데이터베이스 조회 실패: {e}")
        return None


def main():
    setting = load_setting()
    df = load_data()

    # 상단 대시보드 헤더
    st.title("🏢 스마트 아파트 실거래가 심층 분석 대시보드")

    if df is None or df.empty:
        st.warning(
            "⚠️ 현재 적재된 실거래가 데이터(`data/transactions.db`)가 없습니다.\n\n"
            "터미널에서 `python collector.py`를 실행하거나, GitHub Actions 워크플로우를 실행하여 데이터를 수집해주세요."
        )
        return

    # 동적 메타데이터 계산
    db_regions = sorted(df["regionName"].dropna().unique().tolist())
    region_str = ", ".join(db_regions) if db_regions else "전체 지역"
    
    start_ym = setting.get("collection", {}).get("start_year_month", "202601")
    build_cfg = setting.get("collection", {}).get("build_year_filter", {})
    build_str = f"준공 {build_cfg.get('within_years', 10)}년 이내" if build_cfg.get("enabled", False) else "전체 준공연도"
    area_cfg = setting.get("collection", {}).get("area_filter", {})
    area_str = "84타입 전용" if area_cfg.get("enabled", False) else "전체 면적"

    st.markdown(
        f"""
        <div>
            <span class="badge">📍 대상 지역: {region_str}</span>
            <span class="badge">📐 면적: {area_str}</span>
            <span class="badge">🏗️ 건축: {build_str}</span>
            <span class="badge">📅 기준: {start_ym[:4]}년 {start_ym[4:]}월 ~ 현재</span>
            <span class="badge">💾 SQLite DB 연동</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")

    # ---------------------------------------------------------
    # 사이드바 필터 (연쇄 필터링 적용)
    # ---------------------------------------------------------
    st.sidebar.header("🔍 필터 및 분석 옵션")

    # 1. 지역 필터
    selected_regions = st.sidebar.multiselect("지역 선택", options=db_regions, default=db_regions)
    filtered_df = df[df["regionName"].isin(selected_regions)]

    # 2. 연식 기준 필터 (슬라이더, default 5년)
    st.sidebar.markdown("##### 🏗️ 연식 기준 (준공연차 필터)")
    current_year = 2026 # 한국시간 기준 현재연도
    max_age = st.sidebar.slider("연식 기준 (최근 N년 이내 준공)", min_value=0, max_value=30, value=5, step=1)
    st.sidebar.caption(f"ℹ️ {current_year - max_age}년 이후 준공된 아파트 ({max_age}년 이내)")

    if "buildYear" in filtered_df.columns:
        build_numeric = pd.to_numeric(filtered_df["buildYear"], errors="coerce")
        min_allowed_build_year = current_year - max_age
        filtered_df = filtered_df[build_numeric >= min_allowed_build_year]

    # 3. 단지명 필터 (선택된 지역 + 연식 기준에 해당하는 단지만 후보로 노출)
    available_complexes = sorted(filtered_df["aptNm"].dropna().unique().tolist())
    target_defaults = ["매교역푸르지오SKVIEW", "수원센트럴아이파크자이"]
    default_selected_complexes = [apt for apt in target_defaults if apt in available_complexes]

    selected_complexes = st.sidebar.multiselect(
        "아파트 단지명",
        options=available_complexes,
        default=default_selected_complexes,
        help="선택한 단지만 선별하여 조회합니다. 모두 선택을 해제(비움)하면 해당 연식의 전체 단지를 표시합니다.",
    )
    if selected_complexes:
        filtered_df = filtered_df[filtered_df["aptNm"].isin(selected_complexes)]

    # 4. 면적 타입 필터
    if "areaType" in filtered_df.columns and filtered_df["areaType"].notna().any():
        available_types = sorted(filtered_df["areaType"].dropna().unique().tolist())
        selected_types = st.sidebar.multiselect("전용면적 타입", options=available_types, default=available_types)
        if selected_types:
            filtered_df = filtered_df[filtered_df["areaType"].isin(selected_types)]

    # 5. 기간 필터
    if "dealDate" in filtered_df.columns and not filtered_df["dealDate"].dropna().empty:
        min_date = filtered_df["dealDate"].min().date()
        max_date = filtered_df["dealDate"].max().date()
        if min_date != max_date:
            date_range = st.sidebar.date_input("거래 계약 기간", value=(min_date, max_date), min_value=min_date, max_value=max_date)
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
                filtered_df = filtered_df[
                    (filtered_df["dealDate"].dt.date >= start_d) & (filtered_df["dealDate"].dt.date <= end_d)
                ]

    # 6. 거래 취소/해제 건 필터
    include_canceled = st.sidebar.checkbox("거래 취소/해제 건 포함", value=False, help="계약 후 해제/취소된 거래를 포함하여 조회합니다.")
    if not include_canceled:
        filtered_df = filtered_df[~filtered_df["isCanceled"]]

    st.sidebar.markdown("---")
    st.sidebar.caption("💡 팁: 그래프 범례를 클릭하여 특정 단지만 보거나 숨길 수 있습니다.")

    if filtered_df.empty:
        st.info("선택된 필터 조건에 해당하는 실거래 데이터가 없습니다.")
        return

    # ---------------------------------------------------------
    # 핵심 통계 지표 카드 (KPI Metrics)
    # ---------------------------------------------------------
    total_trades = len(filtered_df)
    avg_price = filtered_df["dealAmount"].mean()
    median_price = filtered_df["dealAmount"].median()
    max_price = filtered_df["dealAmount"].max()
    min_price = filtered_df["dealAmount"].min()
    avg_pyeong_price = filtered_df["pyeongPrice"].mean() if "pyeongPrice" in filtered_df.columns else 0

    # 최고가 거래 단지
    max_row = filtered_df.loc[filtered_df["dealAmount"].idxmax()] if total_trades > 0 else None
    max_apt_desc = f"{max_row['aptNm']} ({max_row.get('floor', '-')}층, {max_row.get('dealDate').strftime('%m/%d') if pd.notna(max_row.get('dealDate')) else ''})" if max_row is not None else "-"

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">총 거래 건수</div>
                <div class="metric-value">{total_trades:,} <span style="font-size:16px;font-weight:normal;">건</span></div>
                <div class="metric-sub">{len(filtered_df['aptNm'].unique())}개 단지</div>
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
                <div class="metric-title">중위 실거래가</div>
                <div class="metric-value">{format_korean_currency(median_price)}</div>
                <div class="metric-sub">약 {median_price:,.0f} 만원</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">평균 평당가 (3.3㎡)</div>
                <div class="metric-value">{avg_pyeong_price:,.0f} <span style="font-size:16px;font-weight:normal;">만원</span></div>
                <div class="metric-sub">전용면적 기준</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col5:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">최고 거래가</div>
                <div class="metric-value" style="color:#d90429;">{format_korean_currency(max_price)}</div>
                <div class="metric-sub">{max_apt_desc}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.write("")

    # ---------------------------------------------------------
    # 인터랙티브 심층 분석 탭
    # ---------------------------------------------------------
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📈 시계열 가격 추이 & 단지별 추세선",
        "🏢 단지별 랭킹 & 평당가 비교",
        "층수 & 건축년도별 분석",
        "📊 거래량 & 가격 분포 통계",
        "📋 실거래 상세 목록",
    ])

    # ---------------------------------------------------------
    # TAB 1: 시계열 산점도 + 단지별 이동평균 추세선 & 중심축
    # ---------------------------------------------------------
    with tab1:
        st.subheader("계약일자별 실거래가 및 단지별 이동평균선")

        c1, c2 = st.columns([1, 1])
        with c1:
            show_ma = st.checkbox("단지별 7일 이동평균 추세선 표시", value=True)
        with c2:
            show_mean_line = st.checkbox(f"전체 평균 가격 중심축 ({format_korean_currency(avg_price)}) 표시", value=True)

        fig_trend = go.Figure()

        unique_apts = filtered_df["aptNm"].unique()
        color_palette = px.colors.qualitative.Plotly

        for idx, apt in enumerate(unique_apts):
            apt_df = filtered_df[filtered_df["aptNm"] == apt].sort_values("dealDate")
            color = color_palette[idx % len(color_palette)]

            # 1. 단지별 실거래 산점도
            hover_text = [
                f"<b>{row['aptNm']}</b> ({row['regionName']})<br>"
                f"거래일: {row['dealDate'].strftime('%Y-%m-%d') if pd.notna(row['dealDate']) else ''}<br>"
                f"거래금액: <b>{format_korean_currency(row['dealAmount'])}</b> ({row['dealAmount']:,}만원)<br>"
                f"평당가: {row.get('pyeongPrice', 0):,.0f}만원/평<br>"
                f"층수: {row.get('floor', '-')}층 | 전용: {row.get('excluUseAr', '-')}㎡<br>"
                f"건축년도: {row.get('buildYear', '-')}년"
                + (f"<br><span style='color:red;'>⚠️ 해제/취소: {row.get('cdealDay', '')}</span>" if row.get('isCanceled') else "")
                for _, row in apt_df.iterrows()
            ]

            fig_trend.add_trace(
                go.Scatter(
                    x=apt_df["dealDate"],
                    y=apt_df["dealAmount"],
                    mode="markers",
                    name=f"{apt} (실거래)",
                    marker=dict(
                        size=9,
                        color=color,
                        opacity=0.7,
                        line=dict(width=1, color="white"),
                    ),
                    text=hover_text,
                    hoverinfo="text",
                )
            )

            # 2. 단지별 7일 롤링 이동평균선
            if show_ma and len(apt_df) >= 2:
                # 일별 평균 가격 계산 후 롤링
                apt_daily = apt_df.groupby("dealDate")["dealAmount"].mean().reset_index().sort_values("dealDate")
                apt_daily["MA7"] = apt_daily["dealAmount"].rolling(window=7, min_periods=1).mean()

                fig_trend.add_trace(
                    go.Scatter(
                        x=apt_daily["dealDate"],
                        y=apt_daily["MA7"],
                        mode="lines",
                        name=f"{apt} (7일 이동평균)",
                        line=dict(color=color, width=2.5),
                        hoverinfo="skip",
                    )
                )

        # 3. 전체 평균 가격 중심축 (수평 기준선)
        if show_mean_line:
            fig_trend.add_hline(
                y=avg_price,
                line_dash="dash",
                line_color="#1d3557",
                line_width=2,
                annotation_text=f"전체 평균가: {format_korean_currency(avg_price)} ({avg_price:,.0f}만원)",
                annotation_position="top right",
                annotation_font=dict(size=12, color="#1d3557", weight="bold"),
            )

        fig_trend.update_layout(
            title="단지별 실거래가 분포 및 단지별 이동평균 추세선",
            xaxis_title="계약 체결일",
            yaxis_title="거래금액 (만원)",
            yaxis=dict(tickformat=","),
            template="plotly_white",
            height=540,
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_trend, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 2: 단지별 랭킹 및 평당가 비교
    # ---------------------------------------------------------
    with tab2:
        st.subheader("아파트 단지별 거래가 및 평당가 비교 랭킹")

        complex_agg = (
            filtered_df.groupby(["regionName", "aptNm"])
            .agg(
                평균거래가=("dealAmount", "mean"),
                중위거래가=("dealAmount", "median"),
                최고거래가=("dealAmount", "max"),
                최저거래가=("dealAmount", "min"),
                평균평당가=("pyeongPrice", "mean"),
                거래건수=("dealAmount", "count"),
                평균건축년도=("buildYear", lambda x: pd.to_numeric(x, errors="coerce").mean()),
            )
            .reset_index()
            .sort_values(by="평균거래가", ascending=False)
        )

        c_sort = st.radio("정렬 기준", ["평균 실거래가 높은 순", "평당가(3.3㎡) 높은 순", "최고 거래가 높은 순", "거래량 많은 순"], horizontal=True)
        if c_sort == "평균 실거래가 높은 순":
            complex_agg = complex_agg.sort_values(by="평균거래가", ascending=False)
        elif c_sort == "평당가(3.3㎡) 높은 순":
            complex_agg = complex_agg.sort_values(by="평균평당가", ascending=False)
        elif c_sort == "최고 거래가 높은 순":
            complex_agg = complex_agg.sort_values(by="최고거래가", ascending=False)
        else:
            complex_agg = complex_agg.sort_values(by="거래건수", ascending=False)

        # 단지별 바 차트 (상위 15개 단지)
        top_complexes = complex_agg.head(15)

        fig_bar_comp = go.Figure()
        fig_bar_comp.add_trace(
            go.Bar(
                name="평균 거래가 (만원)",
                x=top_complexes["aptNm"],
                y=top_complexes["평균거래가"],
                marker_color="#3a86ff",
                text=top_complexes["평균거래가"].apply(lambda x: f"{x:,.0f}만"),
                textposition="auto",
            )
        )
        fig_bar_comp.add_trace(
            go.Bar(
                name="최고 거래가 (만원)",
                x=top_complexes["aptNm"],
                y=top_complexes["최고거래가"],
                marker_color="#ff006e",
                text=top_complexes["최고거래가"].apply(lambda x: f"{x:,.0f}만"),
                textposition="auto",
            )
        )
        fig_bar_comp.update_layout(
            barmode="group",
            title="주요 단지별 평균가 vs 최고가 비교 (Top 15)",
            xaxis_title="단지명",
            yaxis_title="금액 (만원)",
            template="plotly_white",
            height=450,
            yaxis=dict(tickformat=","),
        )
        st.plotly_chart(fig_bar_comp, use_container_width=True)

        # 평당가 차트
        fig_pyeong = px.bar(
            top_complexes,
            x="aptNm",
            y="평균평당가",
            color="regionName",
            title="단지별 3.3㎡(평)당 평균 가격 비교",
            labels={"aptNm": "단지명", "평균평당가": "평당가 (만원/평)", "regionName": "지역"},
            text_auto=",.0f",
            template="plotly_white",
        )
        fig_pyeong.update_layout(height=400)
        st.plotly_chart(fig_pyeong, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 3: 층수 & 건축년도별 프리미엄 분석
    # ---------------------------------------------------------
    with tab3:
        st.subheader("층수 및 준공연도에 따른 가격 분포 및 프리미엄")

        col_a, col_b = st.columns(2)

        with col_a:
            if "floor" in filtered_df.columns:
                # 층수별 산점도 & 추세선
                fig_floor_scatter = px.scatter(
                    filtered_df,
                    x="floor",
                    y="dealAmount",
                    color="regionName",
                    hover_data=["aptNm", "dealDate"],
                    trendline="ols",
                    title="층수(Floor) vs 실거래가 상관관계 (저층/로열층 추세선)",
                    labels={"floor": "층수", "dealAmount": "거래금액 (만원)", "regionName": "지역"},
                    template="plotly_white",
                )
                fig_floor_scatter.update_layout(height=420, yaxis=dict(tickformat=","))
                st.plotly_chart(fig_floor_scatter, use_container_width=True)

        with col_b:
            if "buildYear" in filtered_df.columns:
                # 건축년도별 평균 가격
                build_summary = (
                    filtered_df.groupby("buildYear")["dealAmount"]
                    .agg(["mean", "count"])
                    .reset_index()
                    .sort_values("buildYear")
                )
                fig_build = px.bar(
                    build_summary,
                    x="buildYear",
                    y="mean",
                    text_auto=",.0f",
                    title="건축년도(준공연도)별 평균 실거래가",
                    labels={"buildYear": "준공연도", "mean": "평균 거래가 (만원)"},
                    template="plotly_white",
                    color_discrete_sequence=["#8338ec"],
                )
                fig_build.update_layout(height=420, yaxis=dict(tickformat=","))
                st.plotly_chart(fig_build, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 4: 거래량 & 가격 분포 통계
    # ---------------------------------------------------------
    with tab4:
        st.subheader("월별 거래량 추이 및 가격대 분포 통계")

        col_m1, col_m2 = st.columns([1, 1])

        with col_m1:
            # 월별/지역별 거래량
            if "dealYear" in filtered_df.columns and "dealMonth" in filtered_df.columns:
                filtered_df["dealYM"] = filtered_df["dealYear"].astype(str) + "-" + filtered_df["dealMonth"].astype(str).str.zfill(2)
                vol_by_region = filtered_df.groupby(["dealYM", "regionName"]).size().reset_index(name="거래건수")
                fig_monthly = px.bar(
                    vol_by_region,
                    x="dealYM",
                    y="거래건수",
                    color="regionName",
                    barmode="stack",
                    title="월별/지역별 실거래 건수 추이",
                    labels={"dealYM": "계약년월", "거래건수": "거래건수", "regionName": "지역"},
                    text_auto=True,
                    template="plotly_white",
                )
                fig_monthly.update_layout(height=420)
                st.plotly_chart(fig_monthly, use_container_width=True)

        with col_m2:
            # 실거래가 히스토그램 (분포도)
            fig_hist = px.histogram(
                filtered_df,
                x="dealAmount",
                color="regionName",
                nbins=25,
                title="실거래 가격대 분포 히스토그램",
                labels={"dealAmount": "거래금액 (만원)", "regionName": "지역"},
                template="plotly_white",
                marginal="box",
            )
            fig_hist.update_layout(height=420, yaxis=dict(tickformat=","), xaxis=dict(tickformat=","))
            st.plotly_chart(fig_hist, use_container_width=True)

        # 통계 요약표
        st.markdown("#### 📐 주요 가격 통계 요약 (사분위수 & 변동성)")
        stat_summary = pd.DataFrame({
            "지표": ["최소 거래가 (Min)", "하위 25% (Q1)", "중위 거래가 (Median / Q2)", "평균 거래가 (Mean)", "상위 75% (Q3)", "최고 거래가 (Max)", "표준편차 (Std)"],
            "거래금액 (만원)": [
                f"{filtered_df['dealAmount'].min():,}",
                f"{filtered_df['dealAmount'].quantile(0.25):,.0f}",
                f"{filtered_df['dealAmount'].median():,.0f}",
                f"{filtered_df['dealAmount'].mean():,.0f}",
                f"{filtered_df['dealAmount'].quantile(0.75):,.0f}",
                f"{filtered_df['dealAmount'].max():,}",
                f"{filtered_df['dealAmount'].std():,.0f}",
            ],
            "한글 금액": [
                format_korean_currency(filtered_df['dealAmount'].min()),
                format_korean_currency(filtered_df['dealAmount'].quantile(0.25)),
                format_korean_currency(filtered_df['dealAmount'].median()),
                format_korean_currency(filtered_df['dealAmount'].mean()),
                format_korean_currency(filtered_df['dealAmount'].quantile(0.75)),
                format_korean_currency(filtered_df['dealAmount'].max()),
                "-",
            ]
        })
        st.dataframe(stat_summary, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # TAB 5: 실거래 상세 목록 & CSV 다운로드
    # ---------------------------------------------------------
    with tab5:
        st.subheader("실거래 상세 내역 및 데이터 다운로드")

        display_cols = [
            "dealDate",
            "regionName",
            "umdNm",
            "aptNm",
            "dealAmount",
            "pyeongPrice",
            "excluUseAr",
            "floor",
            "buildYear",
            "dealType",
            "cdealType",
            "cdealDay",
        ]
        available_display_cols = [c for c in display_cols if c in filtered_df.columns]

        col_rename_map = {
            "dealDate": "계약일",
            "regionName": "지역",
            "umdNm": "법정동",
            "aptNm": "아파트 단지명",
            "dealAmount": "거래금액(만원)",
            "pyeongPrice": "평당가(만원/평)",
            "excluUseAr": "전용면적(㎡)",
            "floor": "층",
            "buildYear": "건축년도",
            "dealType": "거래유형",
            "cdealType": "해제여부",
            "cdealDay": "해제일자",
        }

        table_df = filtered_df[available_display_cols].copy()
        table_df["거래금액(한글)"] = table_df["dealAmount"].apply(format_korean_currency)
        if "dealDate" in table_df.columns:
            table_df["dealDate"] = table_df["dealDate"].dt.strftime("%Y-%m-%d")

        # 컬럼 순서 재배치 (한글 금액을 거래금액 옆으로)
        if "거래금액(한글)" in table_df.columns:
            cols = list(table_df.columns)
            cols.remove("거래금액(한글)")
            da_idx = cols.index("dealAmount") if "dealAmount" in cols else 0
            cols.insert(da_idx + 1, "거래금액(한글)")
            table_df = table_df[cols]

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
