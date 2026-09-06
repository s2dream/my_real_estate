# 🏢 아파트 실거래가 자동 수집 파이프라인 & 모니터링 대시보드

GitHub Actions를 통해 공공데이터포털(국토교통부 아파트 매매 실거래가) 데이터를 주기적으로 자동 수집·적재하고, SQLite 데이터베이스 및 Streamlit 웹 대시보드로 시각화하는 데이터 파이프라인입니다.

---

## 📂 프로젝트 구조

```plaintext
my_real_estate/
├── .github/
│   └── workflows/
│       └── collector.yml       # GitHub Actions 자동 수집 스케줄러 (4시간 주기 실행)
├── src/                        # 핵심 파이썬 소스코드 패키지
│   ├── db/                     # DB 관리 계층 (RealEstateDB)
│   │   └── db_manager.py
│   └── collector/              # 공공데이터포털 API 수집 및 롤링 적재 계층
│       └── collector.py
├── tests/                      # 자동화 테스트 슈트
│   ├── test_db.py              # SQLite DB 멱등성 및 스키마 단위 테스트
│   ├── test_collector.py       # 수집기 날짜 롤링 및 필터링 단위 테스트
│   ├── test_app.py             # 대시보드 UI/기능 통합 테스트
│   └── test_api.py             # API 연동 및 전처리 단위 테스트
├── data/
│   └── transactions.db         # SQLite 실거래가 데이터베이스 파일
├── app.py                      # ⭐ Streamlit 인터랙티브 시각화 대시보드 (메인 앱)
├── setting.yml                 # 수집 대상 기간, 지역, 전용면적(타입), 관심단지, DB 설정 파일
├── requirements.txt            # Python 의존성 패키지
└── README.md                   # 설정 및 배포 가이드
```

---

## 🛠️ 단계별 설정 및 배포 가이드

### 1단계: 공공데이터포털 API 키 발급
1. [공공데이터포털(data.go.kr)](https://www.data.go.kr/) 접속 및 로그인
2. **'국토교통부_아파트매매 실거래자료'** 오픈 API 검색 후 [활용신청] 클릭 (즉시 자동 승인)
3. 마이페이지 → 오픈API → 개발계정에서 **일반 인증키(Decoding Key)** 복사

---

### 2단계: GitHub Repository Secret 등록
1. GitHub 저장소의 **Settings** → **Secrets and variables** → **Actions** 이동
2. **New repository secret** 버튼 클릭:
   - **Name**: `DATA_GO_KR_API_KEY`
   - **Secret**: 1단계에서 복사한 **디코딩 인증키** 입력 후 등록

---

### 3단계: GitHub Actions 쓰기 권한(Write Permission) 활성화
GitHub Actions가 수집한 SQLite DB 파일(`data/transactions.db`)을 저장소에 다시 커밋·푸시할 수 있도록 권한을 부여합니다.
1. 저장소의 **Settings** → **Actions** → **General** 이동
2. 화면 하단 **Workflow permissions** 에서 **"Read and write permissions"** 선택 후 [Save]

---

### 4단계: `setting.yml` 수집 조건 커스터마이징
필요에 따라 `setting.yml` 파일을 수정하여 수집 범위와 조건을 언제든지 자유롭게 변경할 수 있습니다.

```yaml
collection:
  # 1) 초기 전체 수집 시작 년월 (신규 지역/최초 수집 시 사용)
  start_year_month: "202601"

  # 2) 30일 신고 의무 기간 대응 롤링 버퍼 (기존 수집 지역 대상)
  # - 이미 DB에 데이터가 있는 지역은 당월 + 직전 N개월만 호출하여 API 트래픽을 대폭 절감하고 지연 신고건을 갱신합니다.
  recent_months_buffer: 2 # 기본 최근 3개월 (당월, 전월, 전전월)

  # 3) 수집 대상 지역 코드 (복수 추가 가능)
  regions:
    - code: "41115"
      name: "수원시 팔달구"
    - code: "41117"
      name: "수원시 영통구"

  # 4) 전용면적(타입) 필터
  area_filter:
    enabled: true
    types:
      - name: "84타입"
        min: 84.0
        max: 85.0

  # 5) 준공연도(건축년도) 필터 (현시점 기준 N년 이내 신축/준신축 아파트)
  build_year_filter:
    enabled: true
    within_years: 10

  # 6) 특정 관심 단지 필터 (비워두면 전체 수집)
  target_complexes: []

storage:
  db_path: "data/transactions.db"
  table_name: "transactions"
```

---

### 5단계: Streamlit Cloud 무료 배포
1. [Streamlit Community Cloud](https://share.streamlit.io/) 접속 후 GitHub 계정 로그인
2. **"New app"** 클릭 후 대상 저장소, 브랜치(`main`), 파일 경로(`app.py`) 지정
3. **Deploy!** 클릭 시 전용 URL로 웹 대시보드가 상시 배포됩니다.

---

## 💻 로컬 환경 실행 방법

### 1. 의존성 설치
```bash
pip install -r requirements.txt
```

### 2. 로컬 API 키 설정 (`.env`)
```bash
cp .env.example .env
```
`.env` 파일에 발급받은 API 키를 입력합니다:
```plaintext
DATA_GO_KR_API_KEY=발급받은_공공데이터포털_인증키
```

### 3. 단위 테스트 전체 실행
```bash
python -m unittest discover tests
```

### 4. 로컬 데이터 수집 및 SQLite 적재
```bash
python -m src.collector.collector
```

### 5. Streamlit 로컬 웹 대시보드 실행
```bash
streamlit run app.py
```

## 분석 지표 기준

- 연식 필터는 한국 시간(KST)의 현재 연도를 기준으로 계산합니다.
- 7일 이동평균은 당일 포함 최근 7일(7일 전 제외)에 있는 거래일별 평균가격의 평균입니다. 거래 없는 날은 제외하며 거래일별 가중치는 같습니다. 화면에서 선택한 기간 안의 거래만 사용합니다.
- 신고가는 저장된 데이터의 계약 기간 내 단지·면적타입별 누적 최고가 갱신입니다. 취소 거래를 제외하고, 화면의 날짜·층수 필터 적용 전에 계산합니다. 첫 관측 거래도 포함하며 과거 전체 이력의 신고가를 의미하지 않습니다.
- 가격 변동률은 선택 기간의 첫·마지막 거래 관측월 중위가격을 비교합니다. 월별 거래 건수를 함께 표시하며 한 달만 있으면 비교하지 않습니다. 기간 경계의 월은 일부 날짜만 포함될 수 있고 면적·층수 등의 거래 구성 차이는 보정하지 않습니다.
- 층수 회귀는 단지별 거래 3건 이상, 서로 다른 층수가 있을 때 제공합니다. 표본 수·기울기·상관계수·결정계수를 표시하며, 다른 조건을 보정한 인과 효과나 확정적인 층수 프리미엄을 의미하지 않습니다.
