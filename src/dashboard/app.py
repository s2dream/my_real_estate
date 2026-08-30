import os
import yaml
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats
from src.db.db_manager import RealEstateDB

# -------------------------------------------------------------
# 페이지 기본 설정 & 스타일
# -------------------------------------------------------------
st.set_page_config(
    page_title="스마트 아파트 실거래가 심층 분석 대시보드",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 다크모드 및 모바일/데스크탑 반응형 완벽 호환 모던 CSS 스타일
st.markdown(
    """
    <style>
    /* 전체 레이아웃 너비 극대화 및 반응형 패딩 */
    .main .block-container {
        max-width: 98% !important;
        padding-top: 1.2rem !important;
        padding-bottom: 2.5rem !important;
        padding-left: 1.8rem !important;
        padding-right: 1.8rem !important;
    }

    /* 메트릭 카드 다크/라이트 모드 자동 적응 및 호버 효과 */
    .metric-card {
        background-color: var(--secondary-background-color, #ffffff);
        color: var(--text-color, #1a1e24);
        border-radius: 12px;
        padding: 16px 18px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.06);
        border: 1px solid rgba(128, 128, 128, 0.2);
        margin-bottom: 12px;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0,0,0,0.1);
    }
    .metric-title {
        font-size: 13px;
        color: var(--text-color, #6c757d);
        opacity: 0.85;
        font-weight: 600;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 22px;
        font-weight: 700;
        color: var(--text-color, #1a1e24);
    }
    .metric-sub {
        font-size: 12px;
        color: var(--text-color, #8892b0);
        opacity: 0.8;
        margin-top: 4px;
        line-height: 1.4;
    }
    .badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        background-color: rgba(59, 130, 246, 0.15);
        color: var(--text-color, #1d4ed8);
        border: 1px solid rgba(59, 130, 246, 0.3);
        margin-right: 6px;
        margin-bottom: 6px;
    }
    .insight-box {
        background-color: rgba(58, 134, 255, 0.12);
        border-left: 4px solid #3a86ff;
        padding: 12px 16px;
        border-radius: 4px;
        margin-bottom: 15px;
        font-size: 14px;
        color: var(--text-color, #1e3a8a);
        border-top: 1px solid rgba(58, 134, 255, 0.2);
        border-right: 1px solid rgba(58, 134, 255, 0.2);
        border-bottom: 1px solid rgba(58, 134, 255, 0.2);
    }
    .result-summary-box {
        background-color: var(--secondary-background-color, #f8fafc);
        color: var(--text-color, #1e293b);
        border: 1px solid rgba(128, 128, 128, 0.25);
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 14px;
        font-size: 14px;
    }

    /* 📱 모바일 디바이스 전용 미디어 쿼리 (768px 이하) */
    @media (max-width: 768px) {
        .main .block-container {
            max-width: 100% !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
            padding-top: 0.8rem !important;
            padding-bottom: 1.5rem !important;
        }
        .metric-card {
            padding: 12px 14px;
            margin-bottom: 10px;
        }
        .metric-value {
            font-size: 19px;
        }
        .metric-title {
            font-size: 12px;
        }
        .metric-sub {
            font-size: 11px;
        }
        /* 탭 가로 스크롤 및 폰트 최적화 */
        .stTabs [data-baseweb="tab-list"] {
            gap: 4px;
        }
        .stTabs [data-baseweb="tab"] {
            padding-left: 8px !important;
            padding-right: 8px !important;
            font-size: 13px !important;
        }
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

        # 파생변수: 거래년월 (dealYM: YYYY-MM)
        if "dealYear" in df.columns and "dealMonth" in df.columns:
            df["dealYM"] = df["dealYear"].astype(str) + "-" + df["dealMonth"].astype(str).str.zfill(2)

        # 파생변수: 취소 여부 불리언
        if "cdealType" in df.columns:
            df["isCanceled"] = df["cdealType"].isin(["O", "0", "취소", "해제"])
        else:
            df["isCanceled"] = False

        # 파생변수: 층수 그룹 (저층: 1~5, 중층: 6~15, 고층: 16+)
        if "floor" in df.columns:
            def categorize_floor(fl):
                if pd.isna(fl):
                    return "미분류"
                fl = int(fl)
                if fl <= 5:
                    return "1) 저층 (1~5층)"
                elif fl <= 15:
                    return "2) 중층 (6~15층)"
                else:
                    return "3) 고층/로열 (16층+)"
            df["floorGroup"] = df["floor"].apply(categorize_floor)

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

    # DB에 존재하는 지역 목록 추출
    db_regions = sorted(df["regionName"].dropna().unique().tolist()) if "regionName" in df.columns else []

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

    # ---------------------------------------------------------
    # 상단 대시보드 실시간 선택 필터 뱃지
    # ---------------------------------------------------------
    # 실시간 선택된 지역 및 필터 텍스트
    current_selected_region_str = ", ".join(selected_regions) if selected_regions else "지역 미선택"
    start_ym = setting.get("collection", {}).get("start_year_month", "202601")
    area_cfg = setting.get("collection", {}).get("area_filter", {})
    area_str = "84타입 전용" if area_cfg.get("enabled", False) else "전체 면적"
    selected_complex_str = f"{len(selected_complexes)}개 단지 선택" if selected_complexes else f"전체 {len(available_complexes)}개 단지"

    st.markdown(
        f"""
        <div style="margin-bottom: 15px;">
            <span class="badge" style="background-color: #2563eb; color: white;">📍 선택 지역: {current_selected_region_str}</span>
            <span class="badge">🏢 단지: {selected_complex_str}</span>
            <span class="badge">🏗️ 연식: 최근 {max_age}년 이내 ({current_year - max_age}년~)</span>
            <span class="badge">📐 {area_str}</span>
            <span class="badge">📅 {start_ym[:4]}년 {start_ym[4:]}월 ~ 현재</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
                <div class="metric-sub">계약 기준 평균</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">중위 실거래가</div>
                <div class="metric-value">{format_korean_currency(median_price)}</div>
                <div class="metric-sub">중앙값 (50% 지점)</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">평균 평당가 (3.3㎡)</div>
                <div class="metric-value">{int(round(avg_pyeong_price)):,} <span style="font-size:16px;font-weight:normal;">만원</span></div>
                <div class="metric-sub">전용면적 기준</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col5:
        st.markdown(
            f"""<div class="metric-card">
                <div class="metric-title">최고 거래가</div>
                <div class="metric-value" style="color:#ef4444;">{format_korean_currency(max_price)}</div>
                <div class="metric-sub">{max_apt_desc}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.write("")

    # ---------------------------------------------------------
    # 단지별 전역 일관 색상 매핑 (Global Consistent Color Mapping)
    # 필터 변경 시에도 각 단지의 고유 색상이 모든 차트에서 일관되게 유지됨
    # ---------------------------------------------------------
    COLOR_PALETTE = (
        px.colors.qualitative.Plotly
        + px.colors.qualitative.Bold
        + px.colors.qualitative.Dark24
        + px.colors.qualitative.Set2
        + px.colors.qualitative.Pastel
    )
    all_known_apts = sorted(df["aptNm"].dropna().unique().tolist()) if "aptNm" in df.columns else []
    complex_color_map = {
        apt: COLOR_PALETTE[i % len(COLOR_PALETTE)]
        for i, apt in enumerate(all_known_apts)
    }

    # ---------------------------------------------------------
    # 인터랙티브 심층 분석 탭
    # ---------------------------------------------------------
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📈 시계열 가격 추이 & 단지별 추세선",
        "🏢 단지별 평당가 & 실거래가 비교",
        "층수 & 준공·월별 심층 분석",
        "📊 거래량 & 가격 분포 (지역/단지별)",
        "📋 실거래 상세 목록",
    ])

    # ---------------------------------------------------------
    # TAB 1: 시계열 가격 추이 & 단지별 추세선 (고급 심층 분석)
    # ---------------------------------------------------------
    with tab1:
        st.subheader("📈 시계열 실거래가 추이 및 단지별 가격 모멘텀 심층 분석")

        # 1. 단지별 가격 변동률 & 모멘텀 요약 카드
        st.markdown("##### 🎯 주요 단지별 가격 변동률 및 시세 모멘텀")
        momentum_cols = st.columns(min(4, max(1, len(filtered_df["aptNm"].unique()))))
        
        for i, apt in enumerate(filtered_df["aptNm"].unique()):
            col_target = momentum_cols[i % len(momentum_cols)]
            apt_data = filtered_df[filtered_df["aptNm"] == apt].sort_values("dealDate")
            
            if not apt_data.empty:
                first_deal = apt_data.iloc[0]["dealAmount"]
                last_deal = apt_data.iloc[-1]["dealAmount"]
                change_rate = ((last_deal - first_deal) / first_deal * 100) if first_deal > 0 else 0
                max_d = apt_data["dealAmount"].max()
                min_d = apt_data["dealAmount"].min()
                total_cnt = len(apt_data)
                
                # 신고가 건수
                cum_max = apt_data["dealAmount"].cummax()
                ath_cnt = (apt_data["dealAmount"] == cum_max).sum()
                
                color_badge = "#ef4444" if change_rate > 0 else "#0ea5e9" if change_rate < 0 else "#64748b"
                sign_str = "+" if change_rate > 0 else ""
                
                with col_target:
                    st.markdown(
                        f"""
                        <div class="metric-card" style="border-top: 4px solid {complex_color_map.get(apt, '#3b82f6')};">
                            <div class="metric-title" style="font-weight:bold; font-size:14px; color:var(--text-color);">{apt}</div>
                            <div style="font-size:20px; font-weight:bold; color:{color_badge}; margin: 4px 0;">
                                {sign_str}{change_rate:.1f}% 
                                <span style="font-size:12px; color:var(--text-color); font-weight:normal;">(기간 변동률)</span>
                            </div>
                            <div class="metric-sub">
                                • 최근 거래: <b>{format_korean_currency(last_deal)}</b><br>
                                • 변동폭: {format_korean_currency(min_d)} ~ {format_korean_currency(max_d)}<br>
                                • 총 {total_cnt}건 (🌟 신고가 갱신 {ath_cnt}회)
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

        # 2. 차트 컨트롤 옵션 바
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            show_ma = st.checkbox("단지별 7일 이동평균 추세선", value=True)
        with c2:
            show_ath = st.checkbox("🌟 신고가 갱신 거래 하이라이트", value=True)
        with c3:
            show_volume = st.checkbox("📊 거래량(건수) 서브플롯 결합", value=True)
        with c4:
            show_mean_line = st.checkbox("전체 평균 가격 중심축 표시", value=True)

        # 3. Plotly 시계열 결합 차트 생성 (거래량 서브플롯 포함)
        if show_volume:
            fig_trend = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.10,
                row_heights=[0.75, 0.25],
            )
        else:
            fig_trend = go.Figure()

        unique_apts = filtered_df["aptNm"].unique()

        for apt in unique_apts:
            apt_df = filtered_df[filtered_df["aptNm"] == apt].sort_values("dealDate").copy()
            color = complex_color_map.get(apt, "#3b82f6")

            # 신고가(누적 최고가) 판별
            apt_df["cummax"] = apt_df["dealAmount"].cummax()
            apt_df["is_ath"] = (apt_df["dealAmount"] == apt_df["cummax"]) & (apt_df["dealAmount"] > apt_df["dealAmount"].shift(1).fillna(0))

            # 일반 실거래 점
            normal_df = apt_df[~apt_df["is_ath"]] if show_ath else apt_df
            hover_text_normal = [
                f"<b>{row['aptNm']}</b> ({row['regionName']})<br>"
                f"거래일: {row['dealDate'].strftime('%Y-%m-%d') if pd.notna(row['dealDate']) else ''}<br>"
                f"거래금액: <b>{format_korean_currency(row['dealAmount'])}</b><br>"
                f"평당가: {int(round(row.get('pyeongPrice', 0))):,}만원/평<br>"
                f"층수: {row.get('floor', '-')}층 | 전용: {row.get('excluUseAr', '-')}㎡<br>"
                f"건축년도: {row.get('buildYear', '-')}년"
                + (f"<br><span style='color:red;'>⚠️ 해제/취소: {row.get('cdealDay', '')}</span>" if row.get('isCanceled') else "")
                for _, row in normal_df.iterrows()
            ]

            trace_scatter = go.Scatter(
                x=normal_df["dealDate"],
                y=normal_df["dealAmount"],
                mode="markers",
                name=f"{apt}",
                legendgroup=apt,
                showlegend=True,
                marker=dict(
                    size=9,
                    color=color,
                    opacity=0.75,
                    line=dict(width=1, color="white"),
                ),
                text=hover_text_normal,
                hoverinfo="text",
            )

            if show_volume:
                fig_trend.add_trace(trace_scatter, row=1, col=1)
            else:
                fig_trend.add_trace(trace_scatter)

            # 🌟 신고가 갱신 거래 마커 (단지 고유 색상 적용하여 구분 명확화)
            if show_ath:
                ath_df = apt_df[apt_df["is_ath"]]
                if not ath_df.empty:
                    hover_text_ath = [
                        f"🌟 <b>{row['aptNm']} [단지 신고가 갱신!]</b><br>"
                        f"거래일: {row['dealDate'].strftime('%Y-%m-%d') if pd.notna(row['dealDate']) else ''}<br>"
                        f"신고가 금액: <b style='color:{color};'>{format_korean_currency(row['dealAmount'])}</b><br>"
                        f"평당가: {int(round(row.get('pyeongPrice', 0))):,}만원/평<br>"
                        f"층수: {row.get('floor', '-')}층 | 전용: {row.get('excluUseAr', '-')}㎡"
                        for _, row in ath_df.iterrows()
                    ]
                    trace_ath = go.Scatter(
                        x=ath_df["dealDate"],
                        y=ath_df["dealAmount"],
                        mode="markers",
                        name=f"{apt} (신고가)",
                        legendgroup=apt,
                        showlegend=False,
                        marker=dict(
                            symbol="star",
                            size=14,
                            color=color,
                            line=dict(width=2, color="#ffffff"),
                        ),
                        text=hover_text_ath,
                        hoverinfo="text",
                    )
                    if show_volume:
                        fig_trend.add_trace(trace_ath, row=1, col=1)
                    else:
                        fig_trend.add_trace(trace_ath)

            # 2. 단지별 7일 이동평균 추세선 (범례 겹침 방지: showlegend=False)
            if show_ma and len(apt_df) >= 2:
                apt_daily = apt_df.groupby("dealDate")["dealAmount"].mean().reset_index().sort_values("dealDate")
                apt_daily["MA7"] = apt_daily["dealAmount"].rolling(window=7, min_periods=1).mean()

                trace_ma = go.Scatter(
                    x=apt_daily["dealDate"],
                    y=apt_daily["MA7"],
                    mode="lines",
                    name=f"{apt} (이동평균)",
                    legendgroup=apt,
                    showlegend=False,
                    line=dict(color=color, width=2.5),
                    hoverinfo="skip",
                )
                if show_volume:
                    fig_trend.add_trace(trace_ma, row=1, col=1)
                else:
                    fig_trend.add_trace(trace_ma)

            # 3. 하단 거래량 바 차트 (범례 겹침 방지: showlegend=False)
            if show_volume:
                daily_vol = apt_df.groupby("dealDate").size().reset_index(name="volume")
                trace_vol = go.Bar(
                    x=daily_vol["dealDate"],
                    y=daily_vol["volume"],
                    name=f"{apt} (거래량)",
                    legendgroup=apt,
                    showlegend=False,
                    marker=dict(color=color, opacity=0.7),
                    hoverinfo="x+y",
                )
                fig_trend.add_trace(trace_vol, row=2, col=1)

        # 4. 전체 평균 가격 중심선
        if show_mean_line:
            if show_volume:
                fig_trend.add_hline(
                    y=avg_price,
                    line_dash="dash",
                    line_color="#1d3557",
                    line_width=2,
                    annotation_text=f"평균: {format_korean_currency(avg_price)}",
                    annotation_position="top right",
                    annotation_font=dict(size=12, color="#1d3557", weight="bold"),
                    row=1,
                    col=1,
                )
            else:
                fig_trend.add_hline(
                    y=avg_price,
                    line_dash="dash",
                    line_color="#1d3557",
                    line_width=2,
                    annotation_text=f"평균: {format_korean_currency(avg_price)}",
                    annotation_position="top right",
                    annotation_font=dict(size=12, color="#1d3557", weight="bold"),
                )

        # 5. 깔끔한 레이아웃 및 퀵 줌(Range Selector) 설정
        fig_trend.update_layout(
            template="plotly_white",
            height=620 if show_volume else 520,
            hovermode="closest",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.05,
                xanchor="center",
                x=0.5,
                itemclick="toggle",
                itemdoubleclick="toggleothers",
            ),
            margin=dict(t=70, b=40, l=60, r=40),
        )

        # 상단 실거래가 Y축 및 하단 거래량 Y축 레이블 분리
        if show_volume:
            fig_trend.update_yaxes(title_text="실거래가 (만원)", tickformat=",", row=1, col=1)
            fig_trend.update_yaxes(title_text="거래량 (건)", row=2, col=1)
            fig_trend.update_xaxes(
                title_text="계약 체결일",
                row=2,
                col=1,
                rangeselector=dict(
                    x=0,
                    y=1.18,
                    buttons=list([
                        dict(count=1, label="1개월", step="month", stepmode="backward"),
                        dict(count=3, label="3개월", step="month", stepmode="backward"),
                        dict(count=6, label="6개월", step="month", stepmode="backward"),
                        dict(step="all", label="전체"),
                    ]),
                ),
            )
        else:
            fig_trend.update_yaxes(title_text="실거래가 (만원)", tickformat=",")
            fig_trend.update_xaxes(
                title_text="계약 체결일",
                rangeselector=dict(
                    x=0,
                    y=1.14,
                    buttons=list([
                        dict(count=1, label="1개월", step="month", stepmode="backward"),
                        dict(count=3, label="3개월", step="month", stepmode="backward"),
                        dict(count=6, label="6개월", step="month", stepmode="backward"),
                        dict(step="all", label="전체"),
                    ]),
                ),
            )

        st.plotly_chart(fig_trend, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 2: 단지별 평당 평균 가격 비교 (동적 축 스케일링 적용)
    # ---------------------------------------------------------
    with tab2:
        st.subheader("🏢 단지별 3.3㎡(평)당 평균 가격 및 실거래가 비교 (차이 강조형 가로 차트)")

        complex_agg = (
            filtered_df.groupby(["regionName", "aptNm"])
            .agg(
                평균평당가=("pyeongPrice", "mean"),
                평균거래가=("dealAmount", "mean"),
                중위거래가=("dealAmount", "median"),
                최고거래가=("dealAmount", "max"),
                최저거래가=("dealAmount", "min"),
                거래건수=("dealAmount", "count"),
                평균건축년도=("buildYear", lambda x: pd.to_numeric(x, errors="coerce").mean()),
            )
            .reset_index()
            .sort_values(by="평균평당가", ascending=True)
        )

        bar_colors = [complex_color_map.get(apt, "#3b82f6") for apt in complex_agg["aptNm"]]

        # 0부터 시작하지 않고 최솟값의 85% 지점부터 시작하여 단지 간 차이 극대화
        min_pyeong = complex_agg["평균평당가"].min() if not complex_agg.empty else 0
        max_pyeong = complex_agg["평균평당가"].max() if not complex_agg.empty else 0
        x_min_p = max(0, int(min_pyeong * 0.85))
        x_max_p = int(max_pyeong * 1.06)

        fig_hbar = go.Figure()

        # 평당가 막대 (정수 만원 표기 및 중복 제거)
        fig_hbar.add_trace(
            go.Bar(
                y=complex_agg["aptNm"],
                x=complex_agg["평균평당가"],
                orientation="h",
                name="평균 평당가 (만원/평)",
                marker=dict(
                    color=bar_colors,
                    line=dict(color="#1d3557", width=1),
                ),
                text=[
                    f"평당 {int(round(p)):,}만원 | 평균 {format_korean_currency(a)} ({c}건)"
                    for p, a, c in zip(complex_agg["평균평당가"], complex_agg["평균거래가"], complex_agg["거래건수"])
                ],
                textposition="auto",
                hoverinfo="text",
                hovertext=[
                    f"<b>{row['aptNm']}</b> ({row['regionName']})<br>"
                    f"평균 평당가: <b>{int(round(row['평균평당가'])):,} 만원/평</b><br>"
                    f"평균 거래가: {format_korean_currency(row['평균거래가'])}<br>"
                    f"최고 거래가: {format_korean_currency(row['최고거래가'])}<br>"
                    f"거래건수: {row['거래건수']}건 | 준공: 약 {int(round(row['평균건축년도']))}년"
                    for _, row in complex_agg.iterrows()
                ],
            )
        )

        # 전체 평균 평당가 수직 중심선
        fig_hbar.add_vline(
            x=avg_pyeong_price,
            line_dash="dash",
            line_color="#e63946",
            line_width=2,
            annotation_text=f"전체 평균 평당가: {int(round(avg_pyeong_price)):,}만원/평",
            annotation_position="top right",
        )

        chart_height = max(450, len(complex_agg) * 34 + 100)
        fig_hbar.update_layout(
            title="단지별 평당 평균 가격 랭킹 (차이 식별 최적화 축 스케일링)",
            xaxis_title="3.3㎡(평)당 평균 가격 (만원/평)",
            yaxis_title="아파트 단지명",
            template="plotly_white",
            height=chart_height,
            xaxis=dict(range=[x_min_p, x_max_p], tickformat=","),
            margin=dict(l=150, r=40, t=50, b=50),
        )
        st.plotly_chart(fig_hbar, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 3: 층수 & 준공·월별 심층 분석
    # ---------------------------------------------------------
    with tab3:
        st.subheader("층수 및 준공연도/거래월별 실거래가 심층 분석")

        # 1. 층수 분석 섹션
        st.markdown("#### 1️⃣ 층수(Floor)와 실거래가 상관관계 및 통계 분석")
        
        valid_floor_df = filtered_df.dropna(subset=["floor", "dealAmount"]).copy()
        if len(valid_floor_df) >= 3:
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                valid_floor_df["floor"], valid_floor_df["dealAmount"]
            )
            r_squared = r_value ** 2
            
            strength = "강한 양의 상관관계" if r_value > 0.4 else "보통 수준의 상관관계" if r_value > 0.15 else "미미한 상관관계"
            st.markdown(
                f"""<div class="insight-box">
                💡 <b>층수 가격 영향력 통계 분석 결과:</b><br>
                • 층수가 <b>1층 높아질 때마다 평균 약 +{int(round(slope)):,}만원</b>의 프리미엄이 형성됩니다. (전체 기준)<br>
                • 상관계수(r): <b>{r_value:.3f}</b> ({strength}, 결정계수 R² = {r_squared:.3f})
                </div>""",
                unsafe_allow_html=True,
            )

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            # 전체 층수 vs 실거래가 산점도 + 회귀선 (단지별 색상 적용)
            fig_floor_all = px.scatter(
                valid_floor_df,
                x="floor",
                y="dealAmount",
                color="aptNm",
                color_discrete_map=complex_color_map,
                trendline="ols",
                title="단지별 층수 vs 실거래가 분포 및 회귀선",
                labels={"floor": "층수", "dealAmount": "거래금액 (만원)", "aptNm": "단지명"},
                template="plotly_white",
                hover_data=["regionName", "dealDate"],
            )
            fig_floor_all.update_layout(height=420, yaxis=dict(tickformat=","))
            st.plotly_chart(fig_floor_all, use_container_width=True)

        with col_f2:
            # 단지별 층수 그룹(저층/중층/고층) 평균 가격 비교 (동적 Y축 스케일링)
            floor_group_summary = (
                filtered_df.groupby(["aptNm", "floorGroup"])["dealAmount"]
                .mean()
                .reset_index()
            )
            min_floor_pr = floor_group_summary["dealAmount"].min() if not floor_group_summary.empty else 0
            max_floor_pr = floor_group_summary["dealAmount"].max() if not floor_group_summary.empty else 0

            fig_floor_group = px.bar(
                floor_group_summary,
                x="aptNm",
                y="dealAmount",
                color="floorGroup",
                barmode="group",
                color_discrete_sequence=["#93c5fd", "#3b82f6", "#1e3a8a"],
                title="단지별 층수 구간(저층/중층/고층) 평균 가격 비교 (차이 강조형)",
                labels={"aptNm": "단지명", "dealAmount": "평균 거래가 (만원)", "floorGroup": "층수 구간"},
                template="plotly_white",
                text=floor_group_summary["dealAmount"].apply(lambda x: f"{int(round(x)):,}만"),
            )
            fig_floor_group.update_layout(
                height=420,
                yaxis=dict(range=[max(0, int(min_floor_pr * 0.85)), int(max_floor_pr * 1.06)], tickformat=","),
            )
            st.plotly_chart(fig_floor_group, use_container_width=True)

        # 단지별 층수 회귀 상세 분석 테이블
        st.markdown("##### 📋 단지별 층당 가격 상승액 & 상관관계 세부 통계")
        apt_floor_stats = []
        for apt in filtered_df["aptNm"].unique():
            sub = filtered_df[filtered_df["aptNm"] == apt].dropna(subset=["floor", "dealAmount"])
            if len(sub) >= 3 and sub["floor"].nunique() > 1:
                sl, _, r, p, _ = stats.linregress(sub["floor"], sub["dealAmount"])
                apt_floor_stats.append({
                    "단지명": apt,
                    "거래건수": len(sub),
                    "층당 가격 변동액": f"{int(round(sl)):+,} 만원/층",
                    "상관계수 (r)": f"{r:.3f}",
                    "저층(1~5층) 평균": format_korean_currency(sub[sub["floor"] <= 5]["dealAmount"].mean()),
                    "고층(16층+) 평균": format_korean_currency(sub[sub["floor"] >= 16]["dealAmount"].mean()),
                })
        if apt_floor_stats:
            st.dataframe(pd.DataFrame(apt_floor_stats), use_container_width=True, hide_index=True)

        st.markdown("---")

        # 2. 준공연도 + 거래월별 세분화 차트 (동적 축 스케일링 & 폰트 크기 강화)
        st.markdown("#### 2️⃣ 건축년도(준공연도) × 거래월(계약월) 세분화 평균 실거래가")

        if "buildYear" in filtered_df.columns and "dealYM" in filtered_df.columns:
            build_monthly_agg = (
                filtered_df.groupby(["buildYear", "dealYM"])["dealAmount"]
                .agg(["mean", "count"])
                .reset_index()
                .sort_values(by=["buildYear", "dealYM"])
            )
            build_monthly_agg["buildYear_label"] = build_monthly_agg["buildYear"].astype(str) + "년 준공"

            min_b_val = build_monthly_agg["mean"].min() if not build_monthly_agg.empty else 0
            max_b_val = build_monthly_agg["mean"].max() if not build_monthly_agg.empty else 0

            fig_build_monthly = px.bar(
                build_monthly_agg,
                y="buildYear_label",
                x="mean",
                color="dealYM",
                barmode="group",
                orientation="h",
                title="준공연도별 월별 실거래가 추이 (차이 식별 최적화)",
                labels={"buildYear_label": "준공연도", "mean": "평균 거래가 (만원)", "dealYM": "거래 계약월"},
                template="plotly_white",
                text=build_monthly_agg["mean"].apply(lambda x: f" {int(round(x)):,}만원 "),
            )
            # 글씨 크기를 14px 굵게 설정하고 텍스트 위치 최적화
            fig_build_monthly.update_traces(
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(size=13, weight="bold"),
            )
            fig_build_monthly.update_layout(
                height=max(480, len(build_monthly_agg["buildYear"].unique()) * 85 + 120),
                xaxis=dict(range=[max(0, int(min_b_val * 0.85)), int(max_b_val * 1.10)], tickformat=","),
                yaxis=dict(tickfont=dict(size=13, weight="bold")),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_build_monthly, use_container_width=True)

    # ---------------------------------------------------------
    # TAB 4: 거래량 & 가격 분포 (아파트 단지별 보기 기본 우선)
    # ---------------------------------------------------------
    with tab4:
        st.subheader("📊 거래량 추이 및 가격대 분포 통계")

        # 단지별 보기를 1순위 default로 설정
        view_mode = st.radio(
            "분석 기준 선택",
            ["🏢 아파트 단지별 보기", "🌐 지역(구)별 보기"],
            index=0,
            horizontal=True,
        )

        if view_mode == "🏢 아파트 단지별 보기":
            col_v1, col_v2 = st.columns(2)
            with col_v1:
                # 단지별 월별 거래량 (단지 일관 고유 색상 매핑 적용)
                vol_apt = filtered_df.groupby(["dealYM", "aptNm"]).size().reset_index(name="거래건수")
                fig_v_a = px.bar(
                    vol_apt,
                    x="dealYM",
                    y="거래건수",
                    color="aptNm",
                    color_discrete_map=complex_color_map,
                    barmode="stack",
                    title="단지별 월별 실거래 건수 추이 (단지 고유 색상 적용)",
                    labels={"dealYM": "계약년월", "거래건수": "거래건수", "aptNm": "단지명"},
                    text_auto=True,
                    template="plotly_white",
                )
                fig_v_a.update_layout(height=430)
                st.plotly_chart(fig_v_a, use_container_width=True)

            with col_v2:
                # 단지별 박스플롯 (단지 일관 고유 색상 매핑 적용)
                fig_b_a = px.box(
                    filtered_df,
                    x="aptNm",
                    y="dealAmount",
                    color="aptNm",
                    color_discrete_map=complex_color_map,
                    points="all",
                    title="단지별 실거래가 분포 박스플롯 (단지 고유 색상 적용)",
                    labels={"aptNm": "단지명", "dealAmount": "거래금액 (만원)"},
                    template="plotly_white",
                )
                fig_b_a.update_layout(height=430, showlegend=False, yaxis=dict(tickformat=","))
                st.plotly_chart(fig_b_a, use_container_width=True)

            st.markdown("##### 📋 단지별 세부 통계 요약표 (사분위수 & 평당가 & 최근 거래일)")
            apt_detail_stats = []
            for apt, grp in filtered_df.groupby("aptNm"):
                latest_date = grp["dealDate"].max().strftime("%Y-%m-%d") if pd.notna(grp["dealDate"].max()) else "-"
                avg_py = int(round(grp['pyeongPrice'].mean())) if "pyeongPrice" in grp and pd.notna(grp['pyeongPrice'].mean()) else 0
                apt_detail_stats.append({
                    "단지명": apt,
                    "지역": grp["regionName"].iloc[0],
                    "거래건수": len(grp),
                    "평균 평당가": f"{avg_py:,}만원/평" if avg_py > 0 else "-",
                    "평균 거래가": format_korean_currency(grp["dealAmount"].mean()),
                    "중위 거래가 (Q2)": format_korean_currency(grp["dealAmount"].median()),
                    "하위 25% (Q1)": format_korean_currency(grp["dealAmount"].quantile(0.25)),
                    "상위 75% (Q3)": format_korean_currency(grp["dealAmount"].quantile(0.75)),
                    "최고 거래가": format_korean_currency(grp["dealAmount"].max()),
                    "최저 거래가": format_korean_currency(grp["dealAmount"].min()),
                    "가격 표준편차": f"±{int(round(grp['dealAmount'].std())):,}만원" if pd.notna(grp["dealAmount"].std()) else "-",
                    "최근 거래일": latest_date,
                })
            
            apt_stat_df = pd.DataFrame(apt_detail_stats).sort_values(by="거래건수", ascending=False)
            st.dataframe(apt_stat_df, use_container_width=True, hide_index=True)

        else: # 🌐 지역(구)별 보기
            col_v1, col_v2 = st.columns(2)
            with col_v1:
                vol_region = filtered_df.groupby(["dealYM", "regionName"]).size().reset_index(name="거래건수")
                fig_v_r = px.bar(
                    vol_region,
                    x="dealYM",
                    y="거래건수",
                    color="regionName",
                    barmode="stack",
                    title="지역별 월별 실거래 건수 추이",
                    labels={"dealYM": "계약년월", "거래건수": "거래건수", "regionName": "지역"},
                    text_auto=True,
                    template="plotly_white",
                )
                fig_v_r.update_layout(height=430)
                st.plotly_chart(fig_v_r, use_container_width=True)

            with col_v2:
                fig_h_r = px.histogram(
                    filtered_df,
                    x="dealAmount",
                    color="regionName",
                    nbins=25,
                    title="지역별 실거래가 가격대 분포 (Boxplot 결합)",
                    labels={"dealAmount": "거래금액 (만원)", "regionName": "지역"},
                    template="plotly_white",
                    marginal="box",
                )
                fig_h_r.update_layout(height=430, yaxis=dict(tickformat=","), xaxis=dict(tickformat=","))
                st.plotly_chart(fig_h_r, use_container_width=True)

            st.markdown("##### 📋 지역별 가격 통계 요약표")
            reg_stat = (
                filtered_df.groupby("regionName")["dealAmount"]
                .agg(
                    거래건수="count",
                    평균가="mean",
                    중위값="median",
                    최저가="min",
                    최고가="max",
                    표준편차="std",
                )
                .reset_index()
            )
            reg_stat["평균가"] = reg_stat["평균가"].apply(format_korean_currency)
            reg_stat["중위값"] = reg_stat["중위값"].apply(format_korean_currency)
            reg_stat["최저가"] = reg_stat["최저가"].apply(format_korean_currency)
            reg_stat["최고가"] = reg_stat["최고가"].apply(format_korean_currency)
            reg_stat["표준편차"] = reg_stat["표준편차"].apply(lambda x: f"±{int(round(x)):,}만원" if pd.notna(x) else "-")
            st.dataframe(reg_stat, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # TAB 5: 실거래 상세 목록 & CSV 다운로드
    # ---------------------------------------------------------
    with tab5:
        st.subheader("📋 실거래 상세 내역 및 맞춤형 데이터 다운로드")

        # Tab 5 전용 다이나믹 필터 바
        col_t1, col_t2 = st.columns([1.5, 1])

        # 현재 탭에서 선택 가능한 단지 목록
        tab5_available_apts = sorted(filtered_df["aptNm"].dropna().unique().tolist())

        with col_t1:
            tab5_selected_apts = st.multiselect(
                "🏢 아파트 단지명 필터",
                options=tab5_available_apts,
                default=[],
                placeholder="전체 단지 조회 중 (특정 단지를 선택해 집중 분석)",
                key="tab5_apt_multiselect",
                help="조회하고자 하는 단지를 선택하세요. 비워둘 경우 전체 단지를 조회합니다.",
            )

        with col_t2:
            tab5_search_query = st.text_input(
                "🔍 단지명/동/거래유형 키워드 검색",
                value="",
                placeholder="예: 푸르지오, 매교동, 중개 등",
                key="tab5_keyword_search",
            )

        # 다이나믹 필터링 적용
        tab5_df = filtered_df.copy()
        if tab5_selected_apts:
            tab5_df = tab5_df[tab5_df["aptNm"].isin(tab5_selected_apts)]

        if tab5_search_query.strip():
            query = tab5_search_query.strip().lower()
            mask = (
                tab5_df["aptNm"].astype(str).str.lower().str.contains(query)
                | tab5_df["umdNm"].astype(str).str.lower().str.contains(query)
                | tab5_df["regionName"].astype(str).str.lower().str.contains(query)
                | tab5_df["dealType"].astype(str).str.lower().str.contains(query)
            )
            tab5_df = tab5_df[mask]

        # 결과 요약 헤더 및 통계
        res_cnt = len(tab5_df)
        res_avg = tab5_df["dealAmount"].mean() if res_cnt > 0 else 0
        res_max = tab5_df["dealAmount"].max() if res_cnt > 0 else 0
        res_min = tab5_df["dealAmount"].min() if res_cnt > 0 else 0

        st.markdown(
            f"""
            <div class="result-summary-box">
                <b>📊 실시간 조회 결과:</b> 총 <b>{res_cnt:,}</b>건 | 
                평균 거래가: <b>{format_korean_currency(res_avg)}</b> | 
                최고가: <span style="color:#ef4444;font-weight:bold;">{format_korean_currency(res_max)}</span> | 
                최저가: <span style="color:#0ea5e9;font-weight:bold;">{format_korean_currency(res_min)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

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
        available_display_cols = [c for c in display_cols if c in tab5_df.columns]

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

        table_df = tab5_df[available_display_cols].copy()
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

        # 다이나믹 파일명 생성 (단지명 선택 시 파일명에 반영)
        apt_tag = f"_{tab5_selected_apts[0]}" if len(tab5_selected_apts) == 1 else f"_{len(tab5_selected_apts)}단지" if len(tab5_selected_apts) > 1 else ""
        file_name = f"real_estate_transactions{apt_tag}_{pd.Timestamp.now().strftime('%Y%m%d')}.csv"

        # CSV 다운로드 버튼
        csv_data = tab5_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label=f"📥 필터링된 실거래 데이터 CSV 다운로드 ({res_cnt:,}건)",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
