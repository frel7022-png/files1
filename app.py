"""
한국 주식 포트폴리오 트래커 (Streamlit) — SP Orchestra
------------------------------------------------------------------
    streamlit run app.py

시세는 네이버 금융 비공식 공개 API를 사용합니다(종목코드 자동 검색 포함).
"""

import calendar
import uuid
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).parent

HOLDINGS_FILE = HERE / "portfolio_data.csv"
TX_FILE = HERE / "transactions.csv"
STATE_FILE = HERE / "account_state.csv"
HISTORY_FILE = HERE / "asset_history.csv"

HOLD_COLUMNS = ["종목명", "종목코드", "섹터", "수량", "평단가", "현재가", "등락률", "업데이트시각"]
TX_COLUMNS = ["id", "날짜", "종목명", "구분", "수량", "단가", "실현손익", "메모", "정산반영"]
EDIT_COLUMNS = ["종목명", "섹터", "수량", "평단가", "현재가"]

UP_COLOR = "#d9364f"    # 국내 관례: 상승/이익 = 빨강
DOWN_COLOR = "#2b6cd4"  # 하락/손실 = 파랑
CASH_LABEL = "현금(예수금)"

SECTOR_PALETTE = [
    "#2DD4BF", "#F5A623", "#A78BFA", "#34D399", "#F472B6",
    "#FBBF24", "#60A5FA", "#F87171", "#C084FC", "#38BDF8", "#FB923C",
]

THEMES = {
    "dark": {
        "bg": "#0a0c10", "card": "#12151c", "card2": "#20242e", "border": "#2b303c",
        "text": "#e8eaed", "muted": "#9aa4b2", "muted2": "#6b7280", "cash_dot": "#4b5563",
    },
    "light": {
        "bg": "#f4f5f7", "card": "#ffffff", "card2": "#eceef1", "border": "#e2e4e9",
        "text": "#1a1d23", "muted": "#5b6472", "muted2": "#7a8290", "cash_dot": "#9aa0ab",
    },
}


def theme() -> dict:
    return THEMES[st.session_state.get("theme", "light")]


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def now_kst_str() -> str:
    return now_kst().strftime("%m/%d %H:%M")


def clean_str(x) -> str:
    """pd.NA / NaN / None 안전하게 빈 문자열로 처리."""
    if x is None or (isinstance(x, float) and pd.isna(x)) or (x is pd.NA):
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


# ------------------------------------------------------------------ #
# 데이터 로드 / 저장
# ------------------------------------------------------------------ #
def load_holdings() -> pd.DataFrame:
    if HOLDINGS_FILE.exists():
        df = pd.read_csv(HOLDINGS_FILE, dtype={"종목코드": str}, keep_default_na=False, na_values=[""])
        for col in HOLD_COLUMNS:
            if col not in df.columns:
                df[col] = "" if col in ("종목명", "종목코드", "섹터", "업데이트시각") else 0.0
        for col in ("수량", "평단가", "현재가", "등락률"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
        for col in ("종목명", "종목코드", "섹터", "업데이트시각"):
            df[col] = df[col].apply(clean_str)
        return df[HOLD_COLUMNS]
    return pd.DataFrame(columns=HOLD_COLUMNS)


def save_holdings(df: pd.DataFrame) -> None:
    df.to_csv(HOLDINGS_FILE, index=False)


def load_transactions() -> pd.DataFrame:
    if TX_FILE.exists():
        df = pd.read_csv(TX_FILE, dtype={"id": str}, keep_default_na=False, na_values=[""])
        for col in TX_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[TX_COLUMNS]
    return pd.DataFrame(columns=TX_COLUMNS)


def save_transactions(df: pd.DataFrame) -> None:
    df.to_csv(TX_FILE, index=False)


def load_state() -> dict:
    if STATE_FILE.exists():
        df = pd.read_csv(STATE_FILE)
        return {"cash": float(df.loc[0, "예수금"]), "initial": float(df.loc[0, "최초자본"])}
    return {"cash": 10_000_000.0, "initial": 10_000_000.0}


def save_state(state: dict) -> None:
    pd.DataFrame([{"예수금": state["cash"], "최초자본": state["initial"]}]).to_csv(STATE_FILE, index=False)


def load_history() -> pd.DataFrame:
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE)
    return pd.DataFrame(columns=["날짜", "총자산", "조정자산"])


def save_history(df: pd.DataFrame) -> None:
    df.to_csv(HISTORY_FILE, index=False)


def snapshot_history(total_assets: float, adjusted_assets: float) -> None:
    hist = load_history()
    d = today_kst_str()
    hist = hist[hist["날짜"] != d]
    hist = pd.concat([hist, pd.DataFrame([{"날짜": d, "총자산": total_assets, "조정자산": adjusted_assets}])])
    hist = hist.sort_values("날짜")
    save_history(hist)


# ------------------------------------------------------------------ #
# 네이버 금융: 종목명 → 종목코드 자동 검색 + 시세 조회
# ------------------------------------------------------------------ #
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def resolve_code(name: str):
    name = (name or "").strip()
    if not name:
        return None
    url = "https://ac.stock.naver.com/ac"
    params = {"q": name, "target": "stock,index,marketindicator,coin,ipo", "st": "111"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=6)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        for it in items:
            code = str(it.get("code") or "").strip()
            if code.isdigit() and len(code) == 6:
                return code
    except Exception:
        pass
    return None


def fetch_quotes(codes: list[str]) -> dict:
    codes = [c for c in codes if c]
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
        chg_raw = d.get("fluctuationsRatio") or d.get("cr")
        if not code or price_raw is None:
            continue
        try:
            price = float(str(price_raw).replace(",", ""))
        except ValueError:
            continue
        try:
            change_pct = float(str(chg_raw).replace(",", "").replace("%", "")) if chg_raw is not None else 0.0
        except ValueError:
            change_pct = 0.0
        result[code] = {"price": price, "change_pct": change_pct}
    return result


def refresh_all_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    unresolved = []
    for i, row in df.iterrows():
        code = clean_str(row.get("종목코드", ""))
        if not code or code.lower() == "nan":
            found = resolve_code(row["종목명"])
            if found:
                df.loc[i, "종목코드"] = found
            else:
                unresolved.append(row["종목명"])

    codes = [clean_str(c) for c in df["종목코드"].tolist()]
    codes = [c for c in codes if c and c.lower() != "nan"]
    quotes = fetch_quotes(codes)

    now = now_kst_str()
    updated, failed = 0, []
    for i, row in df.iterrows():
        code = clean_str(row.get("종목코드", ""))
        if code and code in quotes:
            df.loc[i, "현재가"] = quotes[code]["price"]
            df.loc[i, "등락률"] = quotes[code]["change_pct"]
            df.loc[i, "업데이트시각"] = now
            updated += 1
        elif code:
            failed.append(row["종목명"])

    save_holdings(df)
    if updated:
        st.toast(f"{updated}개 종목 시세 갱신 완료")
    if unresolved:
        st.warning("종목명을 찾지 못했어요(직접 입력 필요): " + ", ".join(unresolved))
    if failed:
        st.warning("시세를 못 가져왔어요(직접 입력 필요): " + ", ".join(failed))
    return df


# ------------------------------------------------------------------ #
# 지표 계산
# ------------------------------------------------------------------ #
def compute_metrics(df: pd.DataFrame, cash: float):
    df = df.copy()
    for col in ("수량", "평단가", "현재가", "등락률"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["섹터"] = df["섹터"].replace("", "미분류").fillna("미분류")

    df["평가금액"] = df["수량"] * df["현재가"]
    df["매입금액"] = df["수량"] * df["평단가"]
    df["손익"] = df["평가금액"] - df["매입금액"]
    df["손익률"] = df.apply(lambda r: (r["손익"] / r["매입금액"] * 100) if r["매입금액"] else 0, axis=1)

    stock_valuation = df["평가금액"].sum()
    total_assets = stock_valuation + cash
    df["비중"] = df["평가금액"].apply(lambda v: (v / total_assets * 100) if total_assets else 0)

    unrealized_loss = -df.loc[df["손익"] < 0, "손익"].sum()
    return df, stock_valuation, total_assets, unrealized_loss


# ------------------------------------------------------------------ #
# 거래 반영 (매수/매도)
# ------------------------------------------------------------------ #
def apply_transaction(holdings: pd.DataFrame, state: dict, name: str, kind: str, qty: float, price: float):
    holdings = holdings.copy()
    realized = None
    match = holdings.index[holdings["종목명"] == name]

    if kind == "매수":
        cost = qty * price
        state["cash"] -= cost
        if len(match):
            i = match[0]
            old_qty = float(holdings.loc[i, "수량"])
            old_avg = float(holdings.loc[i, "평단가"])
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + cost) / new_qty if new_qty else 0
            holdings.loc[i, "수량"] = new_qty
            holdings.loc[i, "평단가"] = new_avg
            if not holdings.loc[i, "현재가"] or float(holdings.loc[i, "현재가"]) == 0:
                holdings.loc[i, "현재가"] = price
        else:
            new_row = {c: "" for c in HOLD_COLUMNS}
            new_row.update({"종목명": name, "종목코드": "", "섹터": "미분류",
                             "수량": qty, "평단가": price, "현재가": price,
                             "등락률": 0, "업데이트시각": now_kst_str()})
            holdings = pd.concat([holdings, pd.DataFrame([new_row])], ignore_index=True)
    else:  # 매도
        proceeds = qty * price
        state["cash"] += proceeds
        if len(match):
            i = match[0]
            old_qty = float(holdings.loc[i, "수량"])
            old_avg = float(holdings.loc[i, "평단가"])
            realized = (price - old_avg) * qty
            new_qty = old_qty - qty
            if new_qty <= 0:
                holdings = holdings.drop(index=i).reset_index(drop=True)
            else:
                holdings.loc[i, "수량"] = new_qty
        else:
            realized = 0.0

    return holdings, state, realized


# ------------------------------------------------------------------ #
# 페이지 설정
# ------------------------------------------------------------------ #
st.set_page_config(page_title="SP Orchestra", page_icon="◆", layout="centered")

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"
T = theme()

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&display=swap');
    .stApp {{ background-color: {T['bg']}; }}
    .brand {{
        font-family: 'Sora', sans-serif;
        font-weight: 800;
        font-size: 23px;
        letter-spacing: 0.02em;
        color: {T['text']};
        padding: 4px 0;
    }}
    .block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; padding-left: 1rem; padding-right: 1rem; max-width: 480px; }}
    #MainMenu, footer, header {{ visibility: hidden; }}
    h1, h2, h3, h4, h5, p, span, label, div {{ color: {T['text']}; }}

    .summary-box {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:14px; padding:18px 20px; margin-bottom:14px; }}
    .summary-label {{ color:{T['muted']}; font-size:13px; margin-bottom:4px; }}
    .summary-main {{ font-size:28px; font-weight:800; line-height:1.2; }}
    .summary-sub {{ font-size:14px; font-weight:600; margin-left:6px; }}
    .summary-grid {{ display:flex; flex-wrap:wrap; justify-content:space-between; margin-top:14px; gap:8px; }}
    .summary-grid div {{ font-size:12.5px; color:{T['muted']}; min-width:29%; }}
    .summary-grid b {{ display:block; font-size:15px; color:{T['text']}; margin-top:2px; }}
    .capital-line {{ margin-top:10px; padding-top:10px; border-top:1px solid {T['border']}; font-size:12.5px; color:{T['muted']}; }}
    .capital-line b {{ font-size:14px; }}

    .legend-wrap {{ display:flex; flex-wrap:wrap; gap:7px 14px; margin-top:10px; justify-content:center; }}
    .legend-item {{ display:flex; align-items:center; gap:5px; font-size:12px; color:{T['text']}; }}
    .legend-dot {{ width:8px; height:8px; border-radius:99px; flex-shrink:0; }}
    .legend-pct {{ color:{T['muted']}; font-family: ui-monospace, monospace; }}

    .stock-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:12px; padding:12px 16px; margin-bottom:8px; }}
    .stock-top {{ display:flex; justify-content:space-between; align-items:baseline; }}
    .stock-name {{ font-size:15px; font-weight:700; color:{T['text']}; }}
    .sector-tag {{ font-size:10.5px; padding:2px 7px; border-radius:5px; font-weight:600; }}
    .stock-grid {{ display:grid; grid-template-columns: 0.7fr 1.05fr 1.05fr 1.05fr; gap:6px; margin-top:9px; }}
    .cell .top {{ font-size:12.5px; font-weight:700; color:{T['text']}; }}
    .cell .bottom {{ font-size:11px; color:{T['muted']}; margin-top:2px; }}
    .stock-foot {{ display:flex; justify-content:space-between; margin-top:8px; font-size:10.5px; color:{T['muted2']}; }}

    .tx-card {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:10px; padding:10px 14px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; }}
    .tx-left {{ font-size:13px; }}
    .tx-left .name {{ font-weight:700; color:{T['text']}; }}
    .tx-left .meta {{ color:{T['muted']}; font-size:11.5px; }}
    .tx-right {{ text-align:right; font-size:13px; font-weight:700; }}

    /* ---- 옅은/짙은 회색 버튼: 눌러도 색 안 바뀌게 강제 고정 ---- */
    div.stButton > button,
    div.stButton > button:hover,
    div.stButton > button:active,
    div.stButton > button:focus,
    div.stButton > button:focus:not(:active) {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
        border-radius: 10px;
        font-weight: 600;
        font-size: 12px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        padding-left: 0.35rem;
        padding-right: 0.35rem;
    }}
    div.stButton > button p {{ color: {T['text']} !important; }}
    div.stFormSubmitButton > button,
    div.stFormSubmitButton > button:hover,
    div.stFormSubmitButton > button:active,
    div.stFormSubmitButton > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-testid="stPopover"] > div > button,
    div[data-testid="stPopover"] > div > button:hover,
    div[data-testid="stPopover"] > div > button:active,
    div[data-testid="stPopover"] > div > button:focus {{
        background-color: {T['card2']} !important;
        color: {T['text']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}

    [data-testid="stExpander"] {{
        background-color: {T['card']} !important;
        border: 1px solid {T['border']} !important;
        border-radius: 12px !important;
    }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary:hover,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] svg {{
        color: {T['text']} !important;
        fill: {T['text']} !important;
    }}

    div[data-baseweb="input"],
    div[data-baseweb="base-input"],
    div[data-baseweb="textarea"] {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="base-input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within {{
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
        outline: none !important;
    }}
    input, textarea {{
        background-color: transparent !important;
        color: {T['text']} !important;
    }}
    button[data-testid="stNumberInputStepDown"],
    button[data-testid="stNumberInputStepUp"],
    button[data-testid="stNumberInputStepDown"]:hover,
    button[data-testid="stNumberInputStepUp"]:hover {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        color: {T['text']} !important;
    }}
    div[data-baseweb="select"] > div {{
        background-color: {T['card2']} !important;
        border: 1px solid {T['border']} !important;
        box-shadow: none !important;
    }}
    div[data-baseweb="select"] svg {{ fill: {T['muted']} !important; }}
    div[data-testid="stDateInput"] input {{
        background-color: {T['card2']} !important;
    }}

    /* 라디오/토글: 강조색을 무채색으로 눌러서 튀는 색 방지 */
    div[data-testid="stToggle"] span {{ background-color: {T['card2']} !important; }}
    div[role="radiogroup"] {{ flex-wrap: wrap !important; gap: 4px 10px !important; }}
    div[role="radiogroup"] label {{
        background-color: {T['card2']};
        border: 1px solid {T['border']};
        border-radius: 8px;
        padding: 3px 10px;
        margin: 0 !important;
    }}
</style>
""", unsafe_allow_html=True)


def check_password() -> bool:
    if "app_password" not in st.secrets:
        return True
    if st.session_state.get("authed"):
        return True
    st.markdown('<div class="brand">SP Orchestra</div>', unsafe_allow_html=True)
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

col_title, col_theme = st.columns([3, 1])
with col_title:
    st.markdown('<div class="brand">SP Orchestra</div>', unsafe_allow_html=True)
with col_theme:
    is_dark = st.session_state["theme"] == "dark"
    new_dark = st.toggle("다크", value=is_dark, key="theme_switch", label_visibility="collapsed")
    if new_dark != is_dark:
        st.session_state["theme"] = "dark" if new_dark else "light"
        st.rerun()

holdings = load_holdings()
state = load_state()
tx = load_transactions()

tab_port, tab_tx = st.tabs(["포트폴리오", "거래 기록"])

# ==================================================================== #
# 탭 1: 포트폴리오
# ==================================================================== #
with tab_port:
    df, stock_valuation, total_assets, unrealized_loss = compute_metrics(holdings, state["cash"])
    total_cost = df["매입금액"].sum()
    stock_profit = stock_valuation - total_cost
    stock_profit_pct = (stock_profit / total_cost * 100) if total_cost else 0

    capital_return = total_assets - state["initial"]
    capital_return_pct = (capital_return / state["initial"] * 100) if state["initial"] else 0

    today_str = today_kst_str()
    today_tx = tx[tx["날짜"].astype(str) == today_str]
    daily_pnl = pd.to_numeric(
        today_tx.loc[today_tx["구분"] == "매도", "실현손익"], errors="coerce"
    ).sum()

    color = UP_COLOR if stock_profit >= 0 else DOWN_COLOR
    sign = "+" if stock_profit >= 0 else ""
    cap_color = UP_COLOR if capital_return >= 0 else DOWN_COLOR
    cap_sign = "+" if capital_return >= 0 else ""
    daily_color = UP_COLOR if daily_pnl > 0 else (DOWN_COLOR if daily_pnl < 0 else T["muted"])
    daily_sign = "+" if daily_pnl > 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">보유종목 평가손익</div>
        <span class="summary-main" style="color:{color}">{sign}{stock_profit:,.0f}원</span>
        <span class="summary-sub" style="color:{color}">{sign}{stock_profit_pct:.2f}%</span>
        <div class="summary-grid">
            <div>예수금<b>{state['cash']:,.0f}원</b></div>
            <div>총 매입<b>{total_cost:,.0f}원</b></div>
            <div>총 평가<b>{stock_valuation:,.0f}원</b></div>
            <div>총자산<b>{total_assets:,.0f}원</b></div>
            <div>일일손익<b style="color:{daily_color}">{daily_sign}{daily_pnl:,.0f}원</b></div>
            <div>보유종목<b>{len(df)}개</b></div>
        </div>
        <div class="capital-line">최초 자본 10,000,000원 대비&nbsp;
            <b style="color:{cap_color}">{cap_sign}{capital_return:,.0f}원 ({cap_sign}{capital_return_pct:.2f}%)</b>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 섹터 비중 도넛 ----
    with st.expander("섹터 비중 보기", expanded=False):
        include_cash = st.toggle("예수금 포함", value=st.session_state.get("include_cash", True), key="cash_toggle")
        st.session_state["include_cash"] = include_cash

        sector_val = df.groupby("섹터")["평가금액"].sum().to_dict()
        if include_cash and state["cash"] > 0:
            sector_val[CASH_LABEL] = state["cash"]
        sector_items = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
        denom = sum(v for _, v in sector_items)

        if denom > 0 and sector_items:
            labels = [s for s, _ in sector_items]
            values = [v for _, v in sector_items]
            colors = [SECTOR_PALETTE[i % len(SECTOR_PALETTE)] for i in range(len(labels))]
            cash_idx = labels.index(CASH_LABEL) if CASH_LABEL in labels else None
            if cash_idx is not None:
                colors[cash_idx] = T["cash_dot"]

            fig, ax = plt.subplots(figsize=(4.6, 4.6))
            fig.patch.set_alpha(0)
            ax.pie(values, colors=colors, startangle=90, counterclock=False,
                   wedgeprops=dict(width=0.38, edgecolor=T["card"], linewidth=1.2))
            ax.set(aspect="equal")
            st.pyplot(fig, use_container_width=True)
            plt.close(fig)

            legend_html = '<div class="legend-wrap">'
            for lbl, val, c in zip(labels, values, colors):
                pct = val / denom * 100
                legend_html += (f'<div class="legend-item"><span class="legend-dot" '
                                 f'style="background:{c}"></span>{lbl} '
                                 f'<span class="legend-pct">{pct:.1f}%</span></div>')
            legend_html += "</div>"
            st.markdown(legend_html, unsafe_allow_html=True)
        else:
            st.info("종목/예수금 데이터가 있으면 섹터 비중이 표시됩니다.")

    # ---- 종목별 보유현황 ----
    SORT_OPTIONS = {"비중순": "weight", "섹터순": "sector", "현재가순": "price",
                     "평가금액순": "valuation", "손익순": "profit"}
    if "sort_mode" not in st.session_state:
        st.session_state.sort_mode = "weight"

    st.markdown("##### 종목별 보유현황")
    labels = list(SORT_OPTIONS.keys())
    cur_label = next(k for k, v in SORT_OPTIONS.items() if v == st.session_state.sort_mode)
    chosen = st.radio("정렬 기준", labels, index=labels.index(cur_label),
                       horizontal=True, label_visibility="collapsed", key="sort_radio")
    st.session_state.sort_mode = SORT_OPTIONS[chosen]

    sector_color_map = {}
    for i, s in enumerate(df.sort_values("평가금액", ascending=False)["섹터"].unique()):
        sector_color_map[s] = SECTOR_PALETTE[i % len(SECTOR_PALETTE)]

    mode = st.session_state.sort_mode
    if mode == "sector":
        sector_totals = df.groupby("섹터")["평가금액"].sum().sort_values(ascending=False)
        sector_order = {s: i for i, s in enumerate(sector_totals.index)}
        df_sorted = df.copy()
        df_sorted["_rank"] = df_sorted["섹터"].map(sector_order)
        df_sorted = df_sorted.sort_values(["_rank", "평가금액"], ascending=[True, False])
    elif mode == "price":
        df_sorted = df.sort_values("현재가", ascending=False)
    elif mode == "valuation":
        df_sorted = df.sort_values("평가금액", ascending=False)
    elif mode == "profit":
        df_sorted = df.sort_values("손익", ascending=False)
    else:
        df_sorted = df.sort_values("비중", ascending=False)

    rows = df_sorted.to_dict("records")

    if not rows:
        st.info("보유 종목이 없습니다. 아래 '종목 편집'에서 추가하거나, '거래 기록' 탭에서 매수를 기록해보세요.")
    else:
        for r in rows:
            pc = UP_COLOR if r["손익"] >= 0 else DOWN_COLOR
            psign = "+" if r["손익"] >= 0 else ""
            cc = UP_COLOR if r["등락률"] >= 0 else DOWN_COLOR
            csign = "+" if r["등락률"] >= 0 else ""
            sc = sector_color_map.get(r["섹터"], "#6b7280")
            st.markdown(f"""
            <div class="stock-card">
                <div class="stock-top">
                    <span class="stock-name">{r['종목명']}</span>
                    <span class="sector-tag" style="background:{sc}22;color:{sc}">{r['섹터']}</span>
                </div>
                <div class="stock-grid">
                    <div class="cell"><div class="top">{r['수량']:.0f}주</div></div>
                    <div class="cell"><div class="top">{r['현재가']:,.0f}</div><div class="bottom">{r['평단가']:,.0f}</div></div>
                    <div class="cell"><div class="top">{r['평가금액']:,.0f}</div><div class="bottom">{r['매입금액']:,.0f}</div></div>
                    <div class="cell">
                        <div class="top" style="color:{pc}">{psign}{r['손익']:,.0f}</div>
                        <div class="bottom" style="color:{pc}">{psign}{r['손익률']:.2f}%
                            <span style="color:{cc}">· {csign}{r['등락률']:.2f}%</span>
                        </div>
                    </div>
                </div>
                <div class="stock-foot">
                    <span>비중 {r['비중']:.1f}%</span>
                    <span>{r['업데이트시각'] or '-'}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ---- 새로고침 / 종목 편집 ----
    col_r, col_e = st.columns([1, 1])
    with col_r:
        refresh_clicked = st.button("시세 새로고침", use_container_width=True, key="refresh_btn")
    with col_e:
        with st.popover("종목 편집", use_container_width=True):
            st.caption("종목코드는 몰라도 됩니다 — 종목명만 정확히 입력하면 새로고침할 때 자동으로 찾아옵니다.")
            edited = st.data_editor(
                holdings,
                num_rows="dynamic",
                use_container_width=True,
                column_order=EDIT_COLUMNS,
                column_config={
                    "종목명": st.column_config.TextColumn(required=True, help="정확한 종목명을 입력하세요 (예: 삼성전자)"),
                    "섹터": st.column_config.TextColumn(),
                    "수량": st.column_config.NumberColumn(min_value=0, step=1),
                    "평단가": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f"),
                    "현재가": st.column_config.NumberColumn(min_value=0, step=100, format="%.0f"),
                },
                key="editor",
            )
            if st.button("저장", use_container_width=True, key="save_edit_btn"):
                save_holdings(edited)
                st.success("저장했습니다.")
                st.rerun()

    if refresh_clicked:
        with st.spinner("종목명으로 시세를 찾는 중..."):
            holdings = refresh_all_prices(holdings)
            df2, stock_val2, total_assets2, unreal2 = compute_metrics(holdings, state["cash"])
            adjusted2 = total_assets2 + unreal2
            snapshot_history(total_assets2, adjusted2)
        st.rerun()

    st.caption("시세는 네이버 금융 비공식 API 기준이며 지연/실패할 수 있습니다.")

# ==================================================================== #
# 탭 2: 거래 기록 + 자산 추이
# ==================================================================== #
with tab_tx:
    df3, stock_val3, total_assets3, unreal3 = compute_metrics(holdings, state["cash"])
    cap_return3 = total_assets3 - state["initial"]
    cap_return_pct3 = (cap_return3 / state["initial"] * 100) if state["initial"] else 0
    c3 = UP_COLOR if cap_return3 >= 0 else DOWN_COLOR
    s3 = "+" if cap_return3 >= 0 else ""

    st.markdown(f"""
    <div class="summary-box">
        <div class="summary-label">최초 자본 10,000,000원 대비</div>
        <span class="summary-main" style="color:{c3}">{s3}{cap_return3:,.0f}원</span>
        <span class="summary-sub" style="color:{c3}">{s3}{cap_return_pct3:.2f}%</span>
        <div class="summary-grid">
            <div>현재 총자산<b>{total_assets3:,.0f}원</b></div>
            <div>미실현 손실<b style="color:{DOWN_COLOR}">-{unreal3:,.0f}원</b></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ---- 자산 추이 그래프 ----
    st.markdown("##### 자산 추이")
    hist = load_history()
    if len(hist) >= 1:
        hist = hist.sort_values("날짜")
        profit_series = hist["총자산"] - state["initial"]       # 누적 수익금 (0에서 시작)
        loss_series = hist["조정자산"] - hist["총자산"]          # 미실현 손실 합계

        fig, ax = plt.subplots(figsize=(4.2, 2.6))
        fig.patch.set_alpha(0)
        ax.set_facecolor("none")
        x = range(len(hist))
        ax.plot(x, profit_series, color=UP_COLOR, linewidth=2.2, marker="o", markersize=3, label="수익금")
        ax.plot(x, loss_series, color=DOWN_COLOR, linewidth=2.2, marker="o", markersize=3, label="미실현손실")
        ax.axhline(0, color=T["muted2"], linewidth=1, linestyle="--")
        ax.set_xticks(list(x))
        ax.set_xticklabels([d[5:] for d in hist["날짜"]], fontsize=8, color=T["muted"])
        ax.tick_params(axis="y", labelsize=8, colors=T["muted"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(axis="y", color=T["border"], linewidth=0.6)
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        st.markdown(f"""
        <div class="legend-wrap">
            <div class="legend-item"><span class="legend-dot" style="background:{UP_COLOR}"></span>수익금(누적, 0부터)</div>
            <div class="legend-item"><span class="legend-dot" style="background:{DOWN_COLOR}"></span>미실현손실 합계</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("시세를 새로고침하면 그날의 자산 스냅샷이 하나씩 쌓여 그래프가 그려집니다.")

    st.divider()

    # ---- 새 거래 기록 ----
    st.markdown("##### 새 거래 기록하기")
    with st.form("tx_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            tx_date = st.date_input("날짜", value=date.today())
            tx_name = st.text_input("종목명", placeholder="예: 삼성전자")
        with c2:
            tx_kind = st.selectbox("구분", ["매수", "매도"])
            tx_qty = st.number_input("수량", min_value=0, step=1)
        tx_price = st.number_input("단가", min_value=0, step=100)
        tx_memo = st.text_input("메모 (선택)", placeholder="예: 분할매수 1차")
        submitted = st.form_submit_button("기록 저장", use_container_width=True)

    if submitted:
        if not tx_name or tx_qty <= 0 or tx_price <= 0:
            st.error("종목명, 수량, 단가를 정확히 입력해주세요.")
        else:
            holdings2, state2, realized = apply_transaction(holdings, state, tx_name.strip(), tx_kind, tx_qty, tx_price)
            new_row = {
                "id": str(uuid.uuid4())[:8], "날짜": tx_date.strftime("%Y-%m-%d"),
                "종목명": tx_name.strip(), "구분": tx_kind, "수량": tx_qty, "단가": tx_price,
                "실현손익": realized if realized is not None else "", "메모": tx_memo, "정산반영": True,
            }
            tx2 = pd.concat([tx, pd.DataFrame([new_row])], ignore_index=True)

            save_holdings(holdings2)
            save_state(state2)
            save_transactions(tx2)

            df4, val4, total4, unreal4 = compute_metrics(holdings2, state2["cash"])
            snapshot_history(total4, total4 + unreal4)

            msg = f"{tx_kind} 기록 완료: {tx_name} {tx_qty:.0f}주 @ {tx_price:,.0f}원"
            if realized is not None:
                msg += f" (실현손익 {realized:+,.0f}원)"
            st.success(msg)
            st.rerun()

    st.divider()

    # ---- 거래 내역 (캘린더) ----
    st.markdown("##### 거래 내역")

    if "cal_year" not in st.session_state:
        st.session_state.cal_year = now_kst().year
        st.session_state.cal_month = now_kst().month
    if "selected_tx_date" not in st.session_state:
        st.session_state.selected_tx_date = today_kst_str()

    tx_dates = set(tx["날짜"].astype(str))
    year, month = st.session_state.cal_year, st.session_state.cal_month

    nav1, nav2, nav3 = st.columns([1, 3, 1])
    with nav1:
        if st.button("◀", key="cal_prev", use_container_width=True):
            m, y = month - 1, year
            if m < 1:
                m, y = 12, y - 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()
    with nav2:
        st.markdown(
            f"<div style='text-align:center;font-weight:700;padding-top:6px;color:{T['text']}'>"
            f"{year}년 {month}월</div>",
            unsafe_allow_html=True,
        )
    with nav3:
        if st.button("▶", key="cal_next", use_container_width=True):
            m, y = month + 1, year
            if m > 12:
                m, y = 1, y + 1
            st.session_state.cal_month, st.session_state.cal_year = m, y
            st.rerun()

    last_day = calendar.monthrange(year, month)[1]
    day_options = list(range(1, last_day + 1))

    def _fmt_day(d):
        d_str = f"{year:04d}-{month:02d}-{d:02d}"
        return f"{d}●" if d_str in tx_dates else f"{d}"

    if st.session_state.selected_tx_date.startswith(f"{year:04d}-{month:02d}"):
        cur_day = int(st.session_state.selected_tx_date.split("-")[2])
    else:
        cur_day = min(now_kst().day, last_day) if (year, month) == (now_kst().year, now_kst().month) else 1
    if cur_day not in day_options:
        cur_day = 1

    chosen_day = st.radio(
        "날짜 선택", day_options, index=day_options.index(cur_day),
        horizontal=True, format_func=_fmt_day, label_visibility="collapsed",
        key=f"day_radio_{year}_{month}",
    )
    st.session_state.selected_tx_date = f"{year:04d}-{month:02d}-{chosen_day:02d}"

    st.divider()

    sel = st.session_state.selected_tx_date
    day_tx = tx[tx["날짜"].astype(str) == sel]
    day_realized = pd.to_numeric(day_tx.loc[day_tx["구분"] == "매도", "실현손익"], errors="coerce").sum()

    head_html = f"<b style='color:{T['text']}'>{sel}</b>"
    if day_realized:
        rc = UP_COLOR if day_realized >= 0 else DOWN_COLOR
        rs = "+" if day_realized >= 0 else ""
        head_html += f" <span style='color:{rc};font-size:13px'>({rs}{day_realized:,.0f}원)</span>"
    st.markdown(head_html, unsafe_allow_html=True)

    if day_tx.empty:
        st.info("이 날짜엔 기록된 거래가 없습니다.")
    else:
        for _, r in day_tx.iterrows():
            realized = r["실현손익"]
            right_html = ""
            if r["구분"] == "매도" and str(realized) not in ("", "nan"):
                rv = float(realized)
                rc = UP_COLOR if rv >= 0 else DOWN_COLOR
                rs = "+" if rv >= 0 else ""
                right_html = f'<span style="color:{rc}">{rs}{rv:,.0f}원</span>'
            memo_html = f' · {r["메모"]}' if str(r["메모"]) not in ("", "nan") else ""
            st.markdown(f"""
            <div class="tx-card">
                <div class="tx-left">
                    <span class="name">{r['종목명']}</span>
                    <span class="meta">{r['구분']} {float(r['수량']):.0f}주 @ {float(r['단가']):,.0f}원{memo_html}</span>
                </div>
                <div class="tx-right">{right_html}</div>
            </div>
            """, unsafe_allow_html=True)
