"""
한국 주식 포트폴리오 트래커 (Streamlit)
------------------------------------------------
로컬에서 실행하는 웹앱입니다. 아래 명령으로 실행하세요.

    streamlit run app.py

브라우저가 자동으로 열리며 http://localhost:8501 에서 확인할 수 있습니다.
매일 장 마감 30분 전(약 오후 3시)에 앱을 열고 "시세 새로고침" 버튼만 누르면
현재가·평가금액·손익·비중이 전부 자동으로 갱신됩니다.

시세는 네이버 금융의 공개 실시간 시세 API를 사용합니다(비공식, 인증 불필요).
"""

import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

DATA_FILE = Path(__file__).parent / "portfolio_data.csv"
COLUMNS = ["종목명", "종목코드", "섹터", "수량", "평단가", "현재가", "업데이트시각"]

# ------------------------------------------------------------------ #
# 데이터 로드 / 저장
# ------------------------------------------------------------------ #
def load_data() -> pd.DataFrame:
    if DATA_FILE.exists():
        df = pd.read_csv(DATA_FILE, dtype={"종목코드": str})
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = "" if col in ("종목명", "종목코드", "섹터", "업데이트시각") else 0.0
        return df[COLUMNS]
    # 처음 실행 시 예시 1행 제공
    return pd.DataFrame(
        [{"종목명": "삼성전자", "종목코드": "005930", "섹터": "반도체",
          "수량": 10, "평단가": 72000, "현재가": 72000, "업데이트시각": ""}],
        columns=COLUMNS,
    )


def save_data(df: pd.DataFrame) -> None:
    df.to_csv(DATA_FILE, index=False)


# ------------------------------------------------------------------ #
# 네이버 금융 실시간 시세 조회 (비공식 API)
# ------------------------------------------------------------------ #
def fetch_prices(codes: list[str]) -> dict:
    """종목코드 리스트를 받아 {코드: 현재가} 딕셔너리를 반환.
    실패하거나 코드를 찾지 못하면 해당 코드는 결과에서 빠집니다."""
    codes = [c.strip() for c in codes if c and str(c).strip()]
    if not codes:
        return {}
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(codes)}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        st.warning(f"시세 조회 실패: {e}")
        return {}

    # 응답 구조가 바뀔 수 있어 여러 경로를 시도
    datas = payload.get("datas")
    if datas is None:
        try:
            datas = payload["result"]["areas"][0]["datas"]
        except Exception:
            datas = []

    result = {}
    for d in datas or []:
        code = str(d.get("itemCode") or d.get("cd") or d.get("code") or "").strip()
        price_raw = d.get("closePrice") or d.get("cv") or d.get("nv")
        if not code or price_raw is None:
            continue
        try:
            price = float(str(price_raw).replace(",", ""))
        except ValueError:
            continue
        result[code] = price
    return result


# ------------------------------------------------------------------ #
# 화면 구성
# ------------------------------------------------------------------ #
st.set_page_config(page_title="포트폴리오 트래커", page_icon="📈", layout="wide")


def check_password() -> bool:
    """secrets.toml에 app_password가 설정된 경우에만 비밀번호를 요구합니다.
    (설정하지 않았다면 로컬 사용 시처럼 그냥 통과)"""
    if "app_password" not in st.secrets:
        return True
    if st.session_state.get("authed"):
        return True
    st.title("📈 포트폴리오 트래커")
    pw = st.text_input("비밀번호를 입력하세요", type="password")
    if pw:
        if pw == st.secrets["app_password"]:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False


if not check_password():
    st.stop()

st.title("📈 포트폴리오 트래커")
st.caption("매일 장 마감 30분 전(약 15:00)에 열어서 시세만 새로고침하면 됩니다.")

df = load_data()

# ---- 보유 종목 편집 테이블 ----
st.subheader("보유 종목")
st.caption("표를 직접 수정하거나, 행 아래 ➕ 로 종목을 추가/삭제할 수 있습니다. 종목코드는 6자리 숫자(예: 삼성전자 005930)입니다.")

edited = st.data_editor(
    df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "종목명": st.column_config.TextColumn(required=True),
        "종목코드": st.column_config.TextColumn(help="6자리 종목코드 (예: 005930)"),
        "섹터": st.column_config.TextColumn(),
        "수량": st.column_config.NumberColumn(min_value=0, step=1),
        "평단가": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f"),
        "현재가": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f"),
        "업데이트시각": st.column_config.TextColumn(disabled=True),
    },
    key="editor",
)

col_a, col_b, col_c = st.columns([1, 1, 3])
with col_a:
    if st.button("💾 저장", use_container_width=True):
        save_data(edited)
        st.success("저장했습니다.")
with col_b:
    if st.button("🔄 시세 새로고침", use_container_width=True, type="primary"):
        codes = edited["종목코드"].astype(str).tolist()
        prices = fetch_prices(codes)
        now = time.strftime("%m/%d %H:%M")
        updated = 0
        for i, code in enumerate(edited["종목코드"].astype(str)):
            if code in prices:
                edited.loc[i, "현재가"] = prices[code]
                edited.loc[i, "업데이트시각"] = now
                updated += 1
        save_data(edited)
        st.success(f"{updated}개 종목 시세를 갱신했습니다.")
        st.rerun()

df = edited.copy()
save_data(df)  # 편집 즉시 반영

# ------------------------------------------------------------------ #
# 계산
# ------------------------------------------------------------------ #
df["수량"] = pd.to_numeric(df["수량"], errors="coerce").fillna(0)
df["평단가"] = pd.to_numeric(df["평단가"], errors="coerce").fillna(0)
df["현재가"] = pd.to_numeric(df["현재가"], errors="coerce").fillna(0)
df["섹터"] = df["섹터"].replace("", "미분류").fillna("미분류")

df["평가금액"] = df["수량"] * df["현재가"]
df["매입금액"] = df["수량"] * df["평단가"]
df["손익"] = df["평가금액"] - df["매입금액"]
df["손익률(%)"] = df.apply(lambda r: (r["손익"] / r["매입금액"] * 100) if r["매입금액"] else 0, axis=1)

total_valuation = df["평가금액"].sum()
total_cost = df["매입금액"].sum()
total_profit = total_valuation - total_cost
total_profit_pct = (total_profit / total_cost * 100) if total_cost else 0

df["비중(%)"] = df["평가금액"].apply(lambda v: (v / total_valuation * 100) if total_valuation else 0)

# ------------------------------------------------------------------ #
# 요약 지표
# ------------------------------------------------------------------ #
st.subheader("요약")
m1, m2, m3, m4 = st.columns(4)
m1.metric("총 평가금액", f"₩{total_valuation:,.0f}")
m2.metric("총 손익", f"₩{total_profit:,.0f}", f"{total_profit_pct:+.2f}%")
m3.metric("총 매입금액", f"₩{total_cost:,.0f}")
m4.metric("보유 종목 수", f"{len(df)}개")

# ------------------------------------------------------------------ #
# 섹터 비중
# ------------------------------------------------------------------ #
st.subheader("섹터 비중")
if total_valuation > 0:
    sector_df = (
        df.groupby("섹터")["평가금액"].sum()
        .reset_index()
        .assign(**{"비중(%)": lambda d: d["평가금액"] / total_valuation * 100})
        .sort_values("평가금액", ascending=False)
    )
    st.bar_chart(sector_df.set_index("섹터")["비중(%)"])
    st.dataframe(
        sector_df.style.format({"평가금액": "₩{:,.0f}", "비중(%)": "{:.1f}%"}),
        use_container_width=True, hide_index=True,
    )
else:
    st.info("종목을 추가하고 시세를 새로고침하면 섹터 비중이 표시됩니다.")

# ------------------------------------------------------------------ #
# 상세 테이블 (손익 색상 표시)
# ------------------------------------------------------------------ #
st.subheader("종목별 현황")

def color_profit(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return "color: #d9364f"  # 국내 관례: 상승=빨강
        elif val < 0:
            return "color: #2b6cd4"  # 하락=파랑
    return ""

display_cols = ["종목명", "섹터", "수량", "평단가", "현재가", "평가금액", "손익", "손익률(%)", "비중(%)", "업데이트시각"]
styled = (
    df[display_cols]
    .sort_values("비중(%)", ascending=False)
    .style.map(color_profit, subset=["손익", "손익률(%)"])
    .format({
        "평단가": "{:,.0f}", "현재가": "{:,.0f}", "평가금액": "₩{:,.0f}",
        "손익": "{:+,.0f}", "손익률(%)": "{:+.2f}%", "비중(%)": "{:.1f}%",
    })
)
st.dataframe(styled, use_container_width=True, hide_index=True)

st.caption("시세는 네이버 금융 비공식 API를 사용하며, 지연되거나 일시적으로 실패할 수 있습니다. "
           "필요하면 표에서 현재가를 직접 수정해도 됩니다.")
