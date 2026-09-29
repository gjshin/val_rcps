#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Existing detailed UI. Shared calculation code lives in valuation.legacy."""
import streamlit as st
from hashlib import sha256
from valuation.legacy import *
from valuation.bridge import legacy_term_values

# Cache only at the UI boundary; the engine imports without Streamlit.
korean_font = st.cache_data(show_spinner=False)(korean_font)
fetch_close = st.cache_data(show_spinner=False, ttl=3600)(fetch_close)
fetch_splits = st.cache_data(show_spinner=False, ttl=3600)(fetch_splits)
fetch_prices = st.cache_data(show_spinner=False, ttl=3600)(fetch_prices)

if not st.session_state.get("_app_embedded"):
    st.set_page_config(page_title="복합금융상품 평가 — CB · BW · RCPS", layout="wide")
st.markdown("""<style>
.block-container{padding-top:2.2rem;max-width:1250px}
h1{font-size:1.7rem !important;letter-spacing:-.02em}
[data-testid="stMetricValue"]{font-size:1.9rem}
</style>""", unsafe_allow_html=True)

HLP_CMP = ("0 이면 **단리**입니다 — 「발행가에 연 X% 단리를 가산」 계약이 적지 않습니다. "
           "1 연복리 · 2 반기 · 4 분기.")
# 제목은 상품에 따라 갈리는데 상품은 입력화면에서 정해진다. 입력화면가 아래에서
# 그려지므로 자리만 잡아 두고 값이 정해진 뒤에 채운다 — 그러지 않으면 시나리오를
# 불러온 그 순간의 제목이 한 박자 늦는다.
_HEAD = st.empty()

# 새 평가의 기본값 — 한공회 본문이 콜 유형별로 「기본적인 접근법」을 따로 지정한다.
#   4.3.2  발행자 콜   → 콜조항 유무 가치 비교 (유무가치비교법)
#   4.3.4  제3자 콜    → 복합옵션 (옵션차익혼합할인법)
#   4.1.1  「실무적으로 많이 채택되고 있는 옵션차익혼합할인법을 기준으로 설명한다」
# 제3자 지정 가능 콜이 기본이므로 옵션차익 · TF식 지분-채권 분리할인(4.4.3 + 3.2)에
# 본문 4.3.3 의 전환확률 분해를 짝지어 둔다. dataclass 기본값은 건드리지 않으므로
# 옛 시나리오 JSON 은 저장된 대로 열린다.
if "tm" not in st.session_state:
    st.session_state.tm = Terms(k_method=2, k_split=1)
    # 첫 실행 — 예시 회사의 숫자가 남은 채 조서가 나가지 않게 아래 칸을 비워 둔다.
    st.session_state.blank = set(BLANK_FIELDS)
if "blank" not in st.session_state: st.session_state.blank = set()
# 이번 실행에서 화면에 그렸는데 비어 있는 칸의 이름. 계산 직전에 이 목록을 보고 멈춘다.
_MISS: list = []


def is_blank(f: str) -> bool:
    return f in st.session_state.blank


def any_blank(*fs) -> bool:
    return any(f in st.session_state.blank for f in fs)


def bval(f: str, v):
    """위젯에 줄 기본값 — 빈칸이면 None (칸이 비어 보인다)."""
    return None if f in st.session_state.blank else v


def bget(f: str, v, cur, label: str, scale: float = 1.0, need: bool = True):
    """위젯이 돌려준 값. None 이면 빈칸 — 내부값 cur 를 두고 빠진 칸으로 적는다.

    need=False 는 칸이 잠겨 쓰이지 않는 경우다(행사금액표가 산식을 이길 때 등). 비어 있어도
    계산을 막지 않는다. 값이 들어오면 빈칸 목록에서 지운다.
    """
    if v is None:
        st.session_state.blank.add(f)
        if need: _MISS.append(label)
        return cur
    st.session_state.blank.discard(f)
    return v if scale == 1 else v / scale


def bfill(*fs):
    """버튼(종가 불러오기·변동성 적용·역산)이 칸을 채웠다."""
    for f in fs: st.session_state.blank.discard(f)
if "prices" not in st.session_state: st.session_state.prices = []
if "peers" not in st.session_state: st.session_state.peers = []
if "rate_series" not in st.session_state: st.session_state.rate_series = []


def export_with_sources(data, terms):
    from valuation.case import import_legacy, FIELDS
    from valuation.bridge import apply_changes
    from valuation.evidence import attach_evidence
    shared = globals().get('_WORKSPACE_RUN')
    if shared is not None:
        return attach_evidence(data, shared.case)
    active = st.session_state.get('case')
    if active is None:
        active = import_legacy(asdict(terms), terms.tranche or '상세 기능 평가')
    else:
        # Bind the citations/review status to this export's actual inputs.
        changes = {k:v for k,v in asdict(terms).items() if k in FIELDS and active.effective().get(k) != v}
        active = apply_changes(active, changes, validate=False)
    return attach_evidence(data, active)


def holder_ui(t, full, b0, b1, b2, ca):
    """회계처리 탭 — 투자자 관점. 조서의 write_holder_sheet 와 같은 holder_rows 를 쓴다."""
    h = holder_rows(t, full, b0, b1, b2, ca)
    _F = t.face_total/100
    st.info(HOLDER_NOTE)
    st.markdown(f"**분류 — {h['title']}**")
    st.caption(h["basis"])
    st.markdown("### 평가기준일 공정가치")
    st.dataframe(pd.DataFrame([[k, v, v*_F] for k, v in h["pos"]],
                              columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
        {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
        use_container_width=True, hide_index=True)
    st.markdown("### 참고 — 구성요소 분해")
    st.dataframe(pd.DataFrame([[k, v, v*_F] for k, v in h["parts"]],
                              columns=["구성요소", "100 기준", "전액 기준 (원)"]).style.format(
        {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
        use_container_width=True, hide_index=True)
    st.caption("평가보고서에 주계약·전환권으로 나눠 싣는 표입니다. 투자자 회계에서는 나누지 않고 "
               "위 한 줄(복합계약 전체)로 측정합니다.")
    st.markdown(f"### 분개 — {holder_mode_text(h)}")
    if h["journal"]:
        st.dataframe(pd.DataFrame(
            [[acct, (v if side == "차변" else None), (v if side == "대변" else None), v*_F]
             for side, acct, v in h["journal"]],
            columns=["계정", "차변 (100)", "대변 (100)", "금액 (원)"]).style.format(
            {"차변 (100)": "{:,.4f}", "대변 (100)": "{:,.4f}", "금액 (원)": "{:,.0f}"}, na_rep=""),
            use_container_width=True, hide_index=True)
    if h["mode"] == "initial": st.caption(HOLDER_DAY1)
    st.caption(HOLDER_GROUP)


def vol_listed() -> bool:
    """변동성 칸의 「대상회사 — 상장사 / 비상장사」. 상장사면 대상회사 주가, 비상장사면 피어로 잰다.

    기본은 「기본」 칸에 종목코드가 있으면 상장사, 없으면 비상장사다.
    """
    return st.session_state.get("vol_listed", "상장사" if st.session_state.tm.ticker else "비상장사") == "상장사"


def vol_attach():
    """조서에 싣는 변동성 산출 자료 — (시계열 목록, 설정) 또는 None.

    고른 쪽 자료만 싣는다. 비상장인데 대상회사 칸에 예전 시계열이 남아 있거나, 상장사인데
    피어가 남아 있어도 조서에 섞이지 않는다.
    """
    if vol_listed():
        px = st.session_state.get("prices")
        return (([(st.session_state.get("px_src") or "대상회사", px)], st.session_state.get("vol_opt"))
                if px else None)
    pe = st.session_state.get("peers")
    return (pe, st.session_state.get("peer_opt")) if pe else None

# 위젯이 아니라 앱이 직접 관리하는 상태 — 시나리오를 불러와도 남긴다
_KEEP_STATE = {"tm", "prices", "peers", "scen", "scen_id", "rf_txt", "cr_txt", "ca_txt", "cb_txt",
               "kis_rows", "kis_src", "peer_txt", "px_src", "rate_how", "rate_opt", "rate_series",
               "report", "rvmode", "vol_opt", "peer_opt", "sched_mode", "sched_mode_prev", "scen_old",
               "blank", "_miss_prev"}
# 행사 시점 칸의 key — 날짜/개월 모드를 바꾸면 다른 모드의 저장값이 되살아나지 않게 비운다
_SCHED_KEYS = ["cvs", "cve", "ps", "pe", "ks", "ke", "klock", "ipom", "qipom",
               "shaps", "shape", "shacs", "shace"]
_SCHED_KEYS += [k + "_d" for k in _SCHED_KEYS] + ["p_none", "k_none", "shac_none"]


def reset_widgets(keys=None):
    """key 있는 위젯의 저장값을 지워 다음 실행에서 value= 기본값(Terms)으로 다시 그리게 한다."""
    for k in (list(st.session_state.keys()) if keys is None else keys):
        if keys is None and (k in _KEEP_STATE or k in st.session_state.get("_bridge_protected", set()) or k.startswith("_bridge") or k.startswith("_app")): continue
        if k in st.session_state: del st.session_state[k]


_shared_run = globals().get('_WORKSPACE_RUN')
if _shared_run is None:
    with st.sidebar:
        st.subheader("계약조건")

        up = st.file_uploader("시나리오 불러오기", type=["json"], key="scen")
        # 업로더는 지운 뒤에도 같은 파일을 계속 돌려준다. 실행마다 다시 읽으면
        # 그 뒤에 손으로 바꾼 값이 매번 되돌아가므로 한 번만 읽는다.
        _sid = (up.name, sha256(up.getvalue()).hexdigest()) if up is not None else None
        if up is not None and st.session_state.get("scen_id") != _sid:
            try:
                up.seek(0)
                o = json.load(up)
                st.session_state.tm = Terms(**legacy_term_values(o))
                st.session_state.blank = set()
                # 이자율 곡선은 아래 텍스트 칸이 실행마다 t.rf_curve·t.cr_curve 를
                # 통째로 덮어쓴다. 시나리오의 곡선을 그 칸에 직접 써 넣지 않으면
                # 화면 기본값(국고채·회사채 예시)으로 계산되어 버린다.
                _tm = st.session_state.tm
                def _fmt(pts):
                    return "\n".join(f"{float(x):g}\t{float(y)*100:.4f}%" for x, y in pts)
                if _tm.rf_curve: st.session_state.rf_txt = _fmt(_tm.rf_curve)
                if _tm.cr_curve:
                    st.session_state.cr_txt = _fmt(_tm.cr_curve)
                    st.session_state.ca_txt = _fmt(_tm.cr_curve)
                if _tm.cr_curve_b: st.session_state.cb_txt = _fmt(_tm.cr_curve_b)
                st.session_state.scen_id = _sid
                # 매도청구권 평가체계가 바뀌기 «전» 에 저장된 파일이면 알려 준다. 값은 저장된
                # 대로 연다 — 과거 조서의 재현성이 먼저다. 바꾸고 싶으면 버튼을 누른다.
                st.session_state.scen_old = (int(o.get("_schema", 1)) < SCHEMA_VER
                                             and float(o.get("k_w", 0) or 0) > 0)
                # 원본 시나리오 지문을 Terms 에 싣고 다닌다 — 조서에 「시나리오 지문」과
                # 「계산 지문」을 나란히 적기 위해서다. 두 지문이 다른 것은 정상이다:
                # derive() 가 경과기간·노드 수를 채우고 compat 가 지원하지 않는 조합을
                # 되돌리므로 계산에 들어간 Terms 는 파일과 같지 않다. 그래서 계산 지문이
                # 달라도 «불일치» 라고 부르지 않는다. 파일 자체가 손질됐는지만 가른다.
                _mo = o.get("_meta") or {}
                _now = scen_stamp(o)
                st.session_state.tm.scen_md5 = _now
                st.session_state.scen_meta = (
                    _mo if (_mo.get("scen_md5") and _mo["scen_md5"] != _now) else None)
                # key 가 있는 칸(조기상환·매도청구 시작/종료, 날짜 칸 …)은 한 번 그려지면 저장값이
                # value= 기본값을 이긴다. 비우지 않으면 시나리오의 값이 화면의 옛 값으로 되돌아간다.
                reset_widgets()
                st.success("불러왔습니다. 이자율 곡선도 함께 채웠습니다 (만기 단위 = 년).")
                st.rerun()
            except Exception as ex:
                st.error(f"읽지 못했습니다 — {ex}")
                st.stop()
        t = st.session_state.tm

        _sm = st.session_state.get("scen_meta")
        if _sm:
            st.warning(f"이 시나리오 **파일이 저장된 뒤에 손질되었습니다** "
                       f"(저장 당시 {str(_sm.get('scen_md5', ''))[:8]} · "
                       f"지금 {str(t.scen_md5)[:8]}). 저장한 앱 판 "
                       f"{_sm.get('app_sha12') or '?'} · 소스 {_sm.get('git_head') or '?'} · "
                       f"{_sm.get('made_at', '?')}. 값은 파일에 있는 대로 열었습니다.")
        elif t.scen_md5:
            st.caption(f"시나리오 지문 {t.scen_md5[:8]} · 계산 지문 {_stamp(t)[:8]} — "
                       "두 지문은 다른 것을 가리킵니다. 계산 지문은 경과기간·노드 수를 채우고 "
                       "지원하지 않는 조합을 되돌린 «실제로 계산에 쓴» 값의 지문이라 "
                       "파일과 같지 않은 것이 정상입니다.")

        if st.session_state.get("scen_old"):
            st.info("이 시나리오는 **매도청구권 평가체계를 정리하기 전**에 저장되었습니다. "
                    "값은 **저장된 대로** 열었습니다 — 과거 조서를 그대로 재현하기 위해서입니다. "
                    "현재 기본 방법(옵션차익 · TF식 지분-채권 분리할인 + 본문 4.3.3 전환확률 분해, "
                    "의무보유는 기간·조기상환 제한까지 반영)으로 바꾸려면 아래를 누르십시오. "
                    "**값이 크게 달라질 수 있습니다.**")
            if st.button("현재 기본 평가방법으로 전환", key="scen_upg", use_container_width=True):
                t.k_method, t.k_split = 2, 1
                st.session_state.scen_old = False
                reset_widgets()
                st.rerun()

        with st.expander("모형", expanded=True):
            _INSTS = ["CB", "BW", "RCPS", "SHA"]
            t.inst = st.selectbox(
                "상품", _INSTS,
                index=_INSTS.index(t.inst if t.inst in _INSTS else "CB"),
                format_func=lambda x: {"CB": "전환사채 (CB) · 액면 100 기준",
                                       "BW": "신주인수권부사채 (BW) · 액면 100 기준",
                                       "RCPS": "상환전환우선주 (RCPS) · 1주 발행가 100 기준",
                                       "SHA": "주주간계약 (SHA) · 투자원금 100 기준"}[x],
                help="격자·이자율·변동성·조서 기계는 같습니다. RCPS 를 고르면 우선배당·"
                     "존속기간 만료 처리·발행자 상환권·발행가 역산이 열립니다. 매도청구권은 "
                     "「발행자 상환권」으로 잡으면 유무가치비교법 한 갈래로 잠기고, "
                     "「제3자 지정 매도청구권」으로 잡으면 트랜치·콜옵션 유형·세 평가방법이 "
                     "모두 열립니다. BW 를 고르면 행사대금 "
                     "납입 방식과 신주인수권증권의 분리 여부가 열립니다. SHA 는 사채가 "
                     "없어 화면이 통째로 갈립니다 — 지분에 붙은 풋과 콜만 평가합니다.")
            if is_bw(t):
                # 행사대금을 무엇으로 내는가가 격자를 가른다. 대용납입이면 사채가
                # 소멸해 전환사채와 같아지고, 현금납입이면 사채가 남아 따로 잰다.
                t.bw_pay = st.selectbox(
                    "신주인수권 행사대금", [0, 1], index=int(t.bw_pay),
                    format_func=lambda i: ["현금납입 — 현금을 내고 사채는 남는다",
                                           "사채 대용납입 — 사채를 권면액만큼 납입에 갈음"][i],
                    help="대용납입이면 사채가 소멸하고 주식을 받으므로 전환사채와 "
                         "수학적으로 같습니다. 현금납입이면 사채와 신주인수권을 따로 "
                         "재어 더합니다.")
                if int(t.bw_pay) == 0:
                    t.bw_detach = st.selectbox(
                        "신주인수권증권", [0, 1], index=int(t.bw_detach),
                        format_func=lambda i: ["비분리형 — 사채가 소멸하면 함께 소멸",
                                               "분리형 — 사채와 따로 유통"][i],
                        help="분리형이면 사채를 조기상환받아도 신주인수권이 행사기간 "
                             "끝까지 남습니다. 비분리형이면 사채가 소멸할 때 미행사분이 "
                             "함께 사라집니다 — 상환 직전에 행사할 기회는 있습니다.")
            L = lbl(t)
            if is_rcps(t):
                st.caption("모든 금액은 **1주 발행가 = 100** 기준입니다. 전환가치는 "
                           "100 × 주가 ÷ 전환가격 — 전환가격이 발행가와 같으면 1 : 1 전환입니다.")
            if bw_cash(t):
                st.caption("현금납입형이라 **사채 + 신주인수권**으로 나누어 평가합니다. "
                           "신주인수권 행사가치는 100 × 주가 ÷ 행사가격 − 100 — "
                           "권면액 100 만큼 현금을 내고 그 값어치 주식을 받습니다. "
                           "지분과 부채가 애초에 갈라져 있어 TF 와 GS 가 같은 값을 냅니다.")
            if is_sha(t):
                # 사채가 없어 신용위험을 값에 쪼개 넣을 자리가 없고, 리픽싱도 없다.
                # 전환권 자체가 없으므로 회계 분류도 여기서 정하지 않는다.
                st.caption("주주간계약은 사채가 없어 **신용위험 처리(TF·GS)** 와 "
                           "**리픽싱 처리** 칸이 없습니다. 풋의 신용위험은 아래 "
                           "「의무자 · 할인율」 에서 정합니다.")
            else:
              t.model = st.selectbox("신용위험 처리", ["TF", "GS"],
                                   index=0 if t.model == "TF" else 1,
                                   format_func=lambda x: "TF · 값을 쪼갠다" if x == "TF" else "GS · 할인율을 섞는다")
              t.carry = st.selectbox("조정일 아닌 시점", [0, 1, 2, 3], index=t.carry,
                                   format_func=lambda i: ["상태확장 (정확)", "경로가중치",
                                                          "확률가중평균", "특정노드선택"][i])
              t.conv_class = st.selectbox(inst_text(t, "전환권 회계 분류"),
                                        ["equity", "liability"],
                                        index=0 if t.conv_class == "equity" else 1,
                                        format_func=lambda x: inst_text(
                                            t, "자본 · 전환권대가를 잔여로") if x == "equity"
                                        else "파생상품부채 · 주계약을 잔여로")
              st.caption("분류에 따라 무엇을 공정가치로 재고 무엇을 잔여로 두는지가 뒤바뀝니다.")
              # 평가 관점 — 공정가치는 같고 회계 단위가 갈린다. 기본은 발행자(종전 동작).
              t.view = st.radio(
                  "평가 관점", ["issuer", "holder"], index=(1 if t.view == "holder" else 0),
                  horizontal=True, key="view",
                  format_func=lambda x: "발행자" if x == "issuer" else "투자자",
                  help="**발행자** — 부채·자본을 가르고 주계약·파생상품·전환권대가로 배분합니다 "
                       "(제1032호 · 제1109호 문단 4.3.3). 상각표와 발행자 분개가 나옵니다.\n\n"
                       "**투자자** — 보유한 금융자산을 평가합니다. 주계약이 금융자산이라 내재파생을 "
                       "떼지 않고 복합계약 전체를 당기손익-공정가치로 측정합니다(문단 4.3.2). "
                       "매도청구권은 투자자가 써 준 권리라 차감되거나 파생상품부채가 됩니다.\n\n"
                       "**공정가치 자체는 같습니다** — 누가 들고 있든 시장참여자 사이의 "
                       "교환가격이기 때문입니다(제1113호). 갈리는 것은 회계처리입니다.")
              if t.view == "holder":
                  st.caption("투자자 관점 — 회계처리 탭과 조서의 「회계처리」 시트가 투자자 분개로 "
                             "바뀌고 상각표는 만들지 않습니다. 평가값은 그대로입니다. 위 "
                             "「전환권 회계 분류」는 발행자의 분류라 투자자 분개에는 쓰이지 않지만 "
                             "평가방법(TF·GS 할인)은 그대로 따릅니다.")
            # 평가에서 뺀 권리를 적는 자리. 매도청구권 상자 안의 「평가기법 선택 근거」는
            # 매도청구권이 없는 계약에서는 열리지도 않아, 조건부 풋·청산우선권 같은 판단이
            # 조서 어디에도 남지 않았다. 상품과 무관하게 늘 보인다. 계산에는 쓰지 않는다.
            t.unmod_note = st.text_area(
                "이 계약에서 평가에 반영하지 않은 권리 (조서 표지)", value=t.unmod_note,
                height=90, key="unmod_note",
                placeholder="주식매수청구권(신주인수계약 제11조) — 진술보장 중대 위반 시에만 "
                            "행사하는 조건부 권리라 격자에 넣지 않음\n"
                            "잔여재산 분배 우선권(별첨2 제3조) — 청산 시나리오 확률 필요, 범위 밖",
                help="계약에 있지만 평가에서 뺀 권리와 그 이유를 한 줄에 하나씩 적으십시오. "
                     "세 조서(화면·값·수식)의 표지에 「이 계약에서 반영하지 않은 권리」 절로 "
                     "실립니다. 비워 두면 그 절을 그리지 않습니다.")

        with st.expander("날짜 · 기간", expanded=True):
            c1, c2 = st.columns(2)
            _v = c1.date_input("발행일", value=bval("d_issue", dt.date.fromisoformat(t.d_issue)))
            t.d_issue = bget("d_issue", _v and _v.isoformat(), t.d_issue, "발행일")
            _v = c2.date_input("만기일", value=bval("d_mat", dt.date.fromisoformat(t.d_mat)))
            t.d_mat = bget("d_mat", _v and _v.isoformat(), t.d_mat, "만기일")
            t.tranche = st.text_input(
                "회차 표시 (선택)", value=t.tranche, key="tranche",
                placeholder="1차 납입분 84,189주",
                help="분할납입이면 **회차마다 따로 평가해 합산하십시오** — 존속기간·행사 개시일이 "
                     "각 납입일부터 세어지기 때문입니다. 이 칸은 어느 회차인지 **표시만** "
                     "합니다(계산에 쓰지 않음). 조서 표지·재현 기록·파일 이름에 들어갑니다.")
            _v = st.date_input("평가기준일", value=bval("d_base", dt.date.fromisoformat(t.d_base)),
                               help="발행일과 같으면 최초 인식, 뒤면 후속 측정입니다.")
            t.d_base = bget("d_base", _v and _v.isoformat(), t.d_base, "평가기준일")
            gaps = {"월": 1.0, "2주": 12/26, "주": 12/52}
            if all(abs(t.gap_m-v) > 1e-10 for v in gaps.values()):
                gaps[f"입력 간격 ({t.gap_m:g}개월)"] = t.gap_m
            gname = min(gaps, key=lambda k: abs(gaps[k]-t.gap_m))
            gname = st.selectbox("노드 간격", list(gaps), index=list(gaps).index(gname))
            t.gap_m = gaps[gname]
            derive(t)
            _DATE_BLANK = any_blank("d_issue", "d_mat", "d_base")
            if _DATE_BLANK:
                st.caption("세 날짜를 넣으면 경과·잔존기간과 노드 수가 나옵니다.")
            else:
                c3, c4, c5 = st.columns(3)
                c3.metric("경과", f"{t.elapsed_m:.1f}개월")
                c4.metric("잔존", f"{t.T:.2f}년")
                c5.metric("노드", f"{t.n}")
            st.session_state.sched_mode = st.radio(
                "행사 시점 입력 방식", ["날짜", "발행일 기준 개월"], horizontal=True,
                index=0 if st.session_state.get("sched_mode", "날짜") == "날짜" else 1,
                help="계약서에 날짜로 쓰여 있으면 날짜로 넣으십시오. 앱이 발행일 기준 개월로 바꿔 "
                     "저장하므로 시나리오 파일·조서는 어느 쪽으로 넣어도 같습니다.")
            if st.session_state.sched_mode == "날짜":
                st.caption("행사 시작·종료를 **날짜**로 넣으십시오. 발행일 기준 개월로 바꿔 옆에 보여 "
                           "줍니다. 주기는 개월 그대로입니다.")
            else:
                st.caption("행사 시점은 아래에서 **발행일 기준 개월**로 넣으십시오. "
                           "앱이 평가기준일 기준으로 옮기고, 행사금액도 발행일부터 복리로 붙입니다.")

        # 날짜 모드와 개월 모드가 같은 자리에서 같은 Terms 개월 값을 만든다. 날짜 위젯의 key 는
        # 개월 위젯과 다르게(_d) 두어 서로의 저장값을 건드리지 않는다.
        _DATE_MODE = st.session_state.sched_mode == "날짜"
        if st.session_state.get("sched_mode_prev", st.session_state.sched_mode) != st.session_state.sched_mode:
            reset_widgets(_SCHED_KEYS); st.session_state.sched_mode_prev = st.session_state.sched_mode; st.rerun()
        st.session_state.sched_mode_prev = st.session_state.sched_mode

        def exercise_mode_ui(s_, e_, f_, key, gap_m, hide=False):
            """행사 방식 — 특정일 1회 / 정기 / 기간 중 언제든지. ``(종료, 주기)`` 를 돌려준다.

            조기상환에만 있던 것을 매도청구도 함께 쓴다. 「기간 중 언제든지」는 주기를 노드
            간격으로 맞추면 그 구간의 모든 노드가 열린다 — **엔진은 이미 할 수 있고**
            화면에서 고를 수 없었을 뿐이다.
            """
            _m = st.radio("행사 방식", ["특정일 1회", "정기", "기간 중 언제든지"],
                          index=(0 if s_ >= e_ - 1e-9 else
                                 (2 if f_ <= gap_m + 1e-9 else 1)),
                          horizontal=True, key=key, help="계약이 정한 행사 방식입니다.\n\n**특정일 1회** — 시작일 하나에만 행사할 수 있습니다(종료일을 시작일로 맞춥니다).\n\n**정기** — 「6개월이 되는 날 및 이후 매 3개월」처럼 주기가 있습니다. 아래 주기 칸을 씁니다.\n\n**기간 중 언제든지** — 주기 없이 행사기간 내내 행사할 수 있습니다. 주기를 노드 간격으로 맞춰 모든 노드에서 행사 가능하게 합니다 — 노드가 촘촘할수록 정확합니다.")
            if _m == "특정일 1회":
                if not hide: st.caption(f"발행일 기준 {s_:,.0f}개월 하루만 행사할 수 있습니다.")
                return s_, max(1.0, float(f_))
            if _m == "기간 중 언제든지":
                st.caption(f"주기를 노드 간격({gap_m:g}개월)으로 맞췄습니다 — 행사기간의 "
                           "모든 노드에서 행사할 수 있습니다.")
                return e_, gap_m
            return e_, st.number_input("주기 (개월)", value=float(f_), step=1.0,
                                       key=key + "_f")

        def ded_ui(which, g, m, first_mo, key, has_tbl=False, when="첫 행사일", hide=False):
            """이미 지급한 이자·배당을 행사금액에서 어떻게 빼는가 — 1 / 2 / 0 을 돌려준다.

            세 방식의 금액을 한 줄로 나란히 보여 계약서의 예시 금액과 바로 대조하게 한다.
            계산에 쓰는 지급률이 0 이면 세 방식이 같으므로 잠근다. 표가 있으면 금액은 표가
            정하지만, 표가 암시하는 보장수익률은 이 선택에 따라 달라지므로 열어 둔다.
            """
            cur = ded_of(t, which)
            _c = eff_cpn(t)
            _v = int(st.radio("이미 지급한 이자·배당은", [1, 2, 0], index=[1, 2, 0].index(cur),
                              format_func=lambda x: DED_LBL[x], key=key, horizontal=True,
                              help=DED_HELP, disabled=(_c <= 0)))
            if _c <= 0:
                st.caption("계산에 쓰는 이자·배당이 0 이라 세 공제 방식이 같습니다.")
                return cur
            if has_tbl:
                st.caption("행사금액표가 금액을 정합니다 — 이 선택은 표가 암시하는 보장수익률을 "
                           "역산할 때만 씁니다.")
            elif first_mo > 0 and g > 0 and not hide:
                _yr = yr_of_month(t, first_mo)
                _a = [(d, 100*(1 + ded_prem(_yr, g, _c, m, d, first_mo, t.ipay))) for d in (1, 2, 0)]
                st.caption(f"{when}({months_to_date(t.d_issue, first_mo)}) 기준 — "
                           + " · ".join(f"{DED_LBL[d]} **{a:,.4f}**" for d, a in _a)
                           + f"  (받은 지급 {paid_count(first_mo, t.ipay)}회)")
            return _v

        def _sched_one(col, lab_m, lab_d, m, key, **kw):
            """한 시점. 날짜 모드면 date_input, 아니면 number_input. 개월을 돌려준다."""
            if not _DATE_MODE:
                return col.number_input(lab_m, value=float(m), step=1.0, key=key, **kw)
            if is_blank("d_issue"):
                # 자리만 보여 준다 — 진짜 칸과 key 를 달리 두어야 발행일을 넣은 뒤 빈 저장값이 남지 않는다
                col.date_input(lab_d, value=None, key=key + "_dwait", disabled=True)
                col.caption("발행일을 먼저 넣으십시오.")
                return m
            kw.pop("min_value", None)
            d = col.date_input(lab_d, value=months_to_date(t.d_issue, m), key=key + "_d",
                               help=kw.get("help"), disabled=kw.get("disabled", False))
            if d is None: return m
            mm = date_to_months(t.d_issue, d)
            col.caption(f"발행일 기준 {mm:,.1f}개월")
            return mm

        def _sched_pair(cs, ce, m_s, m_e, key, none_lab=None, lab=("시작", "종료"), fields=None):
            """시작·종료 한 쌍. none_lab 이 있으면 「권리 없음」 체크박스로 시작>종료 관례를 대신한다.

            fields=(시작, 종료) 의 Terms 이름을 주면 첫 실행 빈칸을 따른다 — 비어 있으면 계산을 막는다.
            """
            fs, fe = fields or (None, None)
            _ls, _le = (f"{lab[0]} ({'개월' if not _DATE_MODE else '일'})",
                        f"{lab[1]} ({'개월' if not _DATE_MODE else '일'})")
            def _nm(x):         # 빠진 칸 목록에 적을 이름 — 어느 권리의 시작·종료인지까지
                return f"{none_lab and key_lab.get(key, '') or ''}{x}"
            if not _DATE_MODE:
                a = cs.number_input(f"{lab[0]} (개월)", value=(bval(fs, float(m_s)) if fs else float(m_s)),
                                    step=1.0, key=key + "s")
                b = ce.number_input(f"{lab[1]} (개월)", value=(bval(fe, float(m_e)) if fe else float(m_e)),
                                    step=1.0, key=key + "e")
                if fs:
                    a = bget(fs, a, m_s, _nm(lab[0])); b = bget(fe, b, m_e, _nm(lab[1]))
                return a, b
            if none_lab:
                if st.checkbox(none_lab, value=bool(m_s > m_e) and not (fs and is_blank(fs)),
                               key=key + "_none"):
                    if fs: bfill(fs, fe)
                    return (99.0 if m_s <= m_e else m_s), (0.0 if m_s <= m_e else m_e)
                if m_s > m_e: m_s, m_e = 12.0, max(12.0, t.T*12 + t.elapsed_m - 1)
            if is_blank("d_issue"):
                cs.date_input(f"{lab[0]}일", value=None, key=key + "s_dwait", disabled=True)
                ce.date_input(f"{lab[1]}일", value=None, key=key + "e_dwait", disabled=True)
                st.caption("발행일을 먼저 넣으십시오 — 행사 시점은 발행일부터 센 개월로 저장합니다.")
                if fs:
                    for f, x in ((fs, lab[0]), (fe, lab[1])):
                        if is_blank(f): _MISS.append(_nm(x))
                return m_s, m_e
            ds = cs.date_input(f"{lab[0]}일", value=(bval(fs, months_to_date(t.d_issue, m_s)) if fs
                                                     else months_to_date(t.d_issue, m_s)), key=key + "s_d")
            de = ce.date_input(f"{lab[1]}일", value=(bval(fe, months_to_date(t.d_issue, m_e)) if fe
                                                     else months_to_date(t.d_issue, m_e)), key=key + "e_d")
            a = date_to_months(t.d_issue, ds) if ds is not None else None
            b = date_to_months(t.d_issue, de) if de is not None else None
            if fs:
                a = bget(fs, a, m_s, _nm(lab[0])); b = bget(fe, b, m_e, _nm(lab[1]))
            if ds is not None and de is not None:
                st.caption(f"발행일 기준 {a:,.1f} ~ {b:,.1f}개월")
            return a, b

        key_lab = {"p": "조기상환 ", "k": "매도청구 "}

        with st.expander("기본", expanded=True):
            _SHA = is_sha(t)
            # 상장사면 평가기준일(또는 직전 거래일) 종가를 받아 넣는다. 비상장이면 빈칸으로 두고
            # 손으로 넣거나 아래 역산을 쓴다. 출처는 조서 가정 시트에 같이 실린다.
            tk1, tk2, tk3 = st.columns([2, 1, 2])
            t.ticker = tk1.text_input("종목코드 · 티커", value=t.ticker,
                                      help="국내는 6자리 숫자, 해외는 티커. 비상장이면 비워 두십시오.").strip()
            _mkt = tk2.selectbox("시장", ["KQ", "KS", ""], index=0,
                                 format_func=lambda x: {"KQ": "코스닥", "KS": "코스피", "": "해외"}[x],
                                 key="s0_mkt")
            if tk3.button("평가기준일 종가 불러오기", use_container_width=True,
                          disabled=(not t.ticker or is_blank("d_base")), key="btn_s0",
                          help=("평가기준일을 먼저 넣으십시오." if is_blank("d_base") else None)):
                with st.spinner("받는 중"):
                    try:
                        _dd, _px, _sym, _adj = fetch_close(t.ticker, _mkt, t.d_base)
                        t.S0 = float(_px); bfill("S0")
                        t.s0_src = f"야후 {_sym} {_dd} 종가"
                        t.s0_date, t.s0_raw = _dd, float(_px)
                        t.s0_adj = float(_adj) if _adj is not None else -1.0
                        # 분할 이력은 «참고» 다 — 발행일부터 평가기준일까지 조회한다.
                        _sp = fetch_splits(t.ticker, _mkt, t.d_issue, t.d_base)
                        t.s0_splits = ("" if _sp is None else
                                       ("없음" if not _sp else
                                        " · ".join(f"{d} {r:g}배" for d, r in _sp)))
                        st.rerun()
                    except Exception as ex:
                        st.warning(f"받지 못했습니다 — {ex}. 주가를 직접 넣으십시오.")
            _s0_before = float(t.S0)
            _v = st.number_input("평가기준일 주가 (원)", value=bval("S0", float(t.S0)), step=1.0,
                                   help=("비상장이면 지분가치 평가액 ÷ 주식수를 넣거나, 아래에서 "
                                         "투자원금으로 역산하십시오." if _SHA else
                                         "비상장이면 별도 지분평가액 ÷ 주식수를 넣거나, 아래에서 "
                                         "발행가로 역산하십시오. 이자부부채에서 이 증권을 빼고 "
                                         "보통주식수로 나눈 값은 **희석 전** 입니다 — 이 도구는 "
                                         "전환 희석을 스스로 반영하지 않습니다."))
            t.S0 = bget("S0", _v, t.S0, "평가기준일 주가")
            if abs(t.S0 - _s0_before) > 1e-9:
                # 손으로 고쳤다 — 출처도 조회 기록도 더 이상 이 값의 근거가 아니다
                t.s0_src = t.s0_date = t.s0_splits = ""
                t.s0_raw = t.s0_adj = -1.0
            if not _SHA:
                # 희석 확인 — 계산에는 쓰지 않는다. 전환 시 늘어나는 주식이 많으면 경고한다.
                _d1, _d2 = st.columns(2)
                t.base_shares = float(_d1.number_input(
                    "보통주식수 (희석 확인용)", value=float(t.base_shares), step=1000.0,
                    min_value=0.0, format="%.0f", key="base_sh",
                    help="평가기준일 발행 보통주식수. 계산에는 쓰지 않고 희석 경고에만 씁니다."))
                t.dil_shares = float(_d2.number_input(
                    "전환 시 늘어나는 주식수", value=float(t.dil_shares), step=1000.0,
                    min_value=0.0, format="%.0f", key="dil_sh",
                    help="이 증권과 다른 전환증권이 모두 전환될 때 새로 생기는 보통주식수."))
                if t.base_shares > 0 and t.dil_shares / t.base_shares > DIL_WARN:
                    st.warning(dil_msg(t))
            if t.s0_src:
                st.caption(f"출처 · {t.s0_src}")
                # 조회 기록을 그대로 보여 준다 — 「무엇을 요청했고 무엇을 받았는가」.
                st.caption(f"요청 평가기준일 {t.d_base} · 실제 사용 거래일 {t.s0_date or '?'}")
                if t.s0_date and t.s0_date != t.d_base:
                    st.caption(f"평가기준일 {t.d_base} 은 휴장일이라 직전 거래일 종가입니다.")
                if t.s0_raw > 0:
                    _adjtxt = (f"{t.s0_adj:,.0f} 원" if t.s0_adj > 0 else "받지 못함")
                    st.caption(f"원주가 {t.s0_raw:,.0f} 원 · 수정주가 {_adjtxt}")
                    if t.s0_adj > 0 and abs(t.s0_adj - t.s0_raw) > 0.005 * max(1.0, t.s0_raw):
                        st.warning(f"원주가와 수정주가가 다릅니다 ({t.s0_raw:,.0f} → "
                                   f"{t.s0_adj:,.0f}). 조회일 뒤에 분할·병합·무상증자가 있었을 "
                                   f"수 있습니다. 전환가액이 같은 기준인지 확인하십시오.")
                st.caption("분할 기록 · " + (t.s0_splits or "조회하지 못함 — 확인 못 함"))
                st.caption("한국 종목은 **무상증자가 야후에 분할로 기록되지 않는 경우가 많습니다.** "
                           "「없음」이 「사건이 없었다」는 뜻은 아니니 공시로 확인하십시오.")
            # 발행가 역산 (책 5-1). 비상장 발행회사는 관측 주가가 없으니 「발행된 값이
            # 곧 공정가치」로 놓고 전체 가치가 발행가가 되는 주가를 격자에서 찾는다.
            bc1, bc2 = st.columns([1, 1])
            t.bs_target = bc1.number_input(f"역산 목표 ({L['face']} 100 기준)",
                                           value=float(t.bs_target), step=1.0,
                                           help="발행 시점 평가면 100. 할인·할증 발행이면 그 값.")
            # 무엇을 목표에 맞출 것인가. 매도청구권은 격자 밖에서 따로 재어 차감하는
            # 파생상품자산이라, 본체만 맞추면 투자자가 실제로 받은 순액은 목표에
            # 못 미친다 — 돈을 내면서 매도청구권까지 써 주었기 때문이다.
            if not _SHA and t.k_w > 0:
                t.bs_net = int(st.selectbox(
                    "역산 목표를 무엇에 맞추나", [0, 1], index=int(t.bs_net),
                    format_func=lambda x: (
                        f"본체 — {L['call']}을 빼기 전 값" if x == 0
                        else f"순액 — 본체 − {L['call']}"),
                    help="발행가가 패키지 전체의 대가라면 「순액」 입니다. 투자자는 돈을 "
                         "내면서 매도청구권까지 써 주었으므로, 실제로 받은 것은 그 차감 "
                         "후 순액이기 때문입니다. 매도청구권에 별도 대가가 오갔거나 "
                         "최초인식 차이를 따로 보고 있다면 「본체」 입니다. 매도청구권이 "
                         "클수록 두 답이 벌어집니다."))
            _bs_block = [x for x in st.session_state.get("_miss_prev", []) if x != "평가기준일 주가"]
            if bc2.button(("투자원금으로 지분 역산" if _SHA else "발행가로 주가 역산"),
                          use_container_width=True, disabled=bool(_bs_block),
                          help=("«지분가치 + 풋 − 콜» 이 목표와 같아지는 주가를 이분법으로 "
                                "찾아 위 칸에 넣습니다." if _SHA else
                                "전체 가치(B2, 발행자 상환권이 있으면 B3)가 목표와 같아지는 "
                                "주가를 이분법으로 찾아 위 칸에 넣습니다 (책 [사례 5-1])。")):
                with st.spinner("격자를 되풀이 계산합니다"):
                    _S, _v, _k = (sha_backsolve(t, t.bs_target) if _SHA else backsolve(t))
                if _k < 0 and _SHA:
                    st.error(f"격자가 목표에 닿지 않습니다 (주가 {_S:,.0f}원에서 {_v:,.2f}). "
                             "**풋 자체가 투자원금보다 클 수 있습니다** — 보장수익률이 붙은 "
                             "풋은 주가가 아무리 낮아도 행사금액의 현재가치만큼 값이 남기 "
                             "때문입니다. 그때는 역산이 성립하지 않으므로 지분가치를 직접 "
                             "넣으시고, 풋 할인율에 의무자의 신용을 반영하셨는지 보십시오.")
                elif _k < 0:
                    st.error(f"격자가 목표에 닿지 않습니다 (주가 {_S:,.0f}원에서 {_v:,.2f}). "
                             "전환가액·변동성·상환 조건을 확인하십시오.")
                else:
                    t.S0 = float(_S); bfill("S0")
                    # 격자 값은 주가에 대해 연속이 아니다. 어느 노드의 결정이
                    # 뒤집히는 자리에서 계단처럼 튀므로 이분법이 목표를 정확히
                    # 맞히지 못할 수 있다. 얼마나 못 맞혔는지는 말해 주어야 한다.
                    if abs(_v - t.bs_target) > 0.05:
                        st.warning(
                            f"역산이 목표 {t.bs_target:,.2f} 에 **{_v:,.4f}** 로 "
                            f"멈췄습니다 (차이 {_v - t.bs_target:+,.4f}). 격자 값은 "
                            "주가에 대해 연속이 아니라 어느 노드의 결정이 뒤집히는 "
                            "자리에서 계단처럼 뜁니다 — 이분법이 그 계단을 넘을 수 "
                            "없어서 생기는 한계이지 오류가 아닙니다. 노드를 촘촘히 "
                            "하면 계단이 잘아집니다.")
                    _tg = ("순액" if (not _SHA and t.k_w > 0 and int(t.bs_net))
                           else "본체")
                    st.session_state.px_src = (
                        f"발행가 역산 (목표 {t.bs_target:,.2f})" if _SHA else
                        f"발행가 역산 (목표 {t.bs_target:,.2f} · {_tg} 기준)")
                    st.rerun()
            if _bs_block:
                st.caption("역산은 주가를 뺀 칸을 모두 채운 뒤에 쓸 수 있습니다.")
            if (st.session_state.get("px_src") or "").startswith("발행가 역산") and not is_blank("S0"):
                st.success(f"주가 {t.S0:,.2f}원 — {st.session_state.px_src}. 조서에 "
                           "그대로 적힙니다. 기말 재평가에는 쓰지 마십시오 — 그때는 "
                           "발행가가 기준이 아닙니다.")
            _lk0 = ("주당 인수가액 (원)" if _SHA else inst_text(t, "현재 전환가액 (원)"))
            _v = st.number_input(
                _lk0, value=bval("K0", float(t.K0)), step=1.0,
                help=("투자자가 1주에 낸 금액입니다. 지분가치 = 100 × 주가 ÷ 이 값 이라, "
                      "평가기준일 주가가 이 값과 같으면 지분가치가 100 입니다." if _SHA else
                      "리픽싱이 이미 일어났으면 조정된 값을 넣으십시오."))
            t.K0 = bget("K0", _v, t.K0, _lk0.replace(" (원)", ""))
            if _SHA:
                _v = st.number_input(
                    "투자원금 총액 (원)", value=bval("face_total", float(t.face_total)), step=1e8,
                    format="%.0f", help="화면과 조서의 전액 기준 금액을 계산합니다.")
                t.face_total = bget("face_total", _v, t.face_total, "투자원금 총액")
                st.caption("주주간계약에는 사채가 없어 표면이자·만기상환금액·거래원가 칸이 "
                           "없습니다. 아래 「주주간계약」 칸에서 풋과 콜을 넣으십시오.")
            # 주주간계약에는 사채가 없다. 표면이자·만기상환·거래원가 칸을 뺀다.
            if not _SHA:
                _v = st.number_input(f"{L['cpn']} (%)", value=bval("cpn", t.cpn*100), step=0.5,
                                     help=("확정 배당률을 표면이자처럼 현금흐름으로 봅니다. "
                                           "배당가능이익이 없어 지급 가능성이 없다고 보시면 0. "
                                           "계약서 숫자를 그대로 넣고 아래에서 기준을 고르십시오."
                                           if is_rcps(t) else "없으면 0 을 넣으십시오."))
                t.cpn = bget("cpn", _v, t.cpn, L["cpn"], scale=100)
                if is_rcps(t):
                    # 격자는 1주 발행가를 100 으로 잰다. 계약이 액면가 기준이면 옮겨야 한다.
                    t.div_basis = int(st.radio(
                        "배당률 기준", [0, 1], index=int(t.div_basis), horizontal=True,
                        key="dbas",
                        format_func=lambda x: "발행가 기준" if x == 0 else "액면가 기준",
                        help="계약서가 「1주당 **액면가액** 기준 연 1%」 라고 쓰면 액면가 기준입니다. "
                             "이 도구는 1주 발행가를 100 으로 재므로 액면 기준 배당률을 "
                             "「× 액면가 ÷ 발행가」 로 옮겨 씁니다. 그대로 넣으면 배당이 "
                             "발행가 ÷ 액면가 배로 부풀고 상환금액까지 내려갑니다."))
                    # 액면가 칸은 아래 「전환가액 조정」에 있다 — 칸을 둘로 만들지 않고 그 값을 읽는다.
                    _par_now = st.session_state.get("par_in", None if is_blank("par") else t.par)
                    _par_now = float(_par_now) if _par_now is not None else 0.0
                    _ipx = st.number_input(
                        "1주당 발행가 (원)",
                        value=(float(t.issue_px) if t.issue_px > 0 else bval("K0", float(t.K0))),
                        step=100.0, min_value=0.0, key="ipx", disabled=(t.div_basis == 0),
                        help="보통 1주당 인수금액입니다. 발행가 기준이면 쓰지 않습니다.")
                    if t.div_basis == 1:
                        t.issue_px = float(_ipx or 0.0)
                        if is_blank("cpn"):
                            pass
                        elif t.issue_px > 0 and _par_now > 0:
                            st.caption(f"→ 액면 {_par_now:,.0f}원 기준 연 {t.cpn:.2%} = "
                                       f"**발행가 {t.issue_px:,.0f}원 기준 연 "
                                       f"{t.cpn*_par_now/t.issue_px:.4%}** ← 계산에 쓰는 값.  "
                                       "액면가는 아래 「전환가액 조정」의 액면가 칸 값입니다.")
                        else:
                            st.error("액면가와 1주당 발행가를 모두 넣어야 환산할 수 있습니다. "
                                     "비어 있으면 **발행가 기준으로 되돌려** 계산합니다.")
                    else:
                        st.caption("발행가 기준 — 넣은 배당률을 그대로 씁니다.")
                t.ipay = st.number_input(f"{L['ipay']} (개월)", value=float(t.ipay), step=1.0)
                if is_rcps(t):
                    t.div_mode = st.selectbox(
                        "우선배당의 성격", [0, 1], index=int(t.div_mode),
                        format_func=lambda x: ("미지급분을 상환가액에 가산 — 전체 부채 · 배당은 이자비용"
                                               if x == 0 else
                                               "발행자 재량 · 상환가액과 무관 — 부채 현금흐름에서 제외"),
                        help="기준서 1032 AG37. 지급되지 않은 배당을 상환금액에 가산하면 금융상품 "
                             "전체가 부채이고 배당은 이자비용입니다 (보장수익률 − 배당률 산식이 이 "
                             "경우). 배당이 발행자 재량이고 상환가액과 무관하면 배당은 자본요소의 "
                             "이익분배라 부채 계산에서 뺍니다.")
                    if t.div_mode == 1 and t.cpn > 0 and not is_blank("cpn"):
                        st.caption(f"배당률 {t.cpn:.2%} 는 조서에 계약 조건으로 남고, 격자와 상환가액 "
                                   "산식에는 **0** 으로 들어갑니다. 회계처리에서 배당은 이익잉여금의 "
                                   "처분입니다.")
                    t.mat_mode = st.selectbox(
                        "존속기간 만료 시", [0, 1], index=int(t.mat_mode),
                        format_func=lambda x: ("보통주로 자동전환" if x == 0
                                               else f"{L['face']}(+보장수익률)로 상환"),
                        help="상법상 우선주 존속기간이 끝나면 보통주가 되는 계약이 표준입니다. "
                             "만료 시 현금 상환을 받는 계약이면 두 번째를 고르십시오.")
                    if t.mat_mode == 0:
                        st.caption("자동전환은 **전환권이 있는 격자**에서만 탑니다. 전환권을 뺀 "
                                   "부채요소는 보통주가 될 수 없으니 아래 보장수익률로 상환받는 "
                                   "것으로 잽니다 — 그래서 이 값이 여전히 필요합니다.")
                # 만기금액을 계약서 숫자로 직접 넣으면 아래 보장수익률 산식은 쓰이지 않는다.
                # 칸을 그리기 «전» 에 직전 실행의 체크 상태를 읽어 잠근다.
                _mlk = bool(st.session_state.get("matfix", float(t.mat_amt) > 0))
                _v = st.number_input(f"{L['ytm']} (%)", value=bval("ytm", t.ytm*100), step=0.1,
                                     format="%.4f", disabled=_mlk,
                                     help="없으면 0 을 넣으십시오.")
                t.ytm = bget("ytm", _v, t.ytm, L["ytm"], scale=100, need=not _mlk)
                t.ytm_cmp = int(st.number_input("보장 복리 횟수 (연)", value=int(t.ytm_cmp),
                                                step=1, min_value=0, max_value=12,
                                                help="공시 상환율이 분기복리면 4, 반기면 2. " + HLP_CMP,
                                                disabled=_mlk))
                t.m_less_cpn = ded_ui("m", t.ytm, t.ytm_cmp, t.elapsed_m + t.rem_m, "mless_ui",
                                      has_tbl=_mlk, when="만기일",
                                      hide=any_blank("ytm", "cpn", "d_issue", "d_mat", "d_base"))
                _mfix = st.checkbox("만기상환금액을 계약서 숫자로 직접 넣는다",
                                    value=(float(t.mat_amt) > 0), key="matfix",
                                    help="공시 「원금상환방법」에 「만기에 106.4302% 를 일시 상환한다」처럼 "
                                         "확정 숫자가 실린 계약이 많습니다. 그때는 이 칸에 그 숫자를 "
                                         "그대로 넣으십시오 — 보장수익률 산식으로 되돌려 계산하면 "
                                         "소수점 아래가 어긋납니다. 끄면 위 보장수익률로 계산합니다.")
                if _mfix:
                    t.mat_amt = st.number_input("만기상환금액 (%)",
                                                value=(float(t.mat_amt) if float(t.mat_amt) > 0
                                                       else 100.0),
                                                step=0.1, format="%.4f", key="matamt")
                else:
                    t.mat_amt = -1.0
                if _mlk and float(t.mat_amt) > 0:
                    # 계약서 숫자를 그대로 쓴다 — 위 산식 칸은 잠갔다. 그 숫자가 계약서의
                    # 몇 %를 뜻하는지 역산해 함께 적는다.
                    st.caption("**만기상환금액을 계약서 숫자로 씁니다** — 위 보장수익률 칸은 "
                               "계산에 쓰지 않습니다. 체크를 끄면 다시 열립니다.")
                    _my = (None if any_blank("cpn", "d_issue", "d_mat", "d_base")
                           else mat_implied(t, float(t.mat_amt)))
                    st.caption(f"{L['red']} = **{float(t.mat_amt):,.4f}**"
                               + (f"   ·   이 금액이 암시하는 보장수익률 **연 {_my*100:,.2f}%**"
                                  if _my is not None else
                                  "   ·   할증금이 없어 보장수익률을 역산할 수 없습니다"))
                _lft = ("발행총액 (원)" if is_rcps(t) else "전자등록총액 (원)")
                _v = st.number_input(
                    _lft, value=bval("face_total", float(t.face_total)), step=1e8, format="%.0f",
                    help="회계처리 탭의 전액 기준 금액을 계산합니다.")
                t.face_total = bget("face_total", _v, t.face_total, _lft.replace(" (원)", ""))
                t.issue_cost = st.number_input(
                    "발행 거래원가 (원)", value=float(t.issue_cost), step=1e6, min_value=0.0,
                    format="%.0f",
                    help="주관수수료·등록비 등. 기업회계기준서 제1032호 문단 38 에 따라 "
                         "배분된 발행금액에 비례하여 요소별로 나눕니다. 부채요소 몫은 "
                         "부채에서 차감해 유효이자율에 녹이고, 파생상품부채 몫은 당기손익-"
                         "공정가치라 즉시 비용, 자본요소 몫은 자본에서 직접 뺍니다.")
                if not (_mlk and float(t.mat_amt) > 0) and not any_blank(
                        "ytm", "cpn", "d_issue", "d_mat", "d_base"):
                    st.caption(f"{L['red']} = {mat_red_formula(t):,.4f}   "
                               + ("계약서의 상환가액 산식과 대조하십시오." if is_rcps(t)
                                  else "공시 만기상환율과 대조하십시오."))

        # 주주간계약에는 사채가 없다. 전환·조기상환·매도청구·상각 관련 칸은 뜻이
        # 없으므로 통째로 빼고, 대신 아래 「주주간계약」 칸을 연다.
        if not is_sha(t):
            with st.expander(inst_text(t, "전환 · 조정")):
                if not _DATE_MODE:
                    st.caption("모두 **발행일 기준 개월**입니다. 계약서 그대로 넣으십시오.")
                _c1, _c2 = st.columns(2)
                t.cv_s, t.cv_e = _sched_pair(_c1, _c2, t.cv_s, t.cv_e, "cv",
                                             lab=(inst_text(t, "전환 시작"), inst_text(t, "전환 종료")),
                                             fields=("cv_s", "cv_e"))
                t.rfx_mode = st.selectbox("조정 방식", [2, 1, 0], index=[2, 1, 0].index(t.rfx_mode),
                                          format_func=lambda i: ["조정 없음", "하향만", "하향 + 상향"][i])
                # 「언제든지」는 주기를 노드 간격으로 맞추면 된다 — 매 노드에서 조정한다.
                # 조기상환·매도청구의 「기간 중 언제든지」와 같은 방식이고, 엔진은 이미 할 수 있다.
                _rany = st.radio(
                    "조정 시점", ["정기", "언제든지"], index=(1 if rfx_any(t) else 0),
                    horizontal=True, key="rfx_any", disabled=(t.rfx_mode == 0),
                    help="**정기** — 「발행일부터 매 3개월마다」처럼 조정일이 정해져 있습니다.\n\n"
                         "**언제든지** — 주가가 전환가액을 밑도는 때마다 조정합니다. 주기를 노드 "
                         "간격으로 맞춰 **모든 노드**에서 조정합니다 — 노드가 촘촘할수록 정확합니다.\n\n"
                         "저가 신주발행·무상증자·합병처럼 **회사의 행위**로 조정되는 희석방지 조항은 "
                         "주가 격자로 표현할 수 없습니다. 여기서 고르지 말고 「평가에 반영하지 않은 "
                         "권리」에 적으십시오.")
                if _rany == "언제든지":
                    t.rfx_cyc = float(t.gap_m); bfill("rfx_cyc")
                    if t.rfx_mode > 0:
                        st.caption(f"조정 주기를 노드 간격({t.gap_m:g}개월)으로 맞췄습니다 — 모든 "
                                   "노드에서 전환가액을 조정합니다. 조정 시점마다 전환가액이 그 노드의 "
                                   "주가로 정해지므로 「조정일 아닌 시점」 처리방식에 따른 차이가 "
                                   "거의 사라집니다.")
                else:
                    _v = st.number_input(
                        "조정 주기 (개월)",
                        value=bval("rfx_cyc", float(t.rfx_cyc if not rfx_any(t) else max(3.0, t.gap_m*2))),
                        step=1.0, disabled=(t.rfx_mode == 0))
                    t.rfx_cyc = bget("rfx_cyc", _v, t.rfx_cyc, "리픽싱 조정 주기", need=(t.rfx_mode > 0))
                _v = st.number_input("최저 조정가액 (원)", value=bval("floor", float(t.floor)), step=1.0)
                t.floor = bget("floor", _v, t.floor, "최저 조정가액", need=(t.rfx_mode > 0))
                # 상향 재조정의 상한은 계약상 **최초** 전환가액이다. 현재 전환가액으로
                # 상한을 겸하면 이미 하향된 상품이 계약상 회복 한도까지 못 올라간다.
                _cap_on = st.checkbox(
                    "최초 전환가액이 현재 전환가액과 다르다 (이미 조정되었다)",
                    value=(t.K_cap > 0), key="capon",
                    help="계약은 「조정 후 전환가액은 최초 전환가액을 초과할 수 없다」고 "
                         "정합니다. 하향 조정된 뒤에 평가한다면 상향 재조정의 상한은 "
                         "여전히 최초 전환가액입니다.")
                if _cap_on:
                    t.K_cap = st.number_input(
                        "최초 전환가액 (원) — 상향 조정 상한", value=float(k_cap(t)),
                        step=1.0, min_value=0.0)
                    if t.K_cap < t.K0:
                        st.warning("상한이 현재 전환가액보다 낮습니다. 계약서를 다시 "
                                   "확인하십시오.")
                else:
                    t.K_cap = -1.0
                    if not is_blank("K0"):
                        st.caption(f"상향 조정 상한을 현재 전환가액 {t.K0:,.0f}원으로 둡니다.")
                _v = st.number_input(("액면가 (원)" if not is_rcps(t)
                                      else "액면가 (원) — 조정 하한 · 액면 기준 배당률 환산"),
                                     value=bval("par", float(t.par)), step=100.0, key="par_in")
                t.par = bget("par", _v, t.par, "액면가")
                if is_rcps(t):
                    st.divider()
                    st.markdown("**IPO 조항**")
                    t.ipo_on = int(st.checkbox(
                        "상장 조항을 격자에 넣는다", value=bool(t.ipo_on),
                        help="국내 RCPS 계약에 거의 빠짐없이 들어갑니다 — 상장 시 보통주 "
                             "자동전환과 공모가 연동 전환가격 조정. 책 [사례 5-5] 의 산식을 씁니다."))
                    if t.ipo_on:
                        t.ipo_m = _sched_one(st, "예상 상장 시점 (개월)", "예상 상장일", t.ipo_m, "ipom",
                                             min_value=1.0,
                                             help="발행일 기준입니다. **가정**이므로 여러 시점을 "
                                                  "돌려 조서에 나란히 싣는 편이 정직합니다.")
                        t.ipo_px = st.number_input("공모가액 (원)", value=float(t.ipo_px), step=100.0,
                                                   min_value=0.0)
                        t.ipo_mult = st.number_input("공모가 배수 (%)", value=t.ipo_mult*100,
                                                     step=5.0, min_value=1.0)/100
                        t.ipo_min = st.number_input("최소공모가격 (원)", value=float(t.ipo_min),
                                                    step=100.0, min_value=0.0,
                                                    help="그 시점 주가가 이 값에 못 미치면 **상장 자체가 "
                                                         "무산**된 것으로 봅니다. 격자가 노드마다 주가를 "
                                                         "가지고 있으므로 상장 확률을 따로 넣지 않습니다.")
                        t.ipo_conv = int(st.checkbox("상장하면 보통주로 강제전환", value=bool(t.ipo_conv),
                                                     help="상장 요건상 우선주를 남겨 둘 수 없어 대부분 "
                                                          "강제전환입니다. 그 자리에서 주식으로 끝나므로 "
                                                          "상환청구권도 발행자 상환권도 함께 사라집니다."))
                        if t.ipo_px > 0 and not is_blank("K0"):
                            st.caption(f"조정후 전환가격 = {t.ipo_px:,.0f} × {t.ipo_mult:.0%} = "
                                       f"**{t.ipo_px*t.ipo_mult:,.0f}원** (지금 전환가액 {t.K0:,.0f}원보다 "
                                       + ("낮아 조정됩니다" if t.ipo_px*t.ipo_mult < t.K0 else "높아 조정되지 않습니다")
                                       + "). 최저 조정가액·액면가 하한이 그대로 걸립니다.")
                        elif t.ipo_px <= 0:
                            st.warning("공모가액이 0 이라 IPO 조항이 작동하지 않습니다.")

            with st.expander(L["put"]):
                _p1, _p2 = st.columns(2)
                t.p_s, t.p_e = _sched_pair(_p1, _p2, t.p_s, t.p_e, "p", none_lab="이 권리 없음",
                                           fields=("p_s", "p_e"))
                t.p_e, t.p_f = exercise_mode_ui(t.p_s, t.p_e, t.p_f, "pmode_ui", t.gap_m,
                                                hide=is_blank("p_s"))
                # 표가 있으면 아래 산식 칸은 계산에 쓰이지 않는다. 칸을 그리기 «전» 에
                # 직전 실행의 텍스트를 읽어 잠근다 — 위젯 순서를 바꾸지 않아도 된다.
                _pl = sched_rows(st.session_state.get("p_sched", t.p_sched), t)
                t.p_mode = st.selectbox("행사금액 산정", ["fixed", "accrue"],
                                        index=0 if t.p_mode == "fixed" else 1,
                                        format_func=lambda x: "고정률" if x == "fixed" else "보장수익률 복리",
                                        disabled=bool(_pl))
                if t.p_mode == "fixed":
                    _v = st.number_input("행사금액 (%)", value=bval("p_rate", float(t.p_rate)), step=1.0,
                                         disabled=bool(_pl))
                    t.p_rate = bget("p_rate", _v, t.p_rate, f"{L['put']} 행사금액", need=not _pl)
                else:
                    _lpy = f"{'상환' if is_rcps(t) else '조기상환'} 보장수익률"
                    _v = st.number_input(f"{_lpy} (%)", value=bval("p_yield", t.p_yield*100), step=0.5,
                                         disabled=bool(_pl))
                    t.p_yield = bget("p_yield", _v, t.p_yield, _lpy, scale=100, need=not _pl)
                    t.p_cmp = int(st.number_input("복리 횟수 (연)", value=int(t.p_cmp), step=1,
                                                  min_value=0, help=HLP_CMP,
                                                  disabled=bool(_pl)))
                    if not _pl:
                        st.caption("행사금액 = 100 × (1 + 실효수익률)^경과연수")
                    t.p_less_cpn = ded_ui("p", t.p_yield, t.p_cmp, t.p_s, "pless_ui",
                                          has_tbl=bool(_pl),
                                          hide=any_blank("p_yield", "p_s", "cpn", "d_issue"))
                if _pl:
                    for _c in sched_lock_note(_pl, eff_cpn(t), t.p_cmp, ded_of(t, "p"), t.ipay): st.caption(_c)
                t.p_cpn_add = 1 if st.checkbox(
                    "행사일이 이자지급일이면 그 날 이자를 «따로» 받는다",
                    value=bool(getattr(t, "p_cpn_add", 0)), key="pcadd", help="계약이 「조기상환일에 원금과 «그 날까지의 이자» 를 함께 지급한다」고 쓰여 있으면 켜십시오. 행사금액표나 보장수익률 산식이 이미 그 이자를 담고 있으면 끕니다 — 켜면 두 번 세게 됩니다. **계약 해석은 앱이 정할 일이 아닙니다.** 만기는 스위치와 무관하게 마지막 이자를 함께 받습니다.") else 0

                with st.expander("조기상환 행사금액표 직접 입력 (선택)"):
                    t.p_sched = st.text_area(
                        "회차별 표 — 「날짜 또는 개월 · 금액(%)」",
                        value=t.p_sched, height=140, key="p_sched", help="계약서·공시의 **회차별 행사금액표** 를 그대로 붙여 넣으십시오. 한 줄에 「날짜(2026-12-05) 또는 개월(6) · 금액(%)」 두 값이면 됩니다 — 탭·쉼표·공백 아무거나 구분자로 씁니다. `%` 와 천단위 쉼표는 알아서 뗍니다.\n\n**표를 넣으면 산식보다 우선하고, 행사 가능 시점도 이 표를 따릅니다.** 계약이 확정 숫자를 준 경우 산식으로 되돌려 계산하면 회차마다 조금씩 어긋납니다.\n\n비우면 위의 보장수익률 산식대로 계산합니다.")
                    _rows = sched_rows(t.p_sched, t)
                    _bad = [i+1 for i, (m, _) in enumerate(parse_sched(t.p_sched, t)) if m is None]
                    if _rows:
                        st.caption(f"읽은 회차 {len(_rows)}개 — {_rows[0][0]:,.0f}개월 "
                                   f"{_rows[0][1]:,.4f}% … {_rows[-1][0]:,.0f}개월 "
                                   f"{_rows[-1][1]:,.4f}%. **이 표가 산식보다 우선합니다.**")
                        st.dataframe(pd.DataFrame(_rows, columns=["발행일부터 개월", "금액 (%)"]),
                                     hide_index=True, use_container_width=True)
                    if _bad:
                        st.warning("읽지 못한 줄 — " + ", ".join(str(x) for x in _bad[:8])
                                   + "번째. 한 줄에 두 값이어야 합니다.")

                st.divider()
                st.markdown("**회계 처리**")
                _psok = (t.conv_class == "equity" and t.k_sep != 0)
                t.p_sep = 1 if st.selectbox(
                    inst_text(t, "조기상환권 처리"), [1, 0], index=0 if int(t.p_sep) else 1,
                    format_func=lambda x: ("분리 · 파생상품부채" if x
                                           else "분리하지 않음 · 부채요소에 포함"),
                    disabled=not _psok,
                    help="행사금액이 상각후원가와 거의 같으면 주채무계약과 밀접하게 "
                         "관련되어 분리하지 않습니다 (기준서 1109 문단 B4.3.5(5)(가)). "
                         "「분리 판단」 탭이 계약 조항으로 이 결론을 내 줍니다.") else 0
                if not _psok:
                    st.caption(inst_text(t, COMPAT_PSEP))
                    t.p_sep = 1
                elif int(t.p_sep) == 0:
                    st.caption("부채요소(사채 + 조기상환권)를 통째로 상각후원가로 둡니다. "
                               "파생상품부채를 세우지 않고, 상각표도 부채요소에서 "
                               "출발합니다. 전환권대가는 어느 쪽이든 같습니다. 상각표의 만기는 "
                               "계약만기가 아니라 **첫 조기상환 가능일(기대만기)** 이고 그 시점 "
                               "행사금액이 만기 현금흐름입니다 — 계약만기로 굴리면 이자비용·부채가 "
                               "과소계상됩니다.")

                st.divider()
                st.markdown("**평가 방법**")
                _ok = put_bdt_avail(t)
                t.put_bdt = int(st.checkbox(
                    "BDT 금리격자로 평가", value=bool(t.put_bdt), disabled=not _ok,
                    help="전환을 끄면 격자가 주가와 무관해져 조기상환권이 확정 계산이 "
                         "됩니다. 금리를 확률변수로 두면 옵션의 시간가치가 생깁니다."))
                if not _ok:
                    st.caption(COMPAT_BDT)
                    if t.conv_class != "equity" or t.model != "TF": t.put_bdt = 0
                elif t.put_bdt:
                    # 키를 두지 않는다. 키가 있으면 위젯이 저장해 둔 값이 value 를
                    # 이겨서, 아래 「이 변동성 적용」 도 시나리오 불러오기도 화면에
                    # 반영되지 않는다. 주가 변동성 칸도 같은 이유로 키가 없다.
                    t.bdt_sig = st.number_input("단기이자율 변동성 (%)",
                                                value=t.bdt_sig*100, step=1.0,
                                                min_value=0.0)/100
                    t.bdt_base = st.selectbox(
                        "기준 곡선", [0, 1], index=int(t.bdt_base),
                        format_func=lambda x: ("위험 곡선에 직접" if x == 0
                                               else "무위험 + 확정 스프레드"))
                    if t.bdt_base == 0:
                        st.caption("단기이자율이 곧 위험이자율입니다. 변동성이 신용스프레드 "
                                   "변동까지 안고 갑니다. 옵션 없는 사채가 격자의 주계약과 "
                                   "정확히 같아져 검산이 쉽습니다.")
                    else:
                        st.caption("국고채에 변동성을 태우고 구간 선도 스프레드를 확정으로 "
                                   "얹습니다. 변동성을 국고채에서 관측한 값으로 쓸 수 있지만, "
                                   "**스프레드가 금리와 무관하다고 본 것**이므로 그 한계를 "
                                   "조서에 적으십시오.")
                    st.caption("로그정규 변동성입니다. 실무에서는 10~30% 를 씁니다. "
                               "0 이면 지금과 같은 값이 나옵니다.")

                    st.markdown("**시계열로 σ 산출**")
                    st.caption("할인율은 이미 등급보간 → 만기보간을 거칩니다. 변동성도 같은 "
                               "자료·같은 보간에서 나와야 조서가 하나로 이어집니다.")
                    # 위험 곡선에서 고른 방식을 그대로 첫 값으로 둔다. 사용자가 여기서
                    # 따로 고르면 그 선택이 남는다 (위젯 키가 있으므로 한 번만 정한다).
                    if "rvmode" not in st.session_state:
                        st.session_state.rvmode = {"pick": "pick", "rating": "blend"}.get(
                            t.rate_mode, "single")
                    # 위험 곡선과 같은 세 갈래다. 이름도 같게 두어야 조서에서 「어느
                    # 등급 시계열을 썼나」가 곡선 쪽과 한눈에 대조된다.
                    _RVM = ["pick", "single", "blend"]
                    _RVNM = {"pick": "표에서 등급 하나 고르기",
                             "single": "일자 · 금리 두 열 파일",
                             "blend": "두 등급 곡선으로 보간"}
                    _rmode = st.radio("자료", _RVM, key="rvmode",
                                      format_func=lambda x: _RVNM[x],
                                      help="위험 곡선을 **표에서 등급 하나**로 고르셨으면 "
                                           "여기도 같은 등급을 고르십시오. 곡선과 변동성이 "
                                           "다른 등급에서 나오면 조서가 갈라집니다.")
                    _rt = int(st.number_input("연 거래일수", value=250, step=5,
                                              min_value=30, key="rvtd"))
                    _rdrop = st.checkbox("이상치 제거 (MAD 2.5배)", value=True, key="rvdrop")
                    _ser, _how = None, ""
                    _rv_rating, _rv_tenor = "", -1.0     # 조서에 적을 σ 시계열의 등급·만기
                    _RVX = ["xls", "xlsx", "xlsm", "csv", "txt", "tsv"]
                    if _rmode == "single":
                        _f1 = st.file_uploader("금리 시계열 (일자 · 금리)", type=_RVX,
                                               key="rv1")
                        if _f1 is not None:
                            try:
                                _ser = parse_prices(read_upload(_f1.name, _f1.getvalue()))
                                _how = f"{_f1.name} · 단일 시계열"
                            except Exception as ex:
                                st.error(str(ex))
                    else:
                        st.caption("첫 열이 일자이고 머리 줄에 **등급과 만기**가 있으면 "
                                   "한 파일에 등급이 여럿이어도 갈라 읽습니다. 등급을 "
                                   "따로 받으셨으면 두 번째 칸에 넣으십시오.")
                        _fa = st.file_uploader("금리 시계열", type=_RVX, key="rva")
                        _fb = st.file_uploader("두 번째 파일 (선택)", type=_RVX, key="rvb")
                        _pool, _tens, _seen = {}, {}, False
                        for _f in (_fa, _fb):
                            if _f is None: continue
                            _seen = True
                            try:
                                _c, _r = parse_rate_panel(read_upload(_f.name, _f.getvalue()))
                                _s2, _t2 = panel_series(_c, _r, t.T)
                                _pool.update(_s2); _tens.update(_t2)
                            except Exception as ex:
                                st.error(f"{_f.name} — {ex}")
                        if _seen and not _pool:
                            st.error("등급이나 만기를 찾지 못했습니다. 머리 줄에 "
                                     "`… / BBB0` 같은 등급이나 `5년` 같은 만기가 "
                                     "있어야 합니다. 자료는 10줄 이상이어야 합니다.")
                        elif _pool:
                            # 고를 수 있는 것은 **파일에 실제로 있는 등급**뿐이다.
                            # 고시표에 없는 등급을 곡선으로 고르면 붙일 자료가 없다.
                            _opt = sorted(_pool, key=lambda r: (r is None, rating_idx(r)))
                            _nm = lambda r: (r or "등급 미상") + (
                                f" ({'·'.join(f'{x:g}년' for x in _tens.get(r, []))})"
                                if _tens.get(r) else "")
                            st.success("찾은 등급 — " + " · ".join(_nm(r) for r in _opt))
                            if _rmode == "pick":
                                # 표의 한 등급을 그대로 쓴다. 보간이 없으니 조서에
                                # 적을 것도 「그 등급 · 고정만기」 한 줄뿐이다.
                                _dg = t.rt_tgt if t.rt_tgt in _opt else _opt[0]
                                _rp = st.selectbox("σ 를 뽑을 등급", _opt,
                                                   index=_opt.index(_dg),
                                                   format_func=_nm, key="rvgp")
                                _ser = _pool[_rp]
                                _how = f"{_nm(_rp)} 단일 · 고정만기 {t.T:.2f}년"
                                if t.rt_tgt and _rp and _rp != t.rt_tgt:
                                    st.warning(f"위험 곡선의 평가대상은 **{t.rt_tgt}** 인데 "
                                               f"σ 는 **{_rp}** 에서 뽑고 있습니다. 등급이 "
                                               "다르면 조서에 그 이유를 적으십시오 — 인접 "
                                               "등급끼리는 변동성이 거의 같아 실무에서 "
                                               "흔히 하는 대체이지만, 근거는 남아야 합니다.")
                            else:
                                g1, g2 = st.columns(2)
                                _ia = _opt.index(t.rt_a) if t.rt_a in _opt else 0
                                _ra = g1.selectbox("곡선 A", _opt, index=_ia,
                                                   format_func=_nm, key="rvga")
                                _rest = [x for x in _opt if x != _ra]
                                _ib = (_rest.index(t.rt_b) + 1) if t.rt_b in _rest else 0
                                _rb = g2.selectbox("곡선 B", [None] + _rest, index=_ib,
                                                   format_func=lambda r: "(없음)" if r is None
                                                   else _nm(r), key="rvgb")
                                _rtg = st.selectbox(
                                    "평가대상", RATINGS, key="rvgt",
                                    index=(RATINGS.index(t.rt_tgt)
                                           if t.rt_tgt in RATINGS else 8),
                                    help="두 곡선 사이면 내삽, 밖이면 같은 기울기로 외삽합니다.")
                                if _rb is None or _ra is None:
                                    _ser = _pool[_ra]
                                    _how = f"{_nm(_ra)} 단일 · 고정만기 {t.T:.2f}년"
                                    st.info("곡선 B 가 없어 보간 없이 **곡선 A** 를 "
                                            "그대로 씁니다. 처음부터 등급 하나만 쓰실 "
                                            "거라면 위에서 **표에서 등급 하나 고르기** 를 "
                                            "고르시는 편이 짧습니다.")
                                else:
                                    _ser = blend_series(_pool[_ra], _pool[_rb],
                                                        _ra, _rb, _rtg)
                                    _ja, _jb = rating_idx(_ra), rating_idx(_rb)
                                    _w = (((rating_idx(_rtg)-_ja)/(_jb-_ja))
                                          if _ja != _jb else 0.0)
                                    _how = (f"{_ra}·{_rb} → {_rtg} (가중치 {_w:.2f}) · "
                                            f"고정만기 {t.T:.2f}년")
                                    st.caption(f"가중치 — {_ra} {1-_w:.0%} · "
                                               f"{_rb} {_w:.0%}")
                                    if not 0 <= _w <= 1:
                                        st.warning(
                                            f"평가대상 **{_rtg}** 가 두 곡선 **밖**입니다 "
                                            f"(가중치 {_w:.2f}). 등급 간 스프레드는 아래로 "
                                            "갈수록 가속해서 벌어지므로, 직선으로 뻗는 "
                                            "외삽은 금리를 낮게 잡습니다. 평가대상을 "
                                            "사이에 끼우는 등급을 받아 오시는 편이 "
                                            "낫습니다.")
                            _tn = _tens.get(_rp if _rmode == "pick" else _ra) or []
                            _rv_rating = ((_rp if _rmode == "pick" else
                                           (_rtg if _rb is not None else _ra)) or "")
                            _rv_tenor = float(_tn[0]) if len(_tn) == 1 else float(t.T)
                            if len(_tn) == 1 and abs(_tn[0] - t.T) > 0.5:
                                st.warning(f"파일의 만기가 **{_tn[0]:g}년** 한 열뿐인데 "
                                           f"잔존만기는 **{t.T:.2f}년** 입니다. 만기 보간을 "
                                           "할 수 없으므로 그 차이를 조서에 적으십시오.")
                    if _ser and len(_ser) >= 10:
                        _v = rate_vol(_ser, _rt, _rdrop)
                        if _v:
                            st.session_state.rate_series = _ser
                            st.session_state.rate_how = _how
                            st.session_state.rate_opt = dict(tdays=_rt, drop=_rdrop)
                            st.metric("상대 변동성 (BDT 의 σ)", f"{_v['annual']*100:.2f}%",
                                      f"절대 {_v['abs_annual']:.3f}%p")
                            st.caption(f"평균 금리 {_v['mean']:.3f}% · 관측 {_v['n']}개 · "
                                       f"{_ser[0][0]} ~ {_ser[-1][0]}"
                                       + (f" · 이상치 {_v['removed']}개 제거" if _v['removed'] else "")
                                       + f"  ·  절대 ÷ 평균 = {_v['abs_annual']/max(_v['mean'],1e-9)*100:.2f}%")
                            if _v["neg"]:
                                st.error(f"0 이하인 금리가 {_v['neg']}개 있습니다. 로그를 쓸 수 "
                                         "없어 그 구간이 빠집니다.")
                            if _v["min"] < 1.0:
                                st.warning(f"최저 금리가 {_v['min']:.3f}% 입니다. 저금리 구간에서는 "
                                           "작은 변동도 로그로는 크게 잡혀 σ 가 부풀어 오릅니다.")
                            _exp = 0 if t.bdt_base else 1
                            st.caption("기준 곡선이 **"
                                       + ("무위험 + 확정 스프레드" if t.bdt_base else "위험 곡선 직접")
                                       + "** 이므로 "
                                       + ("국고채" if t.bdt_base else "회사채(평가대상 등급)")
                                       + " 시계열을 쓰셔야 맞습니다.")
                            # 시계열의 금리 수준과 격자가 쓰는 곡선의 수준을 견준다.
                            # 8% 회사채로 변동성을 재고 2.5% 공사채 곡선에 태우면
                            # 서로 다른 채권을 섞은 것이다.
                            try:
                                _RFc, _CRc = curves(t)
                                _lvl = (_RFc(t.T) if t.bdt_base else _CRc(t.T))*100
                                _nmc = "무위험 곡선" if t.bdt_base else "위험 곡선"
                                if _lvl > 0.05 and abs(_v["mean"] - _lvl)/_lvl > 0.5:
                                    st.error(
                                        f"시계열의 평균 금리는 **{_v['mean']:.2f}%** 인데 "
                                        f"격자가 쓰는 {_nmc} 은 잔존 {t.T:.2f}년에서 "
                                        f"**{_lvl:.2f}%** 입니다. 서로 다른 채권입니다 — "
                                        "변동성을 어느 등급에서 뽑았는지, 이자율 칸의 "
                                        "곡선을 어느 줄로 골랐는지 둘 다 확인하십시오. "
                                        "상대변동성이라 수준 자체가 값에 들어가지는 "
                                        "않지만, 근거가 갈리면 조서가 서지 않습니다.")
                                else:
                                    st.caption(f"시계열 평균 {_v['mean']:.2f}% · "
                                               f"{_nmc} {t.T:.2f}년 {_lvl:.2f}% — 수준이 "
                                               "비슷합니다.")
                            except Exception:
                                pass
                            if abs(_v["annual"] - t.bdt_sig) > 5e-5:
                                st.warning(f"아직 **적용하지 않았습니다**. 지금 조서에 "
                                           f"들어가는 값은 **{t.bdt_sig*100:.2f}%** "
                                           "입니다. 아래 단추를 누르셔야 산출한 값이 "
                                           "BDT 격자에 들어갑니다.")
                            if st.button("이 변동성 적용", use_container_width=True,
                                         type="primary", key="rvapply"):
                                t.bdt_sig = _v["annual"]
                                t.rvol_rating, t.rvol_tenor, t.rvol_how = _rv_rating, _rv_tenor, _how
                                st.rerun()

            with st.expander(L["call"]):
              if is_rcps(t):
                # 콜은 두 갈래다. **발행자 상환권**은 발행회사가 우선주를 되사는 조항이라
                # 거래상대방이 그대로고(문단 4.3.1) 내재파생이므로, 격자 안에서
                # MIN(보유, 상환가액) 으로 눌러 전체에 걸린다. **제3자 지정 매도청구권**은
                # 발행회사가 지정한 제3자가 인수인의 우선주를 사 가는 권리라 거래상대방이
                # 달라져 별도의 금융상품이고, CB 의 매도청구권과 같은 길을 간다 —
                # 한도·의무보유·세 평가방법이 그대로 살아난다.
                t.issuer_call = st.selectbox(
                    "콜옵션", [0, 1, 2], index=int(t.issuer_call),
                    format_func=lambda i: ["없음 — 상환권은 투자자만",
                                           "발행회사의 상환권 (전체에 걸림)",
                                           "제3자 지정 매도청구권 (한도 %)"][i],
                    help="**발행회사의 상환권**은 회사가 우선주를 되사 가는 조항입니다. "
                         "거래상대방이 그대로라 내재파생이고, 상환청구권과 하나의 복합내재파생으로 "
                         "묶습니다 (기준서 1109 문단 B4.3.4).\n\n"
                         "**제3자 지정 매도청구권**은 발행회사가 **지정하는 제3자**가 인수인에게서 "
                         "우선주를 사 가는 권리입니다. 거래상대방이 달라지므로 **별도의 금융상품**이고 "
                         "(문단 4.3.1), 발행회사는 이를 **파생상품자산**으로 따로 인식합니다. "
                         "실무 계약에서는 총 발행금액의 10~20% 한도로 자주 붙습니다.")
                # 발행자 상환권·없음 갈래에서는 derive 가 k_method 를 0 으로 눌러 둔다.
                # 제3자 지정으로 **바꾼 순간**에는 그 0 이 남아 있어 유무가치비교법이 되는데,
                # 본문 4.3.4 의 기본은 복합옵션이다. 전환하는 그 회차에만 기본값을 채운다.
                if st.session_state.get("_ic_prev") not in (None, 2) and t.issuer_call == 2:
                    t.k_method, t.k_split = 2, 1
                st.session_state["_ic_prev"] = int(t.issuer_call)
                if t.issuer_call == 1:
                    _k1, _k2 = st.columns(2)
                    t.k_s, t.k_e = _sched_pair(_k1, _k2, t.k_s, t.k_e, "k", none_lab="이 권리 없음",
                                               fields=("k_s", "k_e"))
                    t.k_e, t.k_f = exercise_mode_ui(t.k_s, t.k_e, t.k_f, "kmode_ui", t.gap_m,
                                                    hide=is_blank("k_s"))
                    _kl = sched_rows(t.k_sched, t)     # 이 갈래에는 표 칸이 없다 — Terms 값을 본다
                    _v = st.number_input("상환 보장수익률 (연 %)", value=bval("k_prem", t.k_prem*100), step=0.5,
                                         help="발행자 상환가액 = 100 × (1 + 보장수익률 복리)^경과연수 "
                                              "− 기지급배당. 상환청구권과 같은 산식입니다.",
                                         disabled=bool(_kl))
                    t.k_prem = bget("k_prem", _v, t.k_prem, "발행자 상환 보장수익률", scale=100,
                                    need=not _kl)
                    t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                                  help=HLP_CMP, disabled=bool(_kl)))
                    # 매도청구 행사금액은 계약마다 갈린다 — 차바이오텍 RCPS 는 순수 분기복리
                    # (1년 101.5084%) 라 「공제하지 않음」이다. 옛 체크박스(1/0)와 뜻이 같다.
                    t.k_less_cpn = ded_ui("k", t.k_prem, t.k_cmp, t.k_s, "kless_ui", has_tbl=bool(_kl),
                                      hide=any_blank("k_prem", "k_s", "cpn", "d_issue"))
                    t.k_cpn_add = 1 if st.checkbox(
                        "행사일이 이자지급일이면 그 날 이자를 «따로» 받는다",
                        value=bool(getattr(t, "k_cpn_add", 0)), key="kcadd1",
                        help="계약이 「매도청구일에 매매대금과 «그 날까지의 이자» 를 함께 지급한다」고 쓰여 있으면 켜십시오. 행사금액이 이미 그 이자를 담고 있으면 끕니다 — 켜면 두 번 세게 됩니다. 바로 위의 「이미 지급한 이자·배당은」과는 다른 물음입니다: 그것은 산식이 이미 준 이자를 «어떻게 빼는가» 이고, 이것은 행사하는 날의 이자를 «더 얹는가» 입니다.") else 0
                    if _kl:
                        for _c in sched_lock_note(_kl, eff_cpn(t), t.k_cmp, ded_of(t, "k"), t.ipay): st.caption(_c)
                    st.caption("상환청구권과 하나의 **복합내재파생상품**으로 묶어 순액으로 봅니다 "
                               "(기준서 1109 문단 B4.3.4). 전환권을 자본으로 두면 부채요소는 "
                               "「우선주 + 상환청구권 − 발행자 상환권」입니다.")
                elif t.issuer_call == 2:
                    _k1, _k2 = st.columns(2)
                    t.k_s, t.k_e = _sched_pair(_k1, _k2, t.k_s, t.k_e, "k", none_lab="이 권리 없음",
                                               fields=("k_s", "k_e"))
                    t.k_e, t.k_f = exercise_mode_ui(t.k_s, t.k_e, t.k_f, "kmode_ui", t.gap_m,
                                                    hide=is_blank("k_s"))
                    _kl = sched_rows(st.session_state.get("ksched_rcps", t.k_sched), t)
                    _v = st.number_input(
                        "매수대금 보장수익률 (연 %)", value=bval("k_prem", t.k_prem*100), step=0.5,
                        help="매매대금 = 인수대금 × (1 + 보장수익률 복리)^경과연수. "
                             "계약서의 회차별 매도청구권 행사금액(%) 표와 대조하십시오.",
                        disabled=bool(_kl))
                    t.k_prem = bget("k_prem", _v, t.k_prem, "매도청구 보장수익률", scale=100,
                                    need=not _kl)
                    t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                                  help="공시 행사금액표가 분기복리면 4. " + HLP_CMP,
                                                  disabled=bool(_kl)))
                    # 매도청구 행사금액은 계약마다 갈린다 — 차바이오텍 RCPS 는 순수 분기복리
                    # (1년 101.5084%) 라 「공제하지 않음」이다. 옛 체크박스(1/0)와 뜻이 같다.
                    t.k_less_cpn = ded_ui("k", t.k_prem, t.k_cmp, t.k_s, "kless_ui", has_tbl=bool(_kl),
                                      hide=any_blank("k_prem", "k_s", "cpn", "d_issue"))
                    t.k_cpn_add = 1 if st.checkbox(
                        "행사일이 이자지급일이면 그 날 이자를 «따로» 받는다",
                        value=bool(getattr(t, "k_cpn_add", 0)), key="kcadd2",
                        help="계약이 「매도청구일에 매매대금과 «그 날까지의 이자» 를 함께 지급한다」고 쓰여 있으면 켜십시오. 행사금액이 이미 그 이자를 담고 있으면 끕니다 — 켜면 두 번 세게 됩니다. 바로 위의 「이미 지급한 이자·배당은」과는 다른 물음입니다: 그것은 산식이 이미 준 이자를 «어떻게 빼는가» 이고, 이것은 행사하는 날의 이자를 «더 얹는가» 입니다.") else 0
                    # 콜 갈래를 바꾸면 derive 가 k_w 를 0(없음)이나 1(발행자 상환권)로
                    # 눌러 놓는다. 그 값을 그대로 보이면 한도가 0% 로 뜨므로, 처음
                    # 열릴 때는 실무에서 흔한 20% 를 채워 둔다. 계약서 값으로 고치면 된다.
                    _v = st.number_input(
                        "행사 한도 (%)", value=bval("k_w", (t.k_w*100 if 0 < t.k_w < 1 else 20.0)),
                        step=5.0,
                        help="콜옵션 대상주식이 총 발행금액에서 차지하는 비율입니다. "
                             "실무 계약은 10~20% 가 흔합니다. 계약서의 「콜옵션 대상주식」 "
                             "조항을 그대로 넣으십시오.")
                    t.k_w = bget("k_w", _v, t.k_w, "매도청구 행사 한도", scale=100)

                    with st.expander("매도청구 행사금액표 직접 입력 (선택)"):
                        t.k_sched = st.text_area(
                            "회차별 표 — 「날짜 또는 개월 · 금액(%)」",
                            value=t.k_sched, height=120, key="ksched_rcps", help="계약서의 **회차별 매수대금표** 를 그대로 붙여 넣으십시오. 한 줄에 「날짜 또는 개월 · 금액(%)」 두 값입니다. 표를 넣으면 산식보다 우선하고 행사 가능 시점도 표를 따릅니다. 비우면 위 프리미엄 산식대로입니다.")
                        _kr = sched_rows(t.k_sched, t)
                        _kb = [i+1 for i, (m, _) in enumerate(parse_sched(t.k_sched, t)) if m is None]
                        if _kr:
                            st.caption(f"읽은 회차 {len(_kr)}개. **이 표가 산식보다 우선합니다.**")
                            st.dataframe(pd.DataFrame(_kr, columns=["발행일부터 개월", "금액 (%)"]),
                                         hide_index=True, use_container_width=True)
                        if _kb:
                            st.warning("읽지 못한 줄 — " + ", ".join(str(x) for x in _kb[:8]) + "번째.")
                    if _kl:
                        for _c in sched_lock_note(_kl, eff_cpn(t), t.k_cmp, ded_of(t, "k"), t.ipay): st.caption(_c)
                    # 의무보유는 «있는가 → 언제까지 → 무엇을 막는가» 순서로 묻는다. 종전에는
                    # 있는지 묻는 체크박스가 개월 칸 아래에 있어, 껐는데도 위 칸이 열려 있어
                    # 그 값이 쓰이는 줄 알았다.
                    t.k_hold = 1 if st.checkbox(
                        "콜 대상물량 의무보유 있음",
                        value=bool(t.k_hold), key="khold_rcps", help='**의무보유 있음**: 콜 대상비율에 해당하는 물량을 아래 「의무보유 (개월)」 동안 전환(과 조기상환청구)이 불가능한 상태로 보유한다고 가정합니다. 그 기간에는 콜 대상물량이 남아 있어 콜을 행사할 수 있습니다.\n\n**없음**: 투자자가 먼저 전환하거나 조기상환을 청구해 그 물량을 소멸시키면 그것을 사는 콜도 함께 사라집니다.\n\n한공회 4.4.2·4.4.3 은 **기초자산**에서 의무보유를 빼라고 하고, 같은 문단이 「계약조건의 특성을 가치평가에 반영해야 한다」고도 합니다. 그래서 기초자산은 그대로 두고 **콜 계약층에서 «행사기회가 유지되는가»로만** 반영합니다. 값 차이가 매우 큽니다.\n\n유무가치비교법에서는 같은 기간의 전환(·조기상환) 시작을 늦추는 방식으로 들어갑니다 — **두 방법이 같은 기간·같은 권리를 봅니다.**' ) else 0
                    t.k_lock = _sched_one(
                        st, "의무보유 (개월)", "의무보유 만료일", t.k_lock, "klock",
                        disabled=not t.k_hold,
                        help="인수인이 콜옵션 대상주식을 **묶어 두어야** 하는 기간입니다. 매도청구 종료일까지 두는 계약이 많습니다. **두 평가방법이 모두 이 기간을 봅니다** — 유무가치비교법은 이 기간 동안 전환(과 아래 체크박스가 켜져 있으면 조기상환청구)을 막고, 옵션차익법은 이 기간 안에서만 콜 대상물량이 존속한다고 봅니다.")
                    if not t.k_hold:
                        st.caption("의무보유가 없으므로 이 칸은 계산에 쓰지 않습니다. "
                                   "위 체크를 켜면 다시 열립니다.")
                    # 옵션차익혼합할인법은 노드의 지분·부채 분해 위에 정의된 산식이라 TF
                    # 전용이다 (한공회 4.4.3). GS 를 고르면 기초상품만 GS 이고 콜은 TF 라
                    # 표시와 계산이 어긋나므로 조합 자체를 막는다.
                    _gs_blk = (t.model == "GS")
                    if _gs_blk and t.k_method:
                        t.k_method = 0
                    t.k_method = st.selectbox("평가방법", [0, 1, 2],
                                              index=[0, 1, 2].index(t.k_method),
                                              format_func=lambda i: K_METHODS[i], key="kmeth_rcps",
                                              disabled=_gs_blk)
                    if _gs_blk:
                        st.caption(COMPAT_GS_KMETHOD)
                    t.k_kind = int(st.selectbox("콜옵션 유형", [0, 1], index=int(t.k_kind),
                                                format_func=lambda i: K_KINDS[i], key="kkind_rcps", help="**지정 가능 콜**: 발행자가 보유하다 제3자를 지정해 넘기는 콜 — 발행자의 파생상품자산으로 매 결산 재평가 (회계기준원 2022-I-KQA006, 본문 4.4).\n\n**기특정 콜**: 발행 시 제3자(최대주주 등)가 이미 정해진 콜. 값은 지정 가능 콜과 같은 격자에서 나오고 회계처리만 다릅니다.\n\n본문 4.5 는 기특정 콜에 **세 접근법**을 나란히 둡니다 — 4.5.2 «접근법 1»(발행자 콜과 같이 유무가치비교법), 4.5.3 «접근법 2-1»(이연지정), 4.5.4 «접근법 2-2». 4.5.1 도 「주주간 분배로 **회계처리 되는 경우가 있다**」고 쓰지 「항상 그렇다」고 하지 않습니다. **이 앱은 접근법 2-2 를 채택**했습니다 — 발행회사가 옵션 당사자가 아니라고 보아 자산을 인식하지 않고 최초 인식 시 주주간 분배로 봅니다.\n\n**접근법 1 을 따르려면** 위 「평가방법」을 「유무가치비교법」으로 두십시오. 접근법 2-1(이연지정)은 본문 FAQ 의 반론(제3자 이전이 값을 바꾸면 무차익거래 원칙과 배치)이 있어 넣지 않았습니다."))
                    if t.k_method:
                        t.k_split = int(st.selectbox("지분·채권 구분 기준", [1, 0],
                                                     index=[1, 0].index(int(t.k_split)),
                                                     format_func=lambda i: K_SPLITS[i],
                                                     key="ksplit_rcps", help='한공회 본문 4.3.3 은 「콜옵션의 **행사가를 분해**하기 위해서는 … 행사 확률인 위험중립확률을 구해야 한다. 이 단계에서 **GS 모형의 전환확률**을 활용할 수 있다」고 씁니다. **비례균등차감법**(지분·부채 가치 구성비율)은 국내 실무서·부속예제가 쓰는 방식으로, 행사가를 페이오프에 비례해 균등 차감하는 것과 같습니다 — 본문에서 도출되는 방식은 아니지만 널리 쓰입니다. 두 값을 분리 판단 탭에서 나란히 보실 수 있습니다.'))
                    t.k_basis = st.text_input("평가기법 선택 근거 (조서 문안)", value=t.k_basis, key="kbasis_rcps",
                                              help="한공회 4.6.2 — 복수의 기법이 가능한 자리에서 고른 이유를 적어 조서에 남깁니다. "
                                                   "바꾸면 그 사유도 여기에.")
                    if t.k_hold:
                        t.k_lock_put = 1 if st.checkbox(
                            "이 기간에 조기상환청구도 막는다",
                            value=bool(t.k_lock_put), key="klput_rcps", help='계약 정의는 「콜 대상물량을 의무보유 기간 동안 **전환 및 조기상환청구가 불가능한 상태로** 보유」입니다. 끄면 **전환만** 막고 조기상환청구는 허용하는 계약이 됩니다 — 그 물량이 조기상환으로 빠져나갈 수 있어 콜 가치가 낮아집니다. 계약서의 처분·전환 제한 조항을 그대로 반영하십시오.') else 0
                    st.caption("의무보유는 **기초자산을 바꾸지 않습니다.** 옵션차익법에서는 콜 대상우선주가 그 기간 동안 존속하는지로, 유무가치비교법에서는 같은 기간의 전환(·조기상환) 시작을 늦추는 방식으로 들어갑니다 — 두 방법이 같은 기간·같은 권리를 봅니다. 값 차이는 분리 판단 탭에서 나눠 보실 수 있습니다.")
                    st.caption("거래상대방이 발행회사가 아니라 제3자이므로 **별도의 금융상품**입니다 "
                               "(기준서 1109 문단 4.3.1). 회계처리 탭에서 **파생상품자산**으로 "
                               "따로 세우고, 상환청구권·전환권 묶음에는 넣지 않습니다.")
                else:
                    st.caption("상환권은 투자자만 가집니다.")
              else:
                  _k1, _k2 = st.columns(2)
                  t.k_s, t.k_e = _sched_pair(_k1, _k2, t.k_s, t.k_e, "k", none_lab="이 권리 없음",
                                             fields=("k_s", "k_e"))
                  t.k_e, t.k_f = exercise_mode_ui(t.k_s, t.k_e, t.k_f, "kmode_ui", t.gap_m,
                                                  hide=is_blank("k_s"))
                  _kl = sched_rows(st.session_state.get("ksched_cb", t.k_sched), t)
                  _v = st.number_input("프리미엄 (연 %)", value=bval("k_prem", t.k_prem*100), step=0.5,
                                       disabled=bool(_kl))
                  t.k_prem = bget("k_prem", _v, t.k_prem, "매도청구 프리미엄", scale=100, need=not _kl)
                  t.k_cmp = int(st.number_input("복리 횟수 (연)", 0, 12, int(t.k_cmp), 1,
                                                help="분기복리 4 · 반기 2 · 연 1. " + HLP_CMP
                                                     + " 계약서의 매수대금 표와 맞는지 확인하십시오.",
                                                disabled=bool(_kl)))
                  # 매도청구 행사금액은 계약마다 갈린다 — 차바이오텍 RCPS 는 순수 분기복리
                  # (1년 101.5084%) 라 「공제하지 않음」이다. 옛 체크박스(1/0)와 뜻이 같다.
                  t.k_less_cpn = ded_ui("k", t.k_prem, t.k_cmp, t.k_s, "kless_ui", has_tbl=bool(_kl),
                                      hide=any_blank("k_prem", "k_s", "cpn", "d_issue"))
                  t.k_cpn_add = 1 if st.checkbox(
                      "행사일이 이자지급일이면 그 날 이자를 «따로» 받는다",
                      value=bool(getattr(t, "k_cpn_add", 0)), key="kcadd3",
                      help="계약이 「매도청구일에 매매대금과 «그 날까지의 이자» 를 함께 지급한다」고 쓰여 있으면 켜십시오. 행사금액이 이미 그 이자를 담고 있으면 끕니다 — 켜면 두 번 세게 됩니다. 바로 위의 「이미 지급한 이자·배당은」과는 다른 물음입니다: 그것은 산식이 이미 준 이자를 «어떻게 빼는가» 이고, 이것은 행사하는 날의 이자를 «더 얹는가» 입니다.") else 0

                  with st.expander("매도청구 행사금액표 직접 입력 (선택)"):
                      t.k_sched = st.text_area(
                          "회차별 표 — 「날짜 또는 개월 · 금액(%)」",
                          value=t.k_sched, height=120, key="ksched_cb", help="계약서의 **회차별 매수대금표** 를 그대로 붙여 넣으십시오. 한 줄에 「날짜 또는 개월 · 금액(%)」 두 값입니다. 표를 넣으면 산식보다 우선하고 행사 가능 시점도 표를 따릅니다. 비우면 위 프리미엄 산식대로입니다.")
                      _kr = sched_rows(t.k_sched, t)
                      _kb = [i+1 for i, (m, _) in enumerate(parse_sched(t.k_sched, t)) if m is None]
                      if _kr:
                          st.caption(f"읽은 회차 {len(_kr)}개. **이 표가 산식보다 우선합니다.**")
                          st.dataframe(pd.DataFrame(_kr, columns=["발행일부터 개월", "금액 (%)"]),
                                       hide_index=True, use_container_width=True)
                      if _kb:
                          st.warning("읽지 못한 줄 — " + ", ".join(str(x) for x in _kb[:8]) + "번째.")
                  if _kl:
                      for _c in sched_lock_note(_kl, eff_cpn(t), t.k_cmp, ded_of(t, "k"), t.ipay): st.caption(_c)
                  _v = st.number_input("행사 한도 (%)", value=bval("k_w", t.k_w*100), step=5.0)
                  t.k_w = bget("k_w", _v, t.k_w, "매도청구 행사 한도", scale=100)
                  # 있는가 → 언제까지 → 무엇을 막는가. 껐는데 개월 칸이 열려 있으면
                  # 그 값이 쓰이는 줄 안다.
                  t.k_hold = 1 if st.checkbox(
                      "콜 대상물량 의무보유 있음",
                      value=bool(t.k_hold), key="khold_cb", help='**의무보유 있음**: 콜 대상비율에 해당하는 물량을 아래 「의무보유 (개월)」 동안 전환(과 조기상환청구)이 불가능한 상태로 보유한다고 가정합니다. 그 기간에는 콜 대상물량이 남아 있어 콜을 행사할 수 있습니다.\n\n**없음**: 투자자가 먼저 전환하거나 조기상환을 청구해 그 물량을 소멸시키면 그것을 사는 콜도 함께 사라집니다.\n\n한공회 4.4.2·4.4.3 은 **기초자산**에서 의무보유를 빼라고 하고, 같은 문단이 「계약조건의 특성을 가치평가에 반영해야 한다」고도 합니다. 그래서 기초자산은 그대로 두고 **콜 계약층에서 «행사기회가 유지되는가»로만** 반영합니다. 값 차이가 매우 큽니다.\n\n유무가치비교법에서는 같은 기간의 전환(·조기상환) 시작을 늦추는 방식으로 들어갑니다 — **두 방법이 같은 기간·같은 권리를 봅니다.**' ) else 0
                  t.k_lock = _sched_one(
                      st, "의무보유 (개월)", "의무보유 만료일", t.k_lock, "klock",
                      disabled=not t.k_hold,
                      help="인수인이 콜옵션 대상물량을 **묶어 두어야** 하는 기간입니다. 매도청구 "
                           "종료일까지 두는 계약이 많습니다. **두 평가방법이 모두 이 기간을 봅니다.**")
                  if not t.k_hold:
                      st.caption("의무보유가 없으므로 이 칸은 계산에 쓰지 않습니다. "
                                 "위 체크를 켜면 다시 열립니다.")
                  if t.k_hold:
                      t.k_lock_put = 1 if st.checkbox(
                          "이 기간에 조기상환청구도 막는다",
                          value=bool(t.k_lock_put), key="klput_cb", help='계약 정의는 「콜 대상물량을 의무보유 기간 동안 **전환 및 조기상환청구가 불가능한 상태로** 보유」입니다. 끄면 **전환만** 막고 조기상환청구는 허용하는 계약이 됩니다 — 그 물량이 조기상환으로 빠져나갈 수 있어 콜 가치가 낮아집니다. 계약서의 처분·전환 제한 조항을 그대로 반영하십시오.') else 0
                  st.caption("의무보유는 **기초자산을 바꾸지 않습니다.** 옵션차익법에서는 콜 대상사채가 그 기간 동안 존속하는지로, 유무가치비교법에서는 같은 기간의 전환(·조기상환) 시작을 늦추는 방식으로 들어갑니다 — 두 방법이 같은 기간·같은 권리를 봅니다. 값 차이는 분리 판단 탭에서 나눠 보실 수 있습니다.")
                  if int(t.k_kind) == 1 and not t.k_sep:
                      t.k_sep = 1
                  t.k_sep = 1 if st.selectbox(
                      "회계 처리", ["별도 금융상품", "복합내재파생에 포함"],
                      index=0 if t.k_sep else 1, disabled=(int(t.k_kind) == 1),
                      help="발행회사가 지정하는 제3자가 살 수 있으면 거래상대방이 달라지므로 "
                           "별도의 금융상품입니다 (기준서 1109 문단 4.3.1). 발행회사만 "
                           "행사할 수 있으면 내재파생상품이라 전환권·조기상환권과 하나로 "
                           "묶습니다 (문단 B4.3.4). 주계약과 전환권대가는 어느 쪽이든 같고 "
                           "파생을 총액으로 볼지 순액으로 볼지가 다릅니다."
                      ) == "별도 금융상품" else 0
                  # 옵션차익혼합할인법은 노드의 지분·부채 분해 위에 정의된 산식이라 TF
                  # 전용이다 (한공회 4.4.3). GS 를 고르면 기초상품만 GS 이고 콜은 TF 라
                  # 표시와 계산이 어긋나므로 조합 자체를 막는다.
                  _gs_blk = (t.model == "GS")
                  if _gs_blk and t.k_method:
                      t.k_method = 0
                  t.k_method = st.selectbox("평가방법", [0, 1, 2],
                                            index=[0, 1, 2].index(t.k_method),
                                            format_func=lambda i: K_METHODS[i],
                                            disabled=_gs_blk)
                  if _gs_blk:
                      st.caption(COMPAT_GS_KMETHOD)
                  t.k_kind = int(st.selectbox("콜옵션 유형", [0, 1], index=int(t.k_kind),
                                                format_func=lambda i: K_KINDS[i], key="kkind_cb", help="**지정 가능 콜**: 발행자가 보유하다 제3자를 지정해 넘기는 콜 — 발행자의 파생상품자산으로 매 결산 재평가 (회계기준원 2022-I-KQA006, 본문 4.4).\n\n**기특정 콜**: 발행 시 제3자(최대주주 등)가 이미 정해진 콜. 값은 지정 가능 콜과 같은 격자에서 나오고 회계처리만 다릅니다.\n\n본문 4.5 는 기특정 콜에 **세 접근법**을 나란히 둡니다 — 4.5.2 «접근법 1»(발행자 콜과 같이 유무가치비교법), 4.5.3 «접근법 2-1»(이연지정), 4.5.4 «접근법 2-2». 4.5.1 도 「주주간 분배로 **회계처리 되는 경우가 있다**」고 쓰지 「항상 그렇다」고 하지 않습니다. **이 앱은 접근법 2-2 를 채택**했습니다 — 발행회사가 옵션 당사자가 아니라고 보아 자산을 인식하지 않고 최초 인식 시 주주간 분배로 봅니다.\n\n**접근법 1 을 따르려면** 위 「평가방법」을 「유무가치비교법」으로 두십시오. 접근법 2-1(이연지정)은 본문 FAQ 의 반론(제3자 이전이 값을 바꾸면 무차익거래 원칙과 배치)이 있어 넣지 않았습니다."))
                  if t.k_method:
                      t.k_split = int(st.selectbox("지분·채권 구분 기준", [1, 0],
                                                   index=[1, 0].index(int(t.k_split)),
                                                   format_func=lambda i: K_SPLITS[i],
                                                   key="ksplit_cb", help='한공회 본문 4.3.3 은 「콜옵션의 **행사가를 분해**하기 위해서는 … 행사 확률인 위험중립확률을 구해야 한다. 이 단계에서 **GS 모형의 전환확률**을 활용할 수 있다」고 씁니다. **비례균등차감법**(지분·부채 가치 구성비율)은 국내 실무서·부속예제가 쓰는 방식으로, 행사가를 페이오프에 비례해 균등 차감하는 것과 같습니다 — 본문에서 도출되는 방식은 아니지만 널리 쓰입니다. 두 값을 분리 판단 탭에서 나란히 보실 수 있습니다.'))
                  t.k_basis = st.text_input("평가기법 선택 근거 (조서 문안)", value=t.k_basis, key="kbasis_cb",
                                              help="한공회 4.6.2 — 복수의 기법이 가능한 자리에서 고른 이유를 적어 조서에 남깁니다. "
                                                   "바꾸면 그 사유도 여기에.")
                  if t.k_method:
                      st.caption("발행회사가 **지정하는 제3자**도 행사할 수 있는 콜옵션은 별도의 "
                                 "금융상품이고 기초자산이 전환사채인 복합옵션입니다 "
                                 "(기준서 1109 문단 4.3.1). **기초자산에서는 의무보유가 빠지고**, "
                                 "의무보유는 콜 대상 사채가 행사기간 동안 존속하는지로만 반영됩니다.")
                  else:
                      st.caption("콜을 넣고 뺀 두 평가액의 차이로 봅니다. 의무보유 효과가 콜 값에 "
                                 "포함됩니다.")

              # 풋과 콜이 같은 노드에서 함께 열릴 때 누가 먼저인가. 계약이 정하는
              # 것이지 수식이 정하는 것이 아니다. 콜이 없으면 물을 것도 없다.
              if t.k_w > 0:
                  t.pc_order = int(st.selectbox(
                      "조기상환청구권과 겹칠 때", [0, 1], index=int(t.pc_order),
                      format_func=lambda i: ["투자자 조기상환 우선", "발행자 매도청구 우선"][i],
                      help="같은 날 두 권리가 모두 열릴 때의 계약상 우선순위입니다.\n\n"
                           "**투자자 조기상환 우선** — 통지한 조기상환을 매도청구로 막지 "
                           "못합니다. 한국 사모 전환사채의 매도청구권은 사채 «일부를 "
                           "매수»하는 권리이지 상환이 아니라는 읽기입니다.\n\n"
                           "**발행자 매도청구 우선** — 매도청구가 유효하게 행사되면 "
                           "투자자는 전환으로만 대응할 수 있습니다. 미국식 callable "
                           "convertible 의 표준 처리입니다.\n\n"
                           "두 행사금액이 다르고 행사기간이 겹칠 때만 값이 갈립니다. "
                           "겹치지 않으면 어느 쪽을 고르셔도 같은 답이 나옵니다."))
                  st.caption("계약서에 「이미 통지된 조기상환청구는 매도청구로 "
                             "번복할 수 없다」 같은 조항이 있으면 첫 번째입니다. "
                             "**계약 우선순위가 수식보다 먼저입니다** — 고른 근거를 "
                             "조서에 남기십시오.")

            with st.expander("기말 재평가 · 전기 장부금액"):
                st.caption("평가기준일이 발행일보다 뒤인 **결산 평가**라면 전기말 장부금액을 넣으십시오. "
                           "회계처리 탭에 당기 평가손익과 분개가 나옵니다. 발행 시점 평가면 비워 두십시오.")
                if holder_on(t):
                    # 투자자는 전체를 공정가치로 잰다 — 장부금액이 곧 전기말 공정가치다
                    _hp = st.checkbox("전기말 장부금액(공정가치)이 있다",
                                      value=(float(t.prev_hold) >= 0), key="hprev")
                    if _hp:
                        t.prev_hold = st.number_input(
                            "전기말 장부금액 — 순포지션 공정가치 (100 기준)",
                            value=max(0.0, float(t.prev_hold)), step=0.01, format="%.4f",
                            key="prev_hold",
                            help="전기 조서의 「투자자 순포지션」입니다. 당기말 공정가치와의 차이가 "
                                 "금융자산평가손익(당기손익)입니다.")
                    else:
                        t.prev_hold = -1.0
                    st.caption("투자자 관점 — 발행자의 파생상품부채·주계약 장부금액과 유효이자율 칸은 "
                               "쓰지 않습니다.")
                else:
                    _has_prev = st.checkbox("전기말 장부금액이 있다", value=(t.prev_deriv >= 0))
                    if _has_prev:
                        t.prev_deriv = st.number_input("전기말 파생상품부채 장부금액 (100 기준)",
                                                       value=max(0.0, float(t.prev_deriv)), step=0.01,
                                                       format="%.4f",
                                                       help="전환권이 부채면 복합내재파생상품, 자본이면 "
                                                            "분리한 상환청구권(·발행자 상환권) 파생상품부채.")
                        t.prev_host = st.number_input("전기말 주계약(부채) 장부금액 (100 기준)",
                                                      value=max(0.0, float(t.prev_host)), step=0.01,
                                                      format="%.4f",
                                                      help="상각후원가 장부금액. 당기 상각표의 기초와 대조합니다.")
                        _e = st.number_input("발행일 유효이자율 (%)",
                                             value=(t.eir_issue*100 if t.eir_issue >= 0 else 0.0),
                                             step=0.1, min_value=0.0, format="%.4f",
                                             help="**발행 시점 조서**의 상각표에서 역산한 값입니다. "
                                                  "이 앱의 상각표는 평가기준일 배분액에서 출발해 최초 "
                                                  "인식에만 맞으므로, 결산 평가에서는 이 값을 넣어야 "
                                                  "당기 이자비용이 나옵니다.")
                        t.eir_issue = _e/100 if _e > 0 else -1.0
                        t.cur_periods = int(st.number_input(
                            "당기 이자 회차 수", value=int(t.cur_periods), step=1, min_value=0,
                            help=f"0 이면 1년치({max(1, int(round(12/max(1e-6, t.ipay))))}회)로 봅니다."))
                    else:
                        t.prev_deriv = -1.0; t.prev_host = -1.0; t.eir_issue = -1.0
                _sa = st.number_input(
                    "상환·재매입 지급대가 (100 기준)",
                    value=(t.settle_amt if t.settle_amt >= 0 else 0.0), step=1.0, min_value=0.0,
                    format="%.4f",
                    help="만기 전에 상환하거나 되사는 경우입니다. 0 이면 표시하지 않습니다. "
                         "대가를 부채·자본에 배분해 상환손익을 냅니다 (1032 문단 AG33·AG34).")
                t.settle_amt = _sa if _sa > 0 else -1.0

        else:
            with st.expander("주주간계약 — 풋 · 콜", expanded=True):
                st.caption(("금액 기준은 투자원금 100 입니다." if _DATE_MODE else
                            "모두 **투자일(발행일) 기준 개월**입니다. 계약서 그대로 넣으십시오. "
                            "금액 기준은 투자원금 100 입니다."))
                st.markdown("**투자자 풋옵션** — 보유 지분을 되팔 권리")
                q1, q2, q3 = st.columns(3)
                t.sha_put_s, t.sha_put_e = _sched_pair(q1, q2, t.sha_put_s, t.sha_put_e, "shap")
                t.sha_put_f = q3.number_input("주기 (개월)", value=float(t.sha_put_f),
                                              step=1.0, key="shapf")
                t.sha_put_yield = st.number_input(
                    "풋 보장수익률 (%)", value=t.sha_put_yield*100, step=0.5,
                    format="%.4f",
                    help="행사금액 = 투자원금 × (1 + 보장수익률 복리). 계약서의 「연 복리 "
                         "X% 를 가산한 금액」 조항이 여기입니다.")/100
                t.sha_put_cmp = int(st.number_input(
                    "풋 보장 복리 횟수 (연)", value=int(t.sha_put_cmp), step=1,
                    min_value=0, max_value=12, help=HLP_CMP))
                st.caption(f"첫 행사일({t.sha_put_s:,.0f}개월) 행사금액 = "
                           f"**{100*(1+accrue_rate(t.sha_put_s/12, t.sha_put_yield, 0.0, t.sha_put_cmp)):,.4f}**"
                           "　(투자원금 100 기준)")
                st.divider()
                st.markdown("**최대주주 콜옵션** — 투자자 지분을 사 갈 권리")
                if not _DATE_MODE:
                    st.caption("시작이 종료보다 크면 콜이 없는 계약입니다 (둘 다 0 이면 없음).")
                r1, r2, r3 = st.columns(3)
                if _DATE_MODE and st.checkbox("콜 없음", value=not (t.sha_call_e > 0 and t.sha_call_s <= t.sha_call_e),
                                              key="shac_none"):
                    t.sha_call_s, t.sha_call_e = 0.0, 0.0
                else:
                    if t.sha_call_e <= 0 or t.sha_call_s > t.sha_call_e:
                        t.sha_call_s, t.sha_call_e = 0.0, max(12.0, t.T*12 + t.elapsed_m)
                    t.sha_call_s, t.sha_call_e = _sched_pair(r1, r2, t.sha_call_s, t.sha_call_e, "shac")
                t.sha_call_f = r3.number_input("주기 (개월)", value=float(t.sha_call_f),
                                               step=1.0, key="shacf")
                t.sha_call_prem = st.number_input(
                    "콜 행사금액 가산율 (%)", value=t.sha_call_prem*100, step=0.5,
                    format="%.4f",
                    help="행사금액 = 투자원금 × (1 + 가산율 복리).")/100
                t.sha_call_cmp = int(st.number_input(
                    "콜 가산 복리 횟수 (연)", value=int(t.sha_call_cmp), step=1,
                    min_value=0, max_value=12, help=HLP_CMP))

            # 두 권리가 서로를 소멸시키는가. 따로 재면 공존할 수 없는 두 미래를
            # 각각 값에 넣게 된다 — 행사확률 합이 1 을 넘는 것으로 드러난다.
            t.sha_kill = int(st.selectbox(
                "한쪽이 행사하면 다른 쪽은", [0, 1], index=int(t.sha_kill),
                format_func=lambda x: ("그대로 남는다 — 두 권리를 따로 잰다" if x == 0
                                       else "함께 소멸한다 — 한 격자에서 함께 푼다"),
                help="계약서에 「풋 행사로 주식이 이전되면 콜은 소멸한다」 같은 조항이 "
                     "있으면 두 권리는 경제적으로 독립이 아닙니다. 따로 재면 「풋은 콜이 "
                     "살아 있다고 보고, 콜은 풋이 살아 있다고 보는」 공존할 수 없는 두 "
                     "미래를 각각 값에 넣게 됩니다. 「검산」 탭의 행사확률 합이 1 을 "
                     "넘으면 그 증거입니다."))

            with st.expander("적격상장(Q-IPO) 연계", expanded=True):
                t.ipo_on = int(st.checkbox("적격상장 조항을 격자에 넣는다",
                                           value=bool(t.ipo_on)))
                if t.ipo_on:
                    t.ipo_m = _sched_one(st, "적격상장 기한 (개월)", "적격상장 기한일", t.ipo_m, "qipom")
                    t.ipo_min = st.number_input(
                        "적격 판정 최소 주가 (원)", value=float(t.ipo_min), step=100.0,
                        help="그 시점 주가가 이 값을 넘으면 적격상장이 이루어진 것으로 "
                             "봅니다. 계약의 「적격상장」 정의(공모가·시가총액 기준)를 "
                             "주당으로 환산해 넣으십시오.")
                    t.sha_qipo_kill = int(st.selectbox(
                        "적격상장이 되면", [0, 1], index=int(t.sha_qipo_kill),
                        format_func=lambda x: ("풋만 소멸 — 콜은 남는다" if x == 0
                                               else "풋·콜 모두 소멸"),
                        help="적격상장하면 투자자가 시장에서 팔 수 있으므로 풋이 소멸하는 "
                             "것이 보통입니다. 콜도 함께 끝나는지는 계약마다 다릅니다."))
                    st.caption("상장 성공은 **그 노드의 주가**가 최소 주가를 넘는지로 "
                               "판정합니다. 상장 확률을 따로 넣지 않습니다 — 확률은 격자가 "
                               "이미 담고 있습니다.")

            with st.expander("의무자 · 할인율", expanded=True):
                t.sha_writer = int(st.selectbox(
                    "풋 의무자 (누가 사 주는가)", [0, 1, 2], index=int(t.sha_writer),
                    format_func=lambda x: ["최대주주 (발행회사는 당사자가 아니다)",
                                           "발행회사 (자기지분상품 매입의무)",
                                           "최대주주 · 발행회사 연대"][x],
                    help="발행회사가 의무자면 자기지분상품을 매입할 의무라 기준서 1032 "
                         "문단 23 이 걸립니다 — 옵션 공정가치가 아니라 **상환금액의 "
                         "현재가치를 총액으로** 금융부채에 싣고 자본에서 뺍니다."))
                t.sha_disc = int(st.selectbox(
                    "풋 할인율", [0, 1, 2], index=int(t.sha_disc),
                    format_func=lambda x: ["무위험 곡선",
                                           "위험 곡선 (아래 이자율 칸의 위험 곡선)",
                                           "무위험 + 스프레드"][x],
                    help="풋은 **현금을 받을 권리**라 의무자의 신용위험이 붙습니다. "
                         "콜은 주식을 받을 권리라 인도 위험이 사실상 없어 늘 무위험으로 "
                         "평가합니다."))
                if t.sha_disc == 2:
                    t.sha_spread = st.number_input(
                        "스프레드 (%)", value=t.sha_spread*100, step=0.1,
                        format="%.4f")/100

        with st.expander("변동성", expanded=True):
            # 상장사면 대상회사 주가로, 비상장사면 피어로 잰다. 쓰지 않는 쪽 칸은 잠근다 — 비상장인데
            # 대상회사 칸의 예시 종목 주가가 올라와 σ 로 적용되거나 주가 배수 경고가 뜨던 자리다.
            _lst = st.radio("대상회사", ["상장사", "비상장사"], horizontal=True, key="vol_listed",
                            index=(0 if vol_listed() else 1),
                            help="**상장사** — 대상회사 주가로 변동성을 산출합니다. 피어 칸은 잠깁니다.\n\n"
                                 "**비상장사** — 유사기업(피어) 주가로 평가합니다. 대상회사 주가 칸은 "
                                 "잠깁니다.\n\n조서의 변동성 산출내역과 「적용하지 않은 변동성」 "
                                 "경고도 고른 쪽 자료만 봅니다.") == "상장사"
            _tg = not _lst                     # 대상회사 칸 잠금
            st.markdown("**상장 — 대상회사 주가로 산출**")
            if _tg:
                st.caption("비상장사를 고르셨습니다 — 이 칸은 쓰지 않습니다. 아래 피어 칸에서 산출하십시오.")
            c1, c2 = st.columns([2, 1])
            code = c1.text_input("종목코드 · 티커", value=t.ticker,
                                 help="국내는 6자리 숫자, 해외는 티커. 「기본」의 종목코드를 따라옵니다.",
                                 key="vol_code", disabled=_tg)
            mkt = c2.selectbox("시장", ["KQ", "KS", ""], disabled=_tg,
                               index=["KQ", "KS", ""].index(st.session_state.get("s0_mkt", "KQ")),
                               format_func=lambda x: {"KQ": "코스닥", "KS": "코스피", "": "해외"}[x],
                               key="vol_mkt")
            c3, c4 = st.columns(2)
            pdays = int(c3.number_input("조회 일수", value=250, step=10, min_value=30, disabled=_tg))
            tdays = int(c4.number_input(
                "연 거래일수", value=250, step=5, disabled=_tg,
                help="1년에 며칠 거래하나입니다. 국내 증시는 약 245~250일입니다. "
                     "**받아온 자료가 며칠치인가(조회 일수)와 다릅니다.** 여기에 "
                     "관측 개수를 넣으면 연환산이 어긋납니다."))
            if not 200 <= tdays <= 300:
                st.warning(f"연 거래일수가 **{tdays}일** 입니다. 국내 증시는 약 "
                           "245~250일입니다. 이 칸은 「1년에 며칠 거래하나」이지 "
                           "「몇 일치를 받아왔나」가 아닙니다 — 그건 위의 **조회 "
                           f"일수** 칸입니다. 지금 값이면 σ 가 √({tdays}÷250) = "
                           f"{(tdays/250)**0.5:.3f} 배로 나옵니다.")
            st.caption("야후 파이낸스 수정주가를 씁니다. 유상증자·액면분할·배당이 반영된 종가입니다.")
            # 평가기준일까지의 주가로 변동성을 잰다. 기준일 뒤의 값이 섞이면 결산일 평가가 아니다.
            asof = st.date_input("조회 종료일", value=(dt.date.today() if is_blank("d_base") else dt.date.fromisoformat(t.d_base)), disabled=_tg,
                                 help="기본은 평가기준일입니다. 기준일 뒤 주가로 변동성을 재면 안 됩니다.")
            drop = st.checkbox("이상치 제거 (중앙값 절대편차 2.5배)", value=True, disabled=_tg,
                               help="MAD × 1.4826 × 2.5 밖의 일간수익률을 뺍니다. "
                                    "책 사례 5-2 와 같은 배수입니다. 대상회사 주가에만 씁니다 — "
                                    "피어는 아래 피어 칸에서 따로 고릅니다.")
            if st.button("주가 수집", use_container_width=True, type="secondary", disabled=_tg):
                with st.spinner("받는 중"):
                    try:
                        px, src = fetch_prices(code.strip(), pdays, mkt, asof.isoformat())
                        st.session_state.prices = px
                        st.session_state.px_src = src
                        st.success(f"{src} · {len(px)}개 · {px[0][0]} ~ {px[-1][0]}")
                    except Exception as ex:
                        st.error(f"받지 못했습니다 — {ex}\n\n"
                                 "종목코드와 시장을 확인하시거나 아래에서 파일을 넣으십시오.")
            pf = st.file_uploader("주가 파일 (엑셀 · csv · txt)", disabled=_tg,
                                  type=["xlsx", "xlsm", "xls", "csv", "txt", "tsv"], key="pxf")
            if pf is not None:
                try:
                    rows = parse_prices(read_upload(pf.name, pf.getvalue()))
                except Exception as ex:
                    st.error(str(ex))
                else:
                    if len(rows) >= 10:
                        st.session_state.prices = rows
                        st.session_state.px_src = pf.name
                        st.success(f"{pf.name} · {len(rows)}개")
                    else:
                        st.error(f"종가를 {len(rows)}개밖에 찾지 못했습니다. "
                                 "머리글에 '종가' 또는 'Close' 가 있는지 확인하십시오.")
            if st.session_state.prices and not _tg:
                v = vol_from(st.session_state.prices, tdays, drop)
                if v:
                    st.metric("연 변동성", f"{v['annual']*100:.2f}%",
                              f"일 {v['daily']*100:.2f}%")
                    st.caption(f"{st.session_state.get('px_src','')} · 수익률 {v['n']}개"
                               + (f" · 이상치 {v['removed']}개 제거 "
                                  f"(정상범위 {v['lo']*100:.2f}% ~ {v['hi']*100:.2f}%)"
                                  if v['removed'] else ""))
                    if abs(v["annual"] - t.sig) > 5e-5 or is_blank("sig"):
                        st.warning(f"아직 **적용하지 않았습니다**. 지금 변동성 칸은 "
                                   + ("**비어 있습니다**" if is_blank("sig") else f"**{t.sig*100:.2f}%** 입니다")
                                   + ". 아래 단추를 누르셔야 산출한 값이 계산에 들어갑니다.")
                    if st.button("이 변동성 적용", use_container_width=True, type="primary"):
                        t.sig = v["annual"]; bfill("sig")
                        st.rerun()

            st.divider()
            st.markdown("**비상장 — 피어로 산출**")
            _pg = _lst                          # 피어 칸 잠금
            if _pg:
                st.caption("상장사를 고르셨습니다 — 이 칸은 쓰지 않습니다. 위 대상회사 주가로 산출하십시오.")
            else:
                st.caption("대상회사 주가가 없으면 유사기업 여럿의 변동성을 모아 씁니다. "
                           "업종·규모·상장기간이 비슷한 회사를 고르고, 왜 골랐는지 조서에 남기십시오.")
            ptxt = st.text_area("피어 목록 — 한 줄에 하나, `코드` 또는 `코드,이름`", disabled=_pg,
                                value=st.session_state.get("peer_txt", ""),
                                height=90, placeholder="122870,와이지엔터\n035900,JYP\n041510,SM")
            st.session_state.peer_txt = ptxt
            # 시장은 고르지 않는다. 피어는 코스닥·코스피가 섞이기 마련이라 한 칸으로 정할 수
            # 없고, 6자리 코드는 fetch_prices 가 코스닥 → 코스피 순으로 알아서 찾는다.
            st.caption("시장은 고르지 않습니다 — 6자리 코드는 코스닥·코스피를 차례로 찾고, "
                       "해외 티커는 그대로 씁니다.")
            # 대상회사와 같은 세 칸을 피어에도 따로 둔다 — 피어 관측기간을 계약 잔존기간에
            # 맞추거나(예: 180영업일) 기준일을 달리 잡는 일이 흔하다.
            pc1, pc2 = st.columns(2)
            p_pdays = int(pc1.number_input("조회 일수", value=250, step=10, min_value=30,
                                           key="p_pdays", disabled=_pg,
                                           help="피어마다 받아올 거래일 수입니다."))
            p_tdays = int(pc2.number_input(
                "연 거래일수", value=250, step=5, key="p_tdays", disabled=_pg,
                help="1년에 며칠 거래하나입니다. 국내 증시는 약 245~250일입니다. "
                     "**받아온 자료가 며칠치인가(조회 일수)와 다릅니다.**"))
            if not 200 <= p_tdays <= 300:
                st.warning(f"피어 연 거래일수가 **{p_tdays}일** 입니다. 이 칸은 「1년에 며칠 "
                           "거래하나」이지 「몇 일치를 받아왔나」가 아닙니다 — 그건 **조회 일수** "
                           f"칸입니다. 지금 값이면 σ 가 √({p_tdays}÷250) = "
                           f"{(p_tdays/250)**0.5:.3f} 배로 나옵니다.")
            p_asof = st.date_input("조회 종료일", value=(dt.date.today() if is_blank("d_base")
                                                        else dt.date.fromisoformat(t.d_base)),
                                   key="p_asof", disabled=_pg,
                                   help="기본은 평가기준일입니다. 기준일 뒤 주가로 변동성을 "
                                        "재면 안 됩니다.")
            if p_asof > dt.date.fromisoformat(t.d_base):
                st.warning("조회 종료일이 평가기준일보다 뒤입니다. 기준일 뒤 주가가 섞입니다.")
            # 이상치 제거도 피어 칸에서 따로 고른다 — 대상회사 칸과 다르게 둘 수 있어야 한다
            p_drop = st.checkbox("이상치 제거 (중앙값 절대편차 2.5배)", value=True, key="p_drop",
                                 disabled=_pg,
                                 help="피어마다 MAD × 1.4826 × 2.5 밖의 일간수익률을 뺍니다. "
                                      "책 사례 5-2 와 같은 배수입니다. 전기 평가와 같은 방식으로 "
                                      "맞추십시오.")
            vpick = st.selectbox("종합 방법", ["median", "mean", "max", "min"], disabled=_pg,
                                 format_func=lambda x: {"median": "중앙값", "mean": "단순평균",
                                                        "max": "최댓값", "min": "최솟값"}[x])
            if st.button("피어 주가 수집", use_container_width=True, disabled=_pg):
                got, fail = [], []
                with st.spinner("받는 중"):
                    for line in ptxt.splitlines():
                        line = line.strip()
                        if not line: continue
                        parts = [x.strip() for x in line.replace("\t", ",").split(",")]
                        code = parts[0]
                        nm = parts[1] if len(parts) > 1 and parts[1] else code
                        try:
                            rows, _src = fetch_prices(code, p_pdays, "", p_asof.isoformat())
                            got.append((nm, rows))
                        except Exception as ex:
                            fail.append(f"{nm} — {ex}")
                st.session_state.peers = got
                if got: st.success(f"{len(got)}개 수집 · " + " · ".join(n for n, _ in got))
                if fail: st.error("못 받은 것: " + " / ".join(fail))
            mf = st.file_uploader("여러 종목 종가 파일 (첫 열 일자, 나머지 열 종목)", disabled=_pg,
                                  type=["xlsx", "xlsm", "csv", "txt", "tsv"], key="mpxf")
            if mf is not None:
                try:
                    got = parse_prices_multi(read_upload(mf.name, mf.getvalue()))
                except Exception as ex:
                    st.error(str(ex))
                else:
                    if got:
                        st.session_state.peers = got
                        st.success(f"{mf.name} · {len(got)}개 — "
                                   + " · ".join(n for n, _ in got))
                    else:
                        st.error("종목별 종가를 찾지 못했습니다. 첫 줄이 머리글이고 "
                                 "첫 열이 일자인지 확인하십시오.")
            peers = st.session_state.get("peers") or []
            st.session_state.peer_agg = None
            if peers and not _pg:
                pv = [(nm, vol_from(px, p_tdays, p_drop)) for nm, px in peers]
                pv = [(nm, x) for nm, x in pv if x]
                if pv:
                    ann = sorted(x["annual"] for _, x in pv)
                    agg = {"median": (ann[len(ann)//2] if len(ann) % 2
                                      else (ann[len(ann)//2-1]+ann[len(ann)//2])/2),
                           "mean": sum(ann)/len(ann), "max": ann[-1], "min": ann[0]}[vpick]
                    st.dataframe(pd.DataFrame(
                        [[nm, x["annual"], x["n"], x["removed"]] for nm, x in pv],
                        columns=["회사", "연 변동성", "수익률", "제외"]).style.format(
                        {"연 변동성": "{:.2%}"}), use_container_width=True, hide_index=True)
                    st.metric("피어 종합", f"{agg*100:.2f}%",
                              {"median": "중앙값", "mean": "단순평균",
                               "max": "최댓값", "min": "최솟값"}[vpick])
                    st.session_state.peer_agg = agg
                    if abs(agg - t.sig) > 5e-5 or is_blank("sig"):
                        st.warning(f"아직 **적용하지 않았습니다**. 지금 변동성 칸은 "
                                   + ("**비어 있습니다**" if is_blank("sig") else f"**{t.sig*100:.2f}%** 입니다")
                                   + ". 아래 단추를 누르셔야 피어 종합값이 계산에 들어갑니다.")
                    if st.button("피어 종합 적용", use_container_width=True, type="primary"):
                        t.sig = agg; bfill("sig")
            st.session_state.vol_opt = dict(tdays=tdays, drop=drop, pick=vpick,
                                            asof=asof.isoformat(), days=pdays)
            # 비상장이면 산출내역 조서도 피어의 설정으로 적는다 (vol_attach).
            st.session_state.peer_opt = dict(tdays=p_tdays, drop=p_drop, pick=vpick,
                                             asof=p_asof.isoformat(), days=p_pdays)
            _v = st.number_input("변동성 (%)", value=bval("sig", t.sig*100), step=0.5)
            t.sig = bget("sig", _v, t.sig, "변동성", scale=100)
            # 배당수익률은 위험중립 드리프트에서 빠진다. 배당은 주주에게 가고
            # 전환 전 투자자는 받지 못하므로 전환권·신주인수권이 그만큼 싸진다.
            # 비상장 성장기업은 0 이 보통이지만 상장 배당기업이면 넣어야 한다.
            t.div_y = st.number_input(
                "보통주 배당수익률 δ (%)", value=t.div_y*100, step=0.1,
                min_value=0.0,
                help="배당은 주주에게 가고 전환 전 투자자는 받지 못합니다. "
                     "0 으로 두면 전환권·신주인수권을 그만큼 과대평가합니다. "
                     "위험중립 드리프트에서만 빠지고 할인율에는 닿지 않습니다.")/100

        with st.expander("이자율", expanded=True):
            st.caption("무위험·위험 모두 만기수익률(YTM) 곡선을 넣습니다. "
                       "앱이 선형보간 → 부트스트래핑 → 선도이자율 순으로 처리합니다.")
            t.y_type = st.selectbox("입력 유형", ["par", "spot"],
                                    index=0 if t.y_type == "par" else 1,
                                    format_func=lambda x: "만기수익률 (YTM)" if x == "par"
                                    else "현물이자율 (zero rate)")
            if t.y_type == "spot":
                st.caption("아래 **이표 횟수**를 고시된 곡선의 **복리 횟수**로 맞추십시오. "
                           "회사채 제로커브가 분기복리인데 연복리로 두면 할인계수가 어긋납니다.")
            unit = st.selectbox("만기 단위", ["auto", "month", "year"], index=0,
                                format_func=lambda x: {"auto": "자동 인식", "month": "개월",
                                                       "year": "년"}[x])
            cc1, cc2 = st.columns(2)
            t.cmp_rf = int(cc1.number_input("무위험 이표 (연 회)", value=int(t.cmp_rf),
                                            step=1, min_value=1, max_value=12))
            t.cmp_cr = int(cc2.number_input("위험 이표 (연 회)", value=int(t.cmp_cr),
                                            step=1, min_value=1, max_value=12))
            st.caption("국고채는 6개월 이표(2회), 회사채는 3개월 이표(4회)가 발행 관행입니다. "
                       "부트스트래핑에서 현금흐름 시점을 잡는 데 쓰입니다.")
            # ── KIS-Net 기준수익률 표에서 바로 채우기 ──
            kf = st.file_uploader("KIS-Net 기준수익률 표 (선택)", type=["xls", "xlsx"],
                                  key="kisnet",
                                  help="채권시가평가 기준수익률 표를 그대로 올리면 "
                                       "무위험·위험 곡선을 골라 아래 칸에 채웁니다.")
            if kf is not None:
                try:
                    _rows = read_kisnet(kf.name, kf.getvalue())
                except Exception as ex:
                    st.error(f"읽지 못했습니다 — {ex}")
                else:
                    _lbl = [x[0] for x in _rows]

                    def _first(*cands):
                        """앞에서부터 걸리는 첫 줄. 0 번이 정답일 수 있어 None 으로 가른다."""
                        for ws in cands:
                            for i, L in enumerate(_lbl):
                                if all(w in L for w in ws): return i
                        return 0

                    st.success(f"{len(_rows)}개 곡선을 찾았습니다.")
                    k1, k2 = st.columns(2)
                    _ri = k1.selectbox(
                        "무위험 곡선 (국공채)", range(len(_rows)),
                        index=_first(("국채", "국고채권"), ("국채",)),
                        format_func=lambda i: _lbl[i])
                    _ci = k2.selectbox(
                        "위험 곡선 (신용위험 반영)", range(len(_rows)),
                        index=_first(("회사채 II", "무보증"), ("회사채", "무보증"), ("회사채",)),
                        format_func=lambda i: _lbl[i])
                    st.caption("사모 CB 는 **회사채 II(사모사채)** 줄이 성격에 가깝습니다. "
                               "무등급이면 추정 등급을 고르고 근거를 조서에 남기십시오.")
                    if st.button("이 곡선 적용", use_container_width=True, type="primary"):
                        st.session_state.rf_txt = curve_text(_rows[_ri][1])
                        st.session_state.cr_txt = curve_text(_rows[_ci][1])
                        st.session_state.kis_src = (_lbl[_ri], _lbl[_ci])
                        # 아래 직접 입력 칸이 «어디서 온 곡선인지» 알아야 조서에 출처를
                        # 그대로 실을 수 있다. 채워 넣은 원문을 함께 남겨 두고, 손으로
                        # 고쳤는지 그 원문과 대조해 가른다.
                        st.session_state.cr_applied = (st.session_state.cr_txt, _lbl[_ci])
                        st.rerun()
                    # 등급 보간 칸이 같은 표에서 곡선을 고를 수 있도록 남긴다
                    st.session_state.kis_rows = _rows
            if st.session_state.get("kis_src"):
                st.caption("적용된 곡선 — 무위험 **%s** · 위험 **%s**"
                           % st.session_state["kis_src"])

            # 두 곡선 모두 같은 형식이다 — 만기(개월) 다음에 수익률(%).
            # 날짜 열이 앞에 붙어 있어도 그대로 읽는다.
            st.caption("형식은 두 곡선이 같습니다 — 한 줄에 **만기 · 수익률**. "
                       "고시표를 날짜 열까지 통째로 붙여 넣어도 날짜는 알아서 버립니다.")
            # 첫 실행에는 곡선을 비워 둔다. 예시 곡선이 채워져 있으면 평가기준일 곡선으로 바꾸지
            # 않은 채 조서가 나간다 — 빈칸이면 계산이 멈추고 곡선을 넣으라고 알린다.
            if "rf_txt" not in st.session_state: st.session_state.rf_txt = ""
            if "cr_txt" not in st.session_state: st.session_state.cr_txt = ""
            rf_txt = st.text_area("무위험 곡선 (국공채 YTM)", key="rf_txt", height=130,
                                  placeholder="만기(개월)  수익률 — 예)\n3\t2.40%\n12\t2.25%\n60\t2.50%\n120\t2.80%")
            t.rf_curve = parse_yields(rf_txt, unit)
            _MODES = ["direct", "rating"]
            _MODE_NM = {"direct": "YTM 직접 입력", "rating": "두 등급 곡선으로 보간"}
            if t.rate_mode not in _MODES: t.rate_mode = "direct"
            t.rate_mode = st.selectbox("위험 곡선", _MODES,
                                       index=_MODES.index(t.rate_mode),
                                       format_func=lambda x: _MODE_NM[x],
                                       help="고시표를 올려 **「이 곡선 적용」** 을 누르면 그 곡선이 "
                                            "아래 직접 입력 칸에 들어옵니다 — 그것이 가장 짧은 "
                                            "길입니다. 평가대상 등급이 고시표에 없을 때만 "
                                            "**두 등급 보간**을 쓰십시오.")
            _kr = st.session_state.get("kis_rows") or []
            _kg = [(i, rating_in(L)) for i, (L, _) in enumerate(_kr)]
            _kg = [(i, g) for i, g in _kg if g]
            _pick = sorted({g for _, g in _kg}, key=rating_idx)

            if t.rate_mode == "direct":
                cr_txt = st.text_area("위험 곡선 (등급별 회사채 YTM)", key="cr_txt", height=130,
                                      placeholder="만기(개월)  수익률 — 예)\n12\t9.40%\n60\t14.90%\n120\t16.50%")
                t.cr_curve = parse_yields(cr_txt, unit)
                t.cr_curve_b = []
                # 출처를 지우지 않는다. 고시표에서 받은 곡선을 「직접 입력」이라고 적으면
                # 조서가 근거를 잃는다 — 손으로 고쳤는지만 가려 적는다.
                _ap = st.session_state.get("cr_applied")
                if _ap and _ap[0].strip() == (cr_txt or "").strip():
                    t.cr_src = _ap[1]
                    _g = rating_in(t.cr_src)
                    if _g: t.rt_tgt = _g                  # 줄 이름에 등급이 있으면 평가대상도
                    st.caption(f"출처 · 고시표 **{t.cr_src}** 을 그대로 씁니다. "
                               "이 칸을 고치면 「직접 입력」으로 바뀝니다.")
                else:
                    t.cr_src = ("직접 입력 (고시표를 고쳤음)" if _ap else "직접 입력")
            else:
                # 표를 올리셨으면 **그 표에 있는 등급만** 고르게 한다. 고시표에 없는
                # 등급을 곡선으로 고르면 붙여 넣을 자료가 없다 — 무보증 공모사채는
                # 대개 BBB- 까지만 고시한다.
                _opts = _pick or RATINGS
                _at = lambda r, d: _opts.index(r) if r in _opts else min(d, len(_opts)-1)
                r1, r2, r3 = st.columns(3)
                # 표가 없으면 종전 기본값(BBB+ · BBB-), 있으면 가장 위·아래 등급
                t.rt_a = r1.selectbox("곡선 A", _opts, index=_at(t.rt_a, 0 if _pick else 7))
                t.rt_b = r2.selectbox("곡선 B", _opts,
                                      index=_at(t.rt_b, len(_opts)-1 if _pick else 9))
                t.rt_tgt = r3.selectbox(
                    "평가대상", RATINGS,
                    index=RATINGS.index(t.rt_tgt) if t.rt_tgt in RATINGS else 8,
                    help="곡선 두 개 사이면 내삽, 밖이면 같은 기울기로 외삽합니다. "
                         "평가대상은 표에 없어도 고를 수 있습니다.")
                if _pick:
                    st.caption("올리신 표에 있는 등급 — **" + " · ".join(_pick)
                               + "**. 두 곡선은 이 안에서만 고를 수 있습니다.")
                    if st.button("고른 두 등급으로 채우기", use_container_width=True):
                        _byg = {}
                        for i, g in _kg: _byg.setdefault(g, i)
                        st.session_state.ca_txt = curve_text(_kr[_byg[t.rt_a]][1])
                        st.session_state.cb_txt = curve_text(_kr[_byg[t.rt_b]][1])
                        st.rerun()
                if "ca_txt" not in st.session_state: st.session_state.ca_txt = ""
                if "cb_txt" not in st.session_state: st.session_state.cb_txt = ""
                ca_txt = st.text_area(f"{t.rt_a} 곡선", key="ca_txt", height=100,
                                      placeholder="만기(개월)  수익률")
                cb_txt = st.text_area(f"{t.rt_b} 곡선", key="cb_txt", height=100,
                                      placeholder="만기(개월)  수익률")
                t.cr_curve = parse_yields(ca_txt, unit)
                t.cr_curve_b = parse_yields(cb_txt, unit)
                t.cr_src = f"{t.rt_a}·{t.rt_b} 두 등급 보간 → {t.rt_tgt}"
                ia, ib, it2 = rating_idx(t.rt_a), rating_idx(t.rt_b), rating_idx(t.rt_tgt)
                if ia >= 0 and ib >= 0 and it2 >= 0 and ia != ib:
                    w = (it2-ia)/(ib-ia)
                    if not 0 <= w <= 1:
                        st.warning(f"평가대상 **{t.rt_tgt}** 가 두 곡선 **밖**입니다 "
                                   f"(가중치 {w:.2f}). 등급 간 스프레드는 아래로 갈수록 "
                                   "가속해서 벌어지므로, 직선으로 뻗는 외삽은 금리를 "
                                   "낮게 잡습니다. 평가대상을 사이에 끼우는 등급을 "
                                   "받아 오시는 편이 낫습니다.")
                    st.caption(f"가중치 — {t.rt_a} {1-w:.0%} · {t.rt_b} {w:.0%}"
                               + ("   (등급 범위 밖이라 외삽합니다)" if not 0 <= w <= 1 else ""))
            cc = credit_curve(t)
            if not t.rf_curve and not cc:
                st.info("곡선이 비어 있습니다 — 평가기준일의 무위험·위험 곡선을 넣으십시오 "
                        "(한 줄에 「만기(개월) 수익률」).")
            elif len(t.rf_curve) < 2 or len(cc) < 2:
                st.error(f"읽힌 줄 — 무위험 {len(t.rf_curve)}개, 위험 {len(cc)}개. "
                         "만기가 다른 값이 각각 두 개 이상 필요합니다.")
            elif any_blank("d_issue", "d_mat", "d_base"):
                st.success("곡선을 읽었습니다. 날짜를 넣으면 잔존기간의 금리를 보여 줍니다.")
            else:
                RFq, CRq = curves(t)
                st.success(f"부트스트래핑 완료 · {t.T:.2f}년 무위험 {math.exp(RFq(t.T))-1:.2%} "
                           f"위험 {math.exp(CRq(t.T))-1:.2%} "
                           f"(스프레드 {math.exp(CRq(t.T))-math.exp(RFq(t.T)):.2%})")
                # 예시 곡선이 남았거나 곡선이 잔존기간에 못 미치면 여기서 바로 알린다
                for _cn in curve_notes(t): st.warning(_cn)

        if _MISS:
            st.caption("빈칸이 남아 있어 시나리오를 저장할 수 없습니다 — 예시 숫자가 섞여 저장되지 "
                       "않게 하기 위해서입니다.")
        if st.session_state.get("_miss_prev") != _MISS:
            _was = st.session_state.get("_miss_prev")
            st.session_state._miss_prev = list(_MISS)
            # 역산 버튼은 위에 있어 직전 실행의 목록을 본다. 마지막 칸을 채운 순간 한 번 더 그린다.
            if _was is not None and not [x for x in _MISS if x != "평가기준일 주가"]:
                st.rerun()
        st.download_button("시나리오 저장",
                           json.dumps({**asdict(t), "_schema": SCHEMA_VER,
                                       "_meta": {**run_stamp(t),
                                                 "scen_md5": scen_stamp(asdict(t))}},
                                      ensure_ascii=False, indent=2).encode(),
                           f"{lbl(t)['short']}평가_시나리오_{dt.date.today()}.json",
                           "application/json",
                           use_container_width=True, disabled=bool(_MISS))

    # ── 계산 ──
    t = st.session_state.tm
    # 변동성 시계열의 마지막 값을 함께 넘긴다 — 원주가(S0)와 수정주가의 배수가
    # corporate action 의 가장 싼 탐지 신호다.
    _pxl = None
    try:
        # 비상장이면 대상회사 시계열이 없다 — 남아 있는 예전 시계열로 주가 배수를 따지지 않는다
        _pxl = (float(st.session_state.prices[-1][1])
                if (st.session_state.get("prices") and vol_listed()) else None)
    except Exception:
        _pxl = None
    _need = list(dict.fromkeys(_MISS))
    _nocurve = len(t.rf_curve) < 2 or len(credit_curve(t)) < 2
    if _need or _nocurve:
        _msg = ["**계산을 시작하려면 아래를 채우십시오.** 첫 실행에는 예시 숫자 대신 빈칸으로 "
                "시작합니다 — 예시 값이 조서에 섞여 나가지 않게 하기 위해서입니다. 전기 평가의 "
                "시나리오 파일을 불러오면 저장된 값이 모두 채워집니다."]
        if _need:
            _msg.append("- **아직 넣지 않은 칸** — " + ", ".join(_need)
                        + ". 이자·배당이 없으면 0 을 넣으십시오.")
        if _nocurve:
            _msg.append("- **이자율 곡선** — 입력화면 「이자율」 칸에 무위험 곡선과 위험 곡선을 각각 "
                        "두 만기 이상 넣으십시오. KIS-Net 기준수익률 표를 올리고 「이 곡선 적용」을 "
                        "누르면 고시된 모든 만기가 들어갑니다.")
        st.info("\n\n".join(_msg))
        st.stop()
    # Detailed tools run only after explicit execution; keep one in-session result.
    _detail_key = json.dumps(asdict(t), sort_keys=True, default=str)
    if st.button("상세 입력으로 계산", key="_legacy_execute"):
        st.session_state._legacy_authorized = _detail_key
    if st.session_state.get("_legacy_authorized") != _detail_key:
        st.info("입력값을 확인한 뒤 ‘상세 입력으로 계산’을 누르십시오. 역산·자료 산출은 각 실행 버튼을 사용합니다.")
        st.stop()
    if st.session_state.get("_legacy_warn_key") != _detail_key:
        st.session_state._legacy_warn = validate(t, _pxl)
        st.session_state._legacy_warn_key = _detail_key
    warn = st.session_state._legacy_warn
    st.caption("기존 회계 참고표·판단 문안은 입력 가정에 따른 초안입니다. 기준서와 계약에 대한 결론은 대화에서 별도로 검토하십시오. 검산의 ‘적합’ 표시는 해당 검사 범위에 한정됩니다.")
    if warn:
        st.warning("확인이 필요합니다\n\n" + "\n".join(f"- {w}" for w in warn))

    if t.sig <= 0:
        st.error("변동성이 0 이라 상승계수와 하락계수가 같아집니다. 격자가 성립하지 "
                 "않으므로 계산을 멈춥니다. 「주가·변동성」 탭에서 σ 를 산출해 "
                 "적용하십시오.")
        st.stop()

else:
    from copy import deepcopy
    t = deepcopy(_shared_run.terms)
    st.session_state.tm = t
    st.session_state.prices = []
    st.session_state.peers = []
    st.session_state.vol_listed = '상장사' if t.ticker else '비상장사'
    _pack = _shared_run.case.market_evidence.get('sig')
    if _pack:
        st.session_state.vol_listed = '상장사' if _pack['listed'] else '비상장사'
        if _pack['listed']:
            st.session_state.prices = _pack['series'][0][1]
            st.session_state.px_src = _pack['source']
            st.session_state.vol_opt = _pack['opt']
        else:
            st.session_state.peers = _pack['series']
        st.session_state.peer_opt = _pack['opt']
        st.session_state.peer_agg = _pack['sigma']
    _detail_key = json.dumps(asdict(t), sort_keys=True, default=str)
    st.session_state._legacy_price = (_shared_run.raw if is_sha(t) else
        tuple(_shared_run.raw[k] for k in ('full', 'b0', 'b1', 'b2', 'ca', 'conv')))
    st.session_state._legacy_price_key = _detail_key
    st.caption('현재 평가와 동일한 입력·계산 결과입니다. 입력 변경은 「입력·시장자료」에서 수행하십시오.')

# ── 주주간계약은 사채가 없어 화면이 통째로 갈린다 ──
if is_sha(t):
    LB = lbl(t)
    _sw = sha_validate(t)
    if _sw:
        st.warning("확인이 필요합니다\n\n" + "\n".join(f"- {w}" for w in _sw))
    if st.session_state.get("_legacy_price_key") != _detail_key:
        with st.spinner("계산 중"):
            st.session_state._legacy_price = sha_engine(t)
        st.session_state._legacy_price_key = _detail_key
    R = st.session_state._legacy_price
    if R["qbad"]:
        _i, _q = R["qbad"][0]
        st.error(
            f"위험중립가중치가 범위를 벗어났습니다 — 어긋난 구간 {len(R['qbad'])}개, "
            f"전체 범위 [{R['qmin']:.4f}, {R['qmax']:.4f}].\n\n"
            f"처음 어긋난 곳은 **스텝 {_i}** (투자일부터 "
            f"{(_i*R['dt']*12 + t.elapsed_m):.1f}개월, q = {_q:.4f}) 입니다.\n\n"
            "그 구간의 선도이자율이 변동성에 비해 가파릅니다. 변동성을 올리거나 "
            "노드 수를 늘리거나, 이자율 곡선을 확인하십시오.")
        st.stop()
    _eqv = 100*t.S0/t.K0
    _hascall = t.sha_call_e > 0 and t.sha_call_s <= t.sha_call_e
    _F = t.face_total/100.0
    if _shared_run is None:
        with _HEAD.container():
            st.title("주주간계약 평가")
            st.caption("투자자가 이미 가진 지분에 붙은 **풋**과 **콜**을 이항격자에서 "
                       "각각 평가합니다. 사채가 없으므로 순차 차감이 아닙니다. 금액은 "
                       "투자원금 100 기준입니다.")
        m1, m2, m3 = st.columns(3)
        m1.metric("투자자 풋옵션", f"{R['put']:,.2f}",
                  help="지분을 보장수익률로 되팔 권리")
        m2.metric("최대주주 콜옵션", f"{R['call']:,.2f}",
                  help="지분을 사 갈 권리. 콜이 없으면 0")
        m3.metric("지분가치", f"{_eqv:,.2f}", help="100 × 주가 ÷ 주당 인수가액")

    _sha_sections = ["구성요소", "회계처리 — 세 관점", "행사 분포",
                     "이자율곡선", "주가·변동성", "민감도", "검산", "조서"]
    _sha_section = st.selectbox("상세 분석 항목", _sha_sections, key="_legacy_sha_section")

    if _sha_section == _sha_sections[0]:
        _crow = ([["－ 최대주주 콜옵션", -R["call"], -R["call"]*_F,
                   "MAX(지분가치 − 행사금액, 0) 을 미국형으로"]]
                 if _hascall else [])
        st.dataframe(pd.DataFrame([
            ["지분가치 (평가기준일)", _eqv, _eqv*_F, "100 × 주가 ÷ 주당 인수가액"],
            ["＋ 투자자 풋옵션", R["put"], R["put"]*_F,
             "MAX(행사금액 − 지분가치, 0) 을 미국형으로"]]
            + _crow
            + [["＝ 투자자 순포지션 참고값", _eqv + R["put"] - R["call"],
                (_eqv + R["put"] - R["call"])*_F,
                "투자 시점 평가면 투자원금(100)과 견줍니다"]],
            columns=["항목", "100 기준", "전액 기준 (원)", "설명"]).style.format(
            {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        st.caption("풋과 콜을 **따로** 평가합니다. 풋은 투자자가, 콜은 최대주주가 고르므로 "
                   "한 격자에서 함께 최적화하면 두 사람을 한 사람으로 만드는 셈이 "
                   "됩니다. 각자의 재무제표에 총액으로 싣는 것도 같은 이유입니다. "
                   "위 표의 «투자자 순포지션 참고값» 은 세 값을 더해 본 참고치이지 하나의 "
                   "금융상품 가치가 아닙니다.")
        _pk0 = 100*(1 + accrue_rate(t.sha_put_s/12, t.sha_put_yield, 0.0, t.sha_put_cmp))
        _pk1 = 100*(1 + accrue_rate(t.sha_put_e/12, t.sha_put_yield, 0.0, t.sha_put_cmp))
        _rows = [["풋 행사기간", f"{t.sha_put_s:,.0f} ~ {t.sha_put_e:,.0f}개월 · "
                              f"{t.sha_put_f:,.0f}개월마다"],
                 ["풋 행사금액", f"{_pk0:,.4f} (첫날) ~ {_pk1:,.4f} (마지막날)"]]
        if _hascall:
            _ck0 = 100*(1 + accrue_rate(t.sha_call_s/12, t.sha_call_prem, 0.0,
                                        t.sha_call_cmp))
            _ck1 = 100*(1 + accrue_rate(t.sha_call_e/12, t.sha_call_prem, 0.0,
                                        t.sha_call_cmp))
            _rows += [["콜 행사기간", f"{t.sha_call_s:,.0f} ~ {t.sha_call_e:,.0f}개월 · "
                                   f"{t.sha_call_f:,.0f}개월마다"],
                      ["콜 행사금액", f"{_ck0:,.4f} ~ {_ck1:,.4f}"]]
        else:
            _rows.append(["콜옵션", "없음 (시작 > 종료)"])
        _rows.append(["풋 할인율", ["무위험 곡선", "위험 곡선",
                                 f"무위험 + {t.sha_spread:.2%}"][int(t.sha_disc)]])
        if int(t.ipo_on):
            _rows.append(["적격상장", f"{t.ipo_m:,.0f}개월 · 최소 주가 {t.ipo_min:,.0f}원 · "
                                   + ("풋·콜 모두 소멸" if int(t.sha_qipo_kill)
                                      else "풋만 소멸")])
        st.dataframe(pd.DataFrame(_rows, columns=["계약 조건", "내용"]),
                     use_container_width=True, hide_index=True)

    if _sha_section == _sha_sections[1]:
        st.write("같은 계약인데 세 사람의 재무제표에 실리는 것이 완전히 다릅니다. "
                 "**풋 의무자**에 따라 달라집니다.")
        acc = sha_accounts(t, R)
        for who in ("발행회사", "최대주주", "투자자"):
            rows, memo = acc[who]
            st.markdown(f"### {who}")
            st.dataframe(pd.DataFrame(
                [[k, v, v*_F] for k, v in rows],
                columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            st.caption(memo)
        _g = R["gross"]
        if _g:
            st.divider()
            st.markdown("### 옵션 공정가치 대 총액 부채 — 얼마나 다른가")
            st.dataframe(pd.DataFrame([
                ["풋옵션 공정가치 (파생상품부채)", R["put"], R["put"]*_F,
                 "최대주주가 의무자일 때"],
                ["상환금액의 현재가치 (금융부채 총액)", _g["pv"], _g["pv"]*_F,
                 "발행회사가 의무자일 때 — 1032 문단 23"],
                ["차이", _g["pv"] - R["put"], (_g["pv"] - R["put"])*_F,
                 "같은 조항인데 이만큼 갈립니다"]],
                columns=["항목", "100 기준", "전액 기준 (원)", "언제"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            st.info(f"첫 행사 가능일({_g['step']}스텝 · {_g['t']:,.2f}년)의 행사금액 "
                    f"**{_g['strike']:,.4f}** 를 그날까지 할인한 값이 "
                    f"**{_g['pv']:,.4f}** 입니다. 기준서 1032 문단 23 은 "
                    "자기지분상품을 매입할 의무에 대해 **상환금액의 현재가치**를 "
                    "부채로 인식하라고 합니다 — 옵션이 내가격일 확률과 무관합니다. "
                    "그래서 옵션 공정가치보다 훨씬 큽니다.")
            st.markdown("**발행회사가 의무자일 때의 분개**")
            st.code(f"[최초 인식]\n"
                    f"차) 자본 (기타자본)               {_g['pv']:>12,.4f}\n"
                    f"    대) 금융부채 (주식매입의무)      {_g['pv']:>12,.4f}\n\n"
                    f"[매기]\n"
                    f"차) 이자비용                     유효이자율 × 장부금액\n"
                    f"    대) 금융부채                   같은 금액\n\n"
                    f"[풋이 행사되지 않고 소멸]\n"
                    f"차) 금융부채                     그때 장부금액\n"
                    f"    대) 자본 (기타자본)             같은 금액\n"
                    f"※ 문단 23 후단 — 소멸하면 자본으로 되돌린다. 손익이 없다.",
                    language=None)

    if _sha_section == _sha_sections[2]:
        st.write("위험중립확률로 잰 행사·소멸 분포입니다. **실제 행사 예측이 아닙니다** "
                 "— 격자의 확률은 기준일 주가와 변동성이 정한 것입니다.")
        _dp, _dc = R["dist_put"], R["dist_call"]
        def _tab(dd, nm, other):
            _ct = dd.get("counter", 0.0)
            tot = dd["ex"] + dd["qipo"] + dd["expire"] + _ct or 1
            rows = [[f"{nm} 행사", dd["ex"]/tot,
                     dd["tex"]/dd["ex"]/R["mper"] if dd["ex"] else None]]
            if _ct > 1e-12:
                rows.append([f"{other} 행사로 소멸", _ct/tot, None])
            rows += [["적격상장으로 소멸", dd["qipo"]/tot, t.ipo_m if t.ipo_on else None],
                     ["행사기간 만료", dd["expire"]/tot, t.T*12 + t.elapsed_m]]
            return rows
        st.markdown("#### 투자자 풋옵션")
        st.dataframe(pd.DataFrame(_tab(_dp, "풋", "콜"),
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        if _hascall:
            st.markdown("#### 최대주주 콜옵션")
            st.dataframe(pd.DataFrame(_tab(_dc, "콜", "풋"),
                columns=["유형", "비중", "평균 시점(개월)"]).style.format(
                {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
                use_container_width=True, hide_index=True)
        if _hascall and not int(t.sha_kill):
            _sum = R["dist_put"]["ex"] + R["dist_call"]["ex"]
            if _sum > 1.0 + 1e-9:
                st.warning(
                    f"풋 행사확률 {R['dist_put']['ex']:.1%} 과 콜 행사확률 "
                    f"{R['dist_call']['ex']:.1%} 을 더하면 **{_sum:.1%}** 입니다. "
                    "두 권리를 따로 재고 있어서, 「풋은 콜이 살아 있다고 보고 콜은 "
                    "풋이 살아 있다고 보는」 공존할 수 없는 두 미래가 각각 값에 "
                    "들어 있다는 뜻입니다. 계약서에 한쪽 행사로 다른 쪽이 소멸한다는 "
                    "조항이 있으면 콜옵션 칸에서 「함께 소멸한다」로 바꾸십시오.")
        st.caption("풋 행사확률이 높다는 것은 그만큼 투자자의 하방이 막혀 있다는 "
                   "뜻입니다. 발행회사가 의무자라면 그 계약은 지분이 아니라 사실상 "
                   "부채이므로, 회계처리 탭의 총액 부채를 함께 보십시오.")

    if _sha_section == _sha_sections[6]:
        st.write("격자가 제대로 섰는지 봅니다.")
        _mart = sum(math.comb(R["n"], j) * R["q"]**j * (1-R["q"])**(R["n"]-j)
                    * R["S"](R["n"], j) for j in range(R["n"]+1))
        _grw = 1.0
        for i in range(R["n"]): _grw *= math.exp(R["rf"](i)*R["dt"])
        _pex0 = (max(R["pk"](0) - _eqv, 0.0) if R["p_on"](0) else 0.0)
        _cex0 = (max(_eqv - R["ck"](0), 0.0) if R["c_on"](0) else 0.0)
        st.dataframe(pd.DataFrame([
            ["위험중립가중치 q · 첫 구간", f"{R['q']:.6f}",
             "적합" if 0 < R["q"] < 1 else "확인 필요"],
            # q 는 구간마다 다시 계산된다. 첫 구간만 실으면 뒤쪽이 깨진 것을
            # 조서에서 알 수 없다.
            ["위험중립가중치 q · 전 구간 범위",
             f"[{R['qmin']:.6f}, {R['qmax']:.6f}]  (구간 {R['n']}개)",
             "적합" if not R["qbad"] else "확인 필요"],
            ["풋 ≥ 즉시 행사가치", f"{R['put'] - _pex0:+.6f}",
             "적합" if R["put"] - _pex0 >= -1e-9 else "확인 필요"],
            ["콜 ≥ 즉시 행사가치", f"{R['call'] - _cex0:+.6f}",
             "적합" if R["call"] - _cex0 >= -1e-9 else "확인 필요"],
            ["풋 ≤ 행사금액 현재가치", f"{R['put']:.6f} ≤ "
             + (f"{R['gross']['pv']:.6f}" if R["gross"] else "—"),
             ("적합" if (not R["gross"] or R["put"] <= R["gross"]["pv"] + 1e-6)
              else "확인 필요")],
            ["풋·콜 모두 0 이상", f"{min(R['put'], R['call']):.6f}",
             "적합" if min(R["put"], R["call"]) >= -1e-9 else "확인 필요"]],
            columns=["검산", "값", "판정"]), use_container_width=True, hide_index=True)
        st.caption("«풋 ≤ 행사금액 현재가치» 는 풋이 아무리 깊은 내가격이라도 "
                   "행사금액을 넘을 수 없다는 상한입니다. 이 줄이 어긋나면 할인율이나 "
                   "행사금액 산식을 보십시오.")

    if _sha_section == _sha_sections[5]:
        st.write("주가와 변동성, 그리고 풋 보장수익률을 흔들어 봅니다.")
        _sc = [0.6, 0.8, 1.0, 1.2, 1.5]
        _rows = []
        for m in _sc:
            t2 = Terms(**asdict(t)); t2.S0 = t.S0*m; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"주가 ×{m:.1f}", 100*t2.S0/t2.K0, r2["put"], r2["call"]])
        for sg in (max(0.05, t.sig-0.15), t.sig, t.sig+0.15):
            t2 = Terms(**asdict(t)); t2.sig = sg; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"σ {sg:.1%}", _eqv, r2["put"], r2["call"]])
        for gy in (max(0.0, t.sha_put_yield-0.04), t.sha_put_yield,
                   t.sha_put_yield+0.04):
            t2 = Terms(**asdict(t)); t2.sha_put_yield = gy; derive(t2)
            r2 = sha_engine(t2)
            _rows.append([f"풋 보장 {gy:.1%}", _eqv, r2["put"], r2["call"]])
        st.dataframe(pd.DataFrame(_rows,
            columns=["가정", "지분가치", "풋", "콜"]).style.format(
            {"지분가치": "{:,.2f}", "풋": "{:,.4f}", "콜": "{:,.4f}"}),
            use_container_width=True, hide_index=True)
        st.caption("풋은 주가가 내려갈수록, 보장수익률이 올라갈수록 커집니다. "
                   "변동성에는 둔합니다 — 이미 깊은 내가격이면 시간가치가 얼마 "
                   "남지 않기 때문입니다.")

    if _sha_section == _sha_sections[3]:
        st.write("입력화면 **이자율** 칸에서 넣은 곡선입니다. 풋은 아래에서 고른 "
                 "할인율로, 콜은 무위험으로 평가합니다.")
        _RF, _CR = curves(t)
        _tt = [i*R["dt"] for i in range(R["n"]+1)]
        st.dataframe(pd.DataFrame(
            [[f"{x:.3f}", _RF(x), _CR(x),
              R["rf"](i) if i < R["n"] else None,
              R["pdisc"](i) if i < R["n"] else None]
             for i, x in enumerate(_tt)],
            columns=["잔존(년)", "무위험 현물", "위험 현물",
                     "무위험 선도", "풋 할인 선도"]).style.format(
            {"무위험 현물": "{:.4%}", "위험 현물": "{:.4%}",
             "무위험 선도": "{:.4%}", "풋 할인 선도": "{:.4%}"}, na_rep="—"),
            use_container_width=True, hide_index=True, height=320)

    if _sha_section == _sha_sections[4]:
        st.write("입력화면 **변동성** 칸에서 산출한 값입니다. 비상장 대상회사면 "
                 "유사기업(피어) 변동성을 쓰고 그 근거를 조서에 남기십시오.")
        st.metric("적용 변동성 σ", f"{t.sig:.2%}")
        _pv2 = (st.session_state.get("prices") or []) if vol_listed() else []
        if _pv2:
            _vv2 = vol_from(_pv2, (st.session_state.get("vol_opt") or {}).get("tdays", 250),
                            (st.session_state.get("vol_opt") or {}).get("drop", True))
            if _vv2:
                st.caption(f"산출값 {_vv2['annual']:.2%} · 관측 {len(_pv2)}건. "
                           "입력화면에서 「이 변동성 적용」을 누르셔야 위 값이 바뀝니다.")

    if _sha_section == _sha_sections[7]:
        st.write("가정 · 주가 · 지분가치 · 풋 · 콜 트리와 결과 · 회계처리로 이루어진 "
                 "조서를 만듭니다. 값 조서와 수식 조서가 **같은 함수**에서 나오므로 "
                 "자리와 차례가 갈라지지 않습니다.")
        skind = st.radio("조서 형식", ["값", "수식"], horizontal=True, key="sha_kind",
                         format_func=lambda x: "값 조서 — 계산 결과 스냅샷"
                         if x == "값" else "수식 조서 — 엑셀에서 다시 계산됨")
        if skind == "값":
            st.caption("앱이 계산한 값을 그대로 담습니다. 입력 가정과 검토기록을 확인한 후 사용하십시오.")
        else:
            st.caption("가정 시트의 노란 셀을 바꾸면 엑셀 안에서 트리가 다시 "
                       "계산됩니다. 선도이자율만 값으로 들어갑니다.")
        if st.button("조서 만들기", type="primary", use_container_width=True,
                     key="sha_build"):
            try:
                with st.spinner("엑셀 작성 중"):
                    _att = dict(px=vol_attach(),
                                rate=None, rate_how="",
                                ir=bool(len(t.rf_curve) >= 2
                                        and len(credit_curve(t)) >= 2))
                    data = build_xlsx_sha(t, R, formula=(skind == "수식"),
                                          attach=_att)
                    fn = f"주주간계약평가조서_{skind}{tranche_tag(t)}_{dt.date.today()}.xlsx"
                data = export_with_sources(data, t)
                st.session_state.report = (fn, data, _stamp(t, skind))
            except ModuleNotFoundError:
                st.error("openpyxl 이 없습니다.  pip install openpyxl  을 실행하십시오.")
            except Exception as ex:
                st.error(f"조서를 만들지 못했습니다 — {ex}")
        rep = st.session_state.get("report")
        if rep and len(rep) == 3 and rep[2] != _stamp(t, skind):
            st.warning("**조서를 만든 뒤 인풋이 바뀌었습니다.** 예전 파일은 지웠으니 "
                       "「조서 만들기」를 다시 누르십시오.")
            st.session_state.pop("report", None)
            rep = None
        if rep:
            fn, data = rep[0], rep[1]
            st.download_button(f"{fn} 내려받기  ({len(data)/1024:,.0f} KB)", data, fn,
                               "application/vnd.openxmlformats-officedocument."
                               "spreadsheetml.sheet",
                               type="primary", key="sha_dl", use_container_width=True)
        st.divider()
        st.caption("**변동성 산출내역은 이 조서 안에 함께 들어갑니다.** 주주간계약은 "
                   "부트스트래핑한 선도이자율을 트리 8·9행에 값으로 담습니다 — 사채 "
                   "현금흐름이 없어 곡선을 다시 풀 자리가 없기 때문입니다.")
    st.stop()

if st.session_state.get("_legacy_price_key") != _detail_key:
    with st.spinner("계산 중"):
        st.session_state._legacy_price = decompose(t)
    st.session_state._legacy_price_key = _detail_key
full, b0, b1, b2, ca, conv = st.session_state._legacy_price

# 첫 구간만 보면 뒤쪽 선도이자율이 튈 때 q 가 0~1 을 벗어난 채로 계산이 끝난다.
# 전 구간을 보고, 어긋난 스텝을 짚어 준다.
if full["qbad"]:
    _i, _q = full["qbad"][0]
    _fr = full["fwdRF"](_i)
    st.error(
        f"위험중립가중치가 범위를 벗어났습니다 — 어긋난 구간 {len(full['qbad'])}개, "
        f"전체 범위 [{full['qmin']:.4f}, {full['qmax']:.4f}].\n\n"
        f"처음 어긋난 곳은 **스텝 {_i}** (발행일부터 "
        f"{(_i*full['dt']*12 + t.elapsed_m):.1f}개월, 구간 선도이자율 "
        f"{math.exp(_fr)-1:.2%}, q = {_q:.4f}) 입니다.\n\n"
        "그 구간의 선도이자율이 변동성에 비해 가파릅니다. 변동성을 올리거나 "
        "노드 수를 늘리거나, 이자율 곡선을 확인하십시오.")
    st.stop()

eq = full["GS"]*full["P"] if t.model == "GS" else full["E"]
dv = full["GS"]*(1-full["P"]) if t.model == "GS" else full["B"]

LB = lbl(t)
_b3 = b2 - ca
if _shared_run is None:
    c1, c2, c3 = st.columns([2, 1, 1])
    with _HEAD.container():
        st.title(f"{LB['inst']} 평가")
        st.caption("계약조건과 시장자료를 넣으면 이항격자로 옵션을 분리해 계산하고 조서를 "
                   f"엑셀로 내보냅니다. 금액은 {LB['unit']}입니다.")
    # 무엇을 「공정가치」로 부를지는 콜의 성격이 정한다. B2 는 콜을 뺀 값이라,
    # 발행자 상환권이 붙은 계약에서 그대로 「공정가치」라고 쓰면 상환권 값만큼
    # 과대표시된다. 제3자 지정 콜은 별도의 금융상품이라 기초상품과 나눠 적는다.
    if not full.get("has_call"):
        c1.metric(f"{LB['inst']} 공정가치 · {t.model}", f"{b2:,.2f}",
                  help="콜 조항이 없어 B2 가 그대로 공정가치입니다.")
    elif issuer_redeem(t):
        c1.metric(f"{LB['inst']} 공정가치 · {LB['call']} 반영 · {t.model}", f"{_b3:,.2f}",
                  help=f"{LB['call']}까지 반영한 B3 입니다. 미반영 B2 는 {b2:,.2f}, "
                       f"{LB['call']} 은 {ca:,.2f} 입니다.")
    else:
        c1.metric(f"{LB['inst']} 기초상품 · {LB['call']} 미반영 (B2) · {t.model}",
                  f"{b2:,.2f}",
                  help=f"{LB['call']} 은 거래상대방이 제3자라 **별도의 금융상품**입니다 "
                       f"(기준서 1109 문단 4.3.1) — 파생상품자산 {ca:,.2f} 로 따로 "
                       f"세웁니다. 차감한 순액 B3 는 {_b3:,.2f} 입니다.")
    c2.metric("지분가치", f"{eq:,.2f}", help="주식으로 받게 될 부분")
    c3.metric("부채가치", f"{dv:,.2f}", help="현금으로 받게 될 부분")

_detail_sections = ["구성요소", "회계처리", "분리 판단", "이자율곡선", "주가·변동성",
                "의사결정", "상각표", "민감도", "검산", "조서"]
if _shared_run is not None:
    _detail_sections[2] = "권리·금리 분석"
_detail_section = st.selectbox("상세 분석 항목", _detail_sections[:-1] if _shared_run is not None else _detail_sections, key="_legacy_section")

if _detail_section == _detail_sections[0]:
    df = pd.DataFrame([
        [f"B0  {LB['host'].split(' (')[0]} — 옵션 없음", b0, None, "—"],
        [f"B1  {LB['put']} 추가", b1, b1-b0, LB["put"]],
        [inst_text(t, "B2  전환권 추가"), b2, b2-b1,
         inst_text(t, "전환권")
         + (" (존속기간 만료 시 자동전환 포함)" if auto_conv(t) else "")
         + (" — 행사해도 사채가 남는다" if bw_cash(t) else "")],
        [f"B3  {LB['call']} 반영", b2-ca, -ca,
         (f"{LB['call']} (전체에 걸림)" if issuer_redeem(t)
          else f"{LB['call']} ({t.k_w*100:.0f}% 한도)")]],
        columns=["단계", "가치", "차액", "해당 옵션"])
    st.dataframe(df.style.format({"가치": "{:,.2f}", "차액": "{:+,.2f}"}, na_rep="—"),
                 use_container_width=True, hide_index=True)
    _sc = ipo_scenarios(t)
    if _sc:
        st.markdown("### 상장 시점 가정")
        st.dataframe(pd.DataFrame(
            [[nm, v, v3, cvv, d] for nm, v, v3, cvv, d in _sc],
            columns=["가정", "전체 (B2)", "발행자 상환권 반영 (B3)", "전환권대가", "기준 대비"]
            ).style.format({"전체 (B2)": "{:,.4f}", "발행자 상환권 반영 (B3)": "{:,.4f}",
                            "전환권대가": "{:,.4f}", "기준 대비": "{:+,.4f}"}),
            use_container_width=True, hide_index=True)
        st.caption("예상 상장 시점은 **가정**입니다. 한 값만 싣지 말고 이 표를 조서에 함께 "
                   "넣으십시오 — 감사인이 반드시 묻는 질문의 답이 그 안에 있습니다. "
                   "상장 성공 여부는 그 노드의 주가가 최소공모가격을 넘는지로 판정하므로 "
                   "상장 확률을 따로 넣지 않습니다 (책 [사례 5-5]).")
    if issuer_redeem(t) and ca > 0:
        st.info(f"발행자 상환권을 **전환권 없는 부채 격자**에서 재면 **{full.get('ca_debt', 0.0):,.4f}** "
                f"입니다. 부채요소·전환권대가 배분은 이 값을 씁니다 — 기준서 1032 문단 31 은 "
                "자본요소가 아닌 파생(콜)을 **부채요소 안에** 넣으라고 합니다. 위 B3 의 차액 "
                f"{ca:,.4f} 은 전체 격자에서 전환 상승분을 자른 크기이고, 전환권을 **부채**로 "
                "보면 그쪽을 씁니다 (복합내재파생을 전체로 재므로).")
    if ca < -1e-9:
        st.warning(
            f"**{LB['call']} 값이 음수({ca:,.4f})입니다.** 콜을 넣었더니 전체가 오히려 "
            "커졌다는 뜻이라 계약으로는 설명되지 않습니다. TF 모형에서 콜이 전환을 "
            "앞당기면 그 노드가 통째로 지분이 되어 **무위험이자율로 할인**되기 때문에 "
            "생기는 현상이고, 격자가 성글수록 크게 나타납니다. **노드 간격을 1개월로 "
            "줄여** 보시고, 그래도 음수면 콜의 행사금액·행사기간이 계약과 맞는지 "
            "확인하십시오. 이 값은 배분표에 그대로 들어가므로, 고치지 않으면 "
            "파생상품자산이 음수로 실립니다."
            + ("　발행자 상환권을 부채 격자에서 잰 값(1032 문단 31)은 0 에서 "
               "끊으므로 자본 배분은 영향을 받지 않습니다." if issuer_redeem(t) else ""))
    st.caption("옵션은 서로 대체 관계라 각각 따로 평가해 더하면 총액이 부풀려집니다. "
               "하나씩 얹으며 차액을 보면 합계가 항상 맞습니다.")
    if bw_cash(t):
        # 현금납입형은 지분(신주인수권)과 부채(사채)가 애초에 갈라져 있다.
        # 지분이 될 확률로 할인율을 섞을 자리가 없어 GS 가 TF 와 같은 값이 된다.
        st.dataframe(pd.DataFrame([
            ["신주인수권 (지분요소)", full["E"], "무위험이자율"],
            ["사채 (부채요소)", full["B"], "위험이자율"],
            ["합계", full["TF"], "—"]],
            columns=["요소", "가치", "할인율"]).style.format({"가치": "{:,.2f}"}),
            use_container_width=True, hide_index=True)
        st.caption("현금납입형은 행사해도 사채가 남으므로 지분과 부채가 처음부터 "
                   "갈라져 있습니다. 지분이 될 확률로 할인율을 섞을 자리가 없어 "
                   "**TF 와 GS 가 같은 값**을 냅니다 — 신용위험 처리를 무엇으로 "
                   "고르셔도 결과가 바뀌지 않습니다.")
    else:
        st.dataframe(pd.DataFrame([
            ["TF · 값을 쪼갠다", full["TF"], full["E"], full["B"], None],
            ["GS · 할인율을 섞는다", full["GS"], full["GS"]*full["P"], full["GS"]*(1-full["P"]), full["P"]],
            ["차이", full["TF"]-full["GS"], None, None, None]],
            columns=["모형", "전체", "지분", "부채", inst_text(t, "전환확률")]).style.format(
            {"전체": "{:,.2f}", "지분": "{:,.2f}", "부채": "{:,.2f}",
             inst_text(t, "전환확률"): "{:.4f}"}, na_rep=""),
            use_container_width=True, hide_index=True)
        st.caption(inst_text(t, "전환확률이 0과 1 사이 중간이면 두 모형이 갈립니다. "
                             "한쪽으로 몰리면 사실상 같은 값이 나옵니다."
                             if 0.15 < full["P"] < 0.85 else
                             "전환확률이 한쪽으로 몰려 두 모형이 사실상 같은 값을 냅니다."))

if _detail_section == _detail_sections[1]:
    if holder_on(t):
        holder_ui(t, full, b0, b1, b2, ca)
    elif acc_mode(t) == "fv_only":
        st.warning(FV_ONLY_NOTE)
        _fvr = fv_only_rows(t, full, b0, b1, b2, ca)
        st.dataframe(pd.DataFrame([[k, v, v/100*t.face_total] for k, v in _fvr],
                                  columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
            {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        st.caption("결산 분개는 «전기말 장부금액 → 당기말 공정가치» 차이를 파생상품평가손익으로, "
                   "주계약은 발행일 유효이자율로 계산한 이자비용으로 만듭니다. 두 입력이 있어야 앱이 그 분개를 "
                   "만듭니다. 조서의 「회계처리」 시트도 같은 안내와 표를 포함합니다.")
    else:
        alloc_rows, alloc_note = allocate(t, full, b0, b1, b2, ca)
        af = allocate_full(t, alloc_rows + alloc_extra(t, ca))
        st.dataframe(pd.DataFrame(af, columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
            {"100 기준": "{:,.2f}", "전액 기준 (원)": "{:,.0f}"}),
            use_container_width=True, hide_index=True)
        st.caption(f"{'발행총액' if is_rcps(t) else '전자등록총액'} {t.face_total:,.0f}원 "
                   "기준으로 환산했습니다.")
        st.caption(alloc_note)
        if t.issue_cost > 0:
            _cs, _c100 = cost_split(t, alloc_rows)
            _F = t.face_total/100
            st.markdown("### 거래원가 배분")
            st.dataframe(pd.DataFrame(
                [[inst_text(t, k), v, c, c*_F, how] for k, v, c, how in _cs]
                + [["합계", sum(v for _, v, _, _ in _cs), _c100, _c100*_F, ""]],
                columns=["요소", "배분액 (100)", "거래원가 몫 (100)", "몫 (원)", "처리"]
                ).style.format({"배분액 (100)": "{:,.2f}", "거래원가 몫 (100)": "{:,.4f}",
                                "몫 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            if fvpl_on(t):
                st.caption("복합계약 전체를 당기손익-공정가치로 지정했으므로 거래원가를 얹을 "
                           "자리가 없어 **전액 즉시 비용**입니다 (제1109호 문단 5.1.1). "
                           "요소별 배분도, 유효이자율에 녹이는 몫도 없습니다.")
            else:
                st.caption("기업회계기준서 제1032호 문단 38 — 복합금융상품 발행과 관련된 거래원가는 "
                           "**배분된 발행금액에 비례하여** 부채요소와 자본요소로 배분합니다. "
                           "매도청구권 자산은 별도의 금융상품이라(문단 4.3.1) 분모에서 뺐습니다. "
                           + (f"주계약은 거래원가를 뺀 **{_ahc:,.4f}** 에서 상각을 시작하므로 "
                              "유효이자율이 그만큼 높아집니다."
                              if (_ahc := acc_host(t, full, b0, b1, b2, ca)) is not None
                              else "잔여 주계약이 0 이하라 상각표가 없습니다 (아래 상각표 탭)."))
        # 분개는 배분표를 그대로 뒤집는다 — 조서와 같은 규칙. 음수 줄(자산)만
        # 차변으로 가고 나머지는 대변이다. 따로 쓰면 두 표가 어긋난다.
        _je = [("현금", 100.0, None)]
        for _k, _v in alloc_rows[:-1]:
            _nm = inst_text(t, _k.split(" · ")[0])
            if _v < 0: _je.append((f"파생상품자산 ({_nm})", -_v, None))
            else:      _je.append((f"    {_nm}", None, _v))
        _w = max(len(k) for k, _, _ in _je) + 2
        _ln = [f"차) {k:<{_w}} {dr:>12,.4f}" if dr is not None else
               f"    대) {k.strip():<{_w-4}} {cr:>12,.4f}" for k, dr, cr in _je]
        _sd = sum(dr for _, dr, _ in _je if dr); _sc = sum(cr for _, _, cr in _je if cr)
        _kknote = [NOTE_KKIND.format(v=f"{_v:,.4f}") for _, _v in alloc_extra(t, ca)]
        # 전체 지정이면 상각후원가로 남는 주계약이 없어 유효이자율 이자비용이 없다.
        # 전체를 공정가치로 다시 재고 그 변동을 손익으로 보낸다.
        if fvpl_on(t):
            _post = ("차) 금융부채평가손익            복합계약 전체를 공정가치로 재측정\n"
                     "    대) 당기손익-공정가치 측정 금융부채\n"
                     "※ 상각후원가로 남는 주계약이 없어 유효이자율 이자비용이 없습니다.\n"
                     "※ 자기신용위험 변동분은 기타포괄손익으로 표시합니다 "
                     "(제1109호 문단 5.7.7).\n"
                     "   이 앱은 그 분해를 하지 않으므로 직접 나누어야 합니다.")
        else:
            _post = (
                inst_text(t, "차) 이자비용                   주계약 × 유효이자율\n"
                             "    대) 전환사채 (주계약)\n")
                + ("차) 파생상품평가손익            매 결산 공정가치로 재측정\n"
                   "    대) 파생상품부채\n"
                   "※ 전환권이 부채이므로 주가가 오르면 평가손실이 납니다."
                   if t.conv_class == "liability" else
                   ("차) 파생상품평가손익            분리한 파생상품부채를 공정가치로 재측정\n"
                    "    대) 파생상품부채\n"
                    if any("파생상품부채" in k for k, _ in alloc_rows[:-1]) else "")
                   + "※ 전환권대가는 자본이므로 후속 재측정이 없습니다."))
        je = ("[최초 인식]\n" + "\n".join(_ln)
              + f"\n{'합계':<{_w+4}} 차변 {_sd:,.4f} = 대변 {_sc:,.4f}\n"
              + ("".join("※ " + x + "\n" for x in _kknote))
              + "\n[후속 결산]\n" + _post)
        st.code(je, language=None)

        # ── 기말 재평가 ──
        _rm = remeasure(t, alloc_rows)
        # 전체 지정이면 재평가 대상이 파생상품부채가 아니라 복합계약 전체다.
        _FVL = "복합계약 전체 (당기손익-공정가치)" if fvpl_on(t) else "파생상품부채"
        st.markdown("### 기말 재평가")
        if not _rm["has"]:
            st.caption("결산 평가라면 입력화면 **기말 재평가 · 전기 장부금액** 에 전기말 장부금액을 "
                       "넣으십시오. 당기 공정가치와의 차이가 평가손익으로, 분개와 함께 나옵니다. "
                       f"지금 {_FVL} 공정가치는 **{_rm['fv_liab']:,.4f}** 입니다.")
        else:
            _F = t.face_total/100
            _pl = _rm["pl"]
            st.dataframe(pd.DataFrame([
                [f"{_FVL} · 전기말 장부금액", _rm["prev"], _rm["prev"]*_F],
                [f"{_FVL} · 당기말 공정가치", _rm["fv_liab"], _rm["fv_liab"]*_F],
                [("평가손실 (부채 증가)" if _pl >= 0 else "평가이익 (부채 감소)"), abs(_pl), abs(_pl)*_F]]
                + ([["주계약 · 전기말 장부금액 (참고)", _rm["prev_host"], _rm["prev_host"]*_F]]
                   if _rm["prev_host"] is not None else []),
                columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
                {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            if _pl >= 0:
                st.code(f"차) 파생상품평가손실            {_pl:>12,.4f}\n"
                        f"    대) 파생상품부채                {_pl:>12,.4f}", language=None)
            else:
                st.code(f"차) 파생상품부채                {-_pl:>12,.4f}\n"
                        f"    대) 파생상품평가이익            {-_pl:>12,.4f}", language=None)
            st.caption("공정가치는 이 화면의 배분표에서 파생상품부채 줄을 모은 값입니다 — 전환권이 "
                       "부채면 복합내재파생상품, 자본이면 분리한 상환·매도청구권 파생상품부채입니다. "
                       "**주계약은 여기서 재평가하지 않습니다.** 상각후원가는 발행일 유효이자율로 "
                       "상각한 장부금액이어야 하는데 이 앱의 상각표는 평가기준일 배분액에서 출발하므로 "
                       "최초 인식에만 맞습니다. 발행 시점 조서의 상각표 그 회차 기말 금액을 쓰십시오.")
            if (st.session_state.get("px_src") or "").startswith("발행가 역산"):
                st.error("주가가 **발행가 역산**값입니다. 기말 재평가에서는 발행가가 기준이 아니므로 "
                         "평가기준일 주가를 직접 넣으십시오.")

        # ── 당기 이자비용 — 발행일 유효이자율로 굴린다 ──
        _ar, _end = amort_year(t)
        if _ar:
            _F = t.face_total/100
            st.markdown("### 당기 이자비용 — 발행일 유효이자율")
            st.dataframe(pd.DataFrame(
                [[i, bv, it, c, end] for i, bv, it, c, end in _ar],
                columns=["회차", "기초", "유효이자", "지급이자", "기말"]).style.format(
                {"기초": "{:,.4f}", "유효이자": "{:,.4f}", "지급이자": "{:,.4f}", "기말": "{:,.4f}"}),
                use_container_width=True, hide_index=True)
            _ti = sum(x[2] for x in _ar); _tc = sum(x[3] for x in _ar)
            st.code(inst_text(t,
                f"차) 이자비용                    {_ti:>12,.4f}\n"
                + (f"    대) 현금 (표면이자)             {_tc:>12,.4f}\n" if _tc > 0 else "")
                + f"    대) 전환사채 (주계약)            {_ti-_tc:>12,.4f}"), language=None)
            st.caption(f"전기말 {t.prev_host:,.4f} → 당기말 **{_end:,.4f}** "
                       f"(전액 {_end*_F:,.0f}원). 유효이자율 {t.eir_issue:.4%} · {len(_ar)}회차. "
                       "이 앱의 상각표(다음 탭)는 평가기준일 배분액에서 출발하므로 결산에는 "
                       "이 표를 쓰십시오.")

        # ── 전환·상환 시 분개 ──
        _fvl = remeasure(t, alloc_rows)["fv_liab"]
        _host_bv = _end if _ar else (t.prev_host if t.prev_host >= 0 else alloc_rows[0][1])
        with st.expander(inst_text(t, "전환·상환 시 분개")):
            if bw_cash(t):
                # 현금납입형은 행사해도 사채가 남는다. 사채를 제거하는 갈래가 없고,
                # 들어온 현금과 자본요소만 자본으로 넘어간다.
                st.markdown("**신주인수권이 행사될 때** — 기업회계기준서 제1032호 문단 AG32")
                st.caption("행사대금을 현금으로 받으므로 **사채는 그대로 남는다.** 자본으로 "
                           "넘어가는 것은 받은 현금과 신주인수권대가(또는 그때까지 재평가한 "
                           "파생상품부채)뿐이고, 행사에 따라 인식할 손익은 없다. 사채는 "
                           "만기까지 상각후원가로 굴러간다.")
                st.code(
                    f"차) 현금 (행사대금)               {100.0:>12,.4f}\n"
                    + (f"차) 파생상품부채 (신주인수권)      {_fvl:>12,.4f}\n" if _fvl > 1e-9
                       else f"차) 신주인수권대가 (자본)         {conv:>12,.4f}\n"
                            if t.conv_class == "equity" else "")
                    + f"    대) 자본금 + 주식발행초과금       "
                      f"{100.0 + (max(0.0, _fvl) if _fvl > 1e-9 else (conv if t.conv_class == 'equity' else 0)):>12,.4f}\n"
                    + "※ 신주인수권부사채(주계약)는 분개에 나오지 않는다 — 행사해도 소멸하지 "
                      "않는다.", language=None)
            else:
                st.markdown("**전환될 때** — 기업회계기준서 제1032호 문단 AG32")
                st.caption("발행자는 부채를 제거하고 자본으로 인식한다. 최초 인식시점의 자본요소는 "
                           "자본의 다른 항목으로 대체될 수 있지만 계속 자본으로 유지된다. "
                           "**전환에 따라 인식할 손익은 없다.**")
                st.code(inst_text(t,
                    f"차) 전환사채 (주계약)             {_host_bv:>12,.4f}\n"
                    + (f"차) 파생상품부채                 {_fvl:>12,.4f}\n" if _fvl > 1e-9 else "")
                    + (f"차) 전환권대가 (자본)            {conv:>12,.4f}\n"
                       if t.conv_class == "equity" else "")
                    + f"    대) 자본금 + 주식발행초과금       "
                      f"{_host_bv + max(0.0, _fvl) + (conv if t.conv_class == 'equity' else 0):>12,.4f}"),
                    language=None)
            st.markdown("**상환·재매입될 때** — 문단 AG33 · AG34")
            st.caption("지급한 대가를 발행 시점과 **일관된 방법**으로 부채요소와 자본요소에 "
                       "배분한다. 부채요소에 관련된 손익은 당기손익, 자본요소와 관련된 대가는 "
                       "자본으로 인식한다. 발행 시점의 방법이 「부채요소를 먼저 공정가치로 정하고 "
                       "나머지를 자본에」이므로, 대가 중 부채 몫은 **상환일 부채요소 공정가치**이고 "
                       "나머지가 자본 몫이다.")
            _ss = settle_split(t, b1, _host_bv)
            if _ss is None:
                st.info("입력화면 **기말 재평가 · 전기 장부금액 → 상환·재매입 지급대가** 에 "
                        "지급액을 넣으면 배분표와 분개가 나옵니다.")
            else:
                st.dataframe(pd.DataFrame([
                    ["지급대가", _ss["pay"], _ss["pay"]*t.face_total/100],
                    ["부채 몫 — 상환일 부채요소 공정가치", _ss["liab_fv"], _ss["liab_fv"]*t.face_total/100],
                    ["자본 몫 — 잔여", _ss["eq"], _ss["eq"]*t.face_total/100],
                    ["부채 장부금액", _ss["liab_bv"], _ss["liab_bv"]*t.face_total/100],
                    [("상환이익 (장부 > 부채 몫)" if _ss["pl"] >= 0 else "상환손실"),
                     abs(_ss["pl"]), abs(_ss["pl"])*t.face_total/100]],
                    columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
                    {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
                    use_container_width=True, hide_index=True)
                _pl = _ss["pl"]
                st.code(inst_text(t,
                    f"차) 전환사채 (주계약)             {_ss['liab_bv']:>12,.4f}\n"
                    f"차) 자본 (전환권대가 등)          {_ss['eq']:>12,.4f}\n"
                    + (f"차) 상환손실                    {-_pl:>12,.4f}\n" if _pl < 0 else "")
                    + f"    대) 현금                       {_ss['pay']:>12,.4f}\n"
                    + (f"    대) 상환이익                   {_pl:>12,.4f}" if _pl > 0 else "")),
                    language=None)
                st.caption("부채 몫은 상환일에 다시 잰 부채요소 공정가치입니다 — 지금 화면의 "
                           f"부채요소 **{b1:,.4f}** 를 씁니다. 평가기준일을 상환일로 맞추고 "
                           "그날 곡선을 넣으셔야 맞습니다.")

if _detail_section == _detail_sections[2] and _shared_run is not None:
    from numeric_analysis_ui import main as numerical_rights
    numerical_rights(_shared_run)

if _detail_section == _detail_sections[2] and _shared_run is None:
    if holder_on(t):
        st.info("**투자자 관점입니다.** 이 탭의 판단은 **발행자**의 분리 판단입니다 — 투자자는 "
                "주계약이 금융자산이라 내재파생상품을 분리하지 않습니다(제1109호 문단 4.3.2). "
                "발행자와의 대조용으로 남겨 둡니다.")
    if is_bw(t):
        st.write("**신주인수권**이 별도의 금융상품인지 복합금융상품의 자본요소인지, "
                 "그리고 조기상환청구권·매도청구권을 주계약과 분리해야 하는지를 "
                 "계약 조항에 근거해 판단합니다. 아래 문안을 그대로 조서에 "
                 "옮기실 수 있습니다.")
    else:
        st.write(inst_text(t, f"{LB['put']}과 {LB['call']}을 **주계약과 분리해야 "
                              "하는지**를 계약 조항에 근거해 판단하고, 분리한다면 어떤 "
                              "방법으로 재는지까지 정리합니다. 아래 문안을 그대로 조서에 "
                              "옮기실 수 있습니다."))
    st.caption("판단 순서가 정해져 있습니다 — 문단 B4.3.5 말미가 "
               "\"제1032호에 따라 전환채무상품의 자본요소를 분리하기 전에 "
               "내재된 콜옵션이나 풋옵션이 주채무계약과 밀접하게 관련되어 "
               "있는지를 판단한다\" 고 못박습니다.")

    # 격자 로직의 설계도. 모델을 짜기 전에 이 표부터 채워야 노드 의사결정이
    # 계약을 옮긴 것이 된다.
    st.markdown("**계약상 권리** — 이 표가 격자 의사결정의 설계도입니다")
    st.dataframe(pd.DataFrame(rights_table(t), columns=RIGHT_COLS),
                 use_container_width=True, hide_index=True)
    _ov = [x for x in pc_overlap(t) if x[2] > x[3] + 1e-9] if t.k_w > 0 else []
    if _ov:
        st.caption(f"조기상환청구권과 매도청구권이 **{len(_ov)}개 노드에서 함께 열리고** "
                   f"그 자리의 조기상환금액이 더 큽니다 (첫 자리 "
                   f"{_ov[0][1]:,.0f}개월 · {_ov[0][2]:,.4f} 대 {_ov[0][3]:,.4f}). "
                   "**동일 시점의 권리행사 우선순위에 따라 평가값이 달라집니다.** 아래 비교표를 확인하십시오.")
    elif t.k_w > 0 and pc_overlap(t):
        st.caption("두 권리가 함께 열리는 노드가 있으나 매도청구금액이 늘 크거나 같아 "
                   "**어느 우선순위를 고르셔도 같은 답**이 나옵니다.")
    st.divider()
    if _shared_run is None:
        st.markdown("**계약 조항 확인** — 입력화면에 없는 사실만 여기서 받습니다")
        f1, f2 = st.columns(2)
        # RCPS 는 입력화면의 「콜옵션」 갈래가 이 둘을 정한다 (derive 가 덮어쓴다).
        # 눌러도 소용없는 칸을 살려 두면 판단이 어긋난 것처럼 보이므로 잠근다.
        _lk = is_rcps(t)
        t.k_third = 1 if f1.checkbox(
            inst_text(t, "매도청구권을 제3자에게 지정할 수 있다"), value=bool(t.k_third),
            disabled=_lk,
            help=("입력화면 「콜옵션」에서 **제3자 지정 매도청구권**을 고르시면 켜집니다."
                  if _lk else
                  "공시에 \"발행회사 및 발행회사가 지정하는 자\" 로 적혀 있으면 "
                  "해당합니다. 거래상대방이 달라질 수 있어 내재파생상품이 아니라 "
                  "별도의 금융상품입니다 (문단 4.3.1 마지막 문장).")) else 0
        t.k_transfer = 1 if f2.checkbox(
            inst_text(t, "매도청구권을 사채와 독립적으로 양도할 수 있다"),
            value=bool(t.k_transfer), disabled=_lk,
            help=("RCPS 는 입력화면 「콜옵션」 갈래가 정합니다." if _lk else
                  "같은 문단의 다른 갈래입니다. 둘 중 하나만 해당해도 별도의 "
                  "금융상품입니다.")) else 0
        f3, f4 = st.columns(2)
        t.p_lost_int = 1 if f3.checkbox(
            inst_text(t, "조기상환 행사금액이 상실이자 보상 수준이다"), value=bool(t.p_lost_int),
            help="잔여기간에 못 받게 된 이자의 현재가치를 보상하는 수준이면 주계약과 "
                 "밀접하게 관련되어 있어 분리하지 않습니다 (문단 B4.3.5(5)(나)). "
                 "국내 사모 CB 는 대개 해당하지 않습니다.") else 0
        t.fvpl_whole = 1 if f4.checkbox(
            "복합계약 전체를 당기손익-공정가치로 지정했다", value=bool(t.fvpl_whole),
            help="전체를 공정가치로 재면 내재파생을 따로 뗄 이유가 없습니다 "
                 "(문단 4.3.3(3)). 고르면 **배분표가 한 줄**이 되고 유효이자율 "
                 "**상각표를 만들지 않습니다** — 상각후원가로 남는 주계약이 없기 "
                 "때문입니다. 거래원가는 전액 즉시 비용입니다. 전환권이 자본이면 "
                 "지정할 수 없어(문단 4.2.2) 형태가 바뀌지 않고 경고만 뜹니다. "
                 "실무에서 드뭅니다.") else 0

    else:
        st.caption('계약 조항과 회계분류 가정은 「입력·시장자료 → 분해방법·기간 기준」에서 수정하십시오.')

    # 상각표는 아래 탭에서 만들어지므로 여기서 따로 부른다. 판단이 쓰는 것은
    # 실제로 인식한 배분액에서 상각한 장부금액이다.
    # 전체 지정이면 인식한 주계약이 없어 상각표를 만들 수 없다. split_test 는
    # 판정에 쓸 상각표를 어차피 **B0 기준으로 다시 만들므로**(순환을 끊은 자리)
    # 빈 목록을 넘겨도 판정이 흔들리지 않는다.
    _ah = acc_host(t, full, b0, b1, b2, ca)
    _sp = split_test(t, full, b0, b1, b2, ca,
                     [] if _ah is None else eir_table(t, _ah)[1])
    st.divider()

    _items = ([("warrant", "신주인수권")] if is_bw(t) else []) + \
             [("put", LB["put"]), ("call", LB["call"])]
    for _key, _nm in _items:
        _d = _sp.get(_key)
        if _d is None: continue
        st.markdown(f"### {_nm}")
        if not _d["있음"]:
            st.info(inst_text(t, _d["이유"][0])); continue
        _box = (st.success if _d["결론"] in ("분리", "별도의 금융상품", "묶어서 분리")
                else st.warning)
        _box(inst_text(t, f"**{_d['결론']}**　—　" + " ".join(_d["이유"])))
        if _d["근거"]:
            st.caption("근거 · " + " · ".join(_d["근거"]))
        if _d["지표"]:
            st.dataframe(pd.DataFrame(
                [[k2, (f"{v2*100:.1f}%" if k2 == "차이" else
                       "예" if v2 is True else "아니오" if v2 is False
                       else v2 if isinstance(v2, str) else f"{v2:,.4f}")]
                 for k2, v2 in _d["지표"].items()],
                columns=["항목", "값"]), use_container_width=True, hide_index=True)
        if _key == "put" and _d["결론"] == "묶어서 분리" and "차이" in _d["지표"]:
            _gap = _d["지표"]["차이"]
            st.caption(inst_text(t,
                "전환권이 파생상품부채라 10% 검토(B4.3.5(5)(가))에 **들어가기 전에** 묶음으로 "
                f"결정됐습니다. 위 차이 {_gap*100:.1f}% 는 지표로만 보여 줍니다 — 전환권이 자본이었다면 "
                + ("이 차이만으로도 «분리» 입니다." if abs(_gap) > SPLIT_TOL
                   else "«분리하지 않을 여지» 입니다.")))
        st.markdown("**평가방법** — " + inst_text(t, _d["평가"]))

    if not _sp["put"]["설정일치"]:
        st.error(inst_text(t,
                 "입력화면의 **조기상환청구권 → 회계 처리** 설정이 위 판정과 "
                 f"어긋납니다. 판정은 **{_sp['put']['결론']}** 인데 설정은 "
                 + ("분리 · 파생상품부채" if int(t.p_sep) else "분리하지 않음")
                 + " 입니다. 배분표와 분개가 판정과 다르게 나오므로 입력화면에서 "
                   "맞추십시오."))
    elif (_sp["put"].get("스위치") and _sp["put"]["결론"] == "분리하지 않을 여지"):
        st.info(inst_text(t,
                "조기상환권은 **어느 쪽도 설명할 수 있는** 자리입니다. 지금 설정은 "
                + ("**분리 · 파생상품부채**" if int(t.p_sep)
                   else "**분리하지 않음 · 부채요소에 포함**")
                + " 입니다. 입력화면 **조기상환청구권 → 회계 처리** 에서 바꿀 수 "
                  "있고, 어느 쪽을 골랐는지와 그 이유를 조서에 적으십시오. "
                  "전환권대가는 어느 쪽이든 같고, 갈리는 것은 부채 표시와 "
                  "후속측정입니다 — 분리하면 파생상품부채를 매기 공정가치로 "
                  "재평가하고, 분리하지 않으면 부채요소를 상각후원가로 굴립니다."))
    if not _sp["call"]["설정일치"]:
        st.error(inst_text(t,
                 "입력화면의 **매도청구권 → 회계 처리** 설정이 위 판정과 "
                 f"어긋납니다. 판정은 **{_sp['call']['결론']}** 인데 설정은 "
                 + ("별도 금융상품" if t.k_sep else "복합내재파생에 포함")
                 + " 입니다. 배분표와 분개가 판정과 다르게 나오므로 입력화면에서 "
                   "맞추십시오."))

    st.divider()
    st.markdown("## 평가방법 — 어떻게 잴 것인가")

    # ── 조기상환권 : 확정 계산으로 충분한가, 금리모형이 필요한가 ──
    # 켤 수 없는 자리에서 「켜십시오」라고 권하지 않는다. 전환권이 파생상품부채면
    # 조기상환권을 따로 재지 않으므로 이 절 자체가 해당 없음이다.
    _bblk = put_bdt_block(t)
    _bdt_na = (_bblk == "전환권이 파생상품부채다")
    if _bdt_na:
        st.markdown(inst_text(t, "### 조기상환청구권 — 금리모형은 해당 없음"))
        st.info(inst_text(t,
                "전환권이 파생상품부채라 전환권·조기상환청구권·매도청구권이 **하나의 "
                "복합내재파생상품**입니다 (기준서 1109 문단 B4.3.4). 조기상환권을 따로 "
                "재는 방식은 이 앱에서 지원하지 않습니다. 이는 앱의 지원 범위이며 금리위험이 중요하지 않다는 판단은 아닙니다."))
    else:
        st.markdown(inst_text(t, "### 조기상환청구권 — 금리모형(BDT)을 켤 것인가"))
        st.caption(inst_text(t, "전환을 끄면 격자가 주가와 무관해져 스텝마다 값이 하나뿐입니다. "
                   "확정 금리를 쓰는 이 분석에는 금리 변동에 따른 행사시점의 "
                   "시간가치가 반영되지 않습니다. 아래 행사금액과 계속보유가치의 비교는 "
                   "참고자료이며, 금리모형 채택 여부는 금리 변동성 자료와 평가 영향까지 "
                   "검토해 결정하십시오."))
    if (not _bdt_na) and t.p_s <= t.p_e and t.T > 0:
        _r0 = engine(t, conv=False, put=False, call=False)
        _dtx = t.T/int(t.n)
        _lo2, _hi2 = step_mapper(t, int(t.n), _dtx)
        _mp = int(t.n)/(t.T*12)
        _pr = max(1, int(round(t.p_f*_mp)))
        _s2, _e2 = _lo2(t.p_s), _hi2(t.p_e)
        # 금액과 열림 판정을 엔진과 같은 곳에서 가져온다 — 행사금액표를 넣으면
        # 표가 산식을 이기고 행사 가능 시점도 표가 정한다.
        _EA2 = exercise_amounts(t, int(t.n), _dtx)
        _open2 = (_EA2["p_on"] if _EA2["p_on"] else
                  (lambda i: max(_s2, 0) <= i <= _e2 and (i - _s2) % _pr == 0))
        _at2 = {}
        for _k3, _v3 in _r0["memo"].items():
            _at2.setdefault(_k3[0], _v3)
        _rows2, _rat2 = [], []
        for _i3 in range(max(_s2, 0), _e2+1):
            if not _open2(_i3) or _i3 not in _at2: continue
            _hold = _at2[_i3]["E"] + _at2[_i3]["B"]
            _amt = _EA2["put"](_i3)
            _rat2.append(_amt/max(_hold, 1e-9))
            _rows2.append([_i3, round(_EA2["cmonth"](_i3)), _amt, _hold,
                           _rat2[-1]])
        if _rows2:
            st.dataframe(pd.DataFrame(
                _rows2, columns=["스텝", "발행 후 개월", "행사금액", "계속보유가치",
                                 "행사금액 ÷ 계속보유"]).style.format(
                {"행사금액": "{:,.2f}", "계속보유가치": "{:,.2f}",
                 "행사금액 ÷ 계속보유": "{:.3f}"}),
                use_container_width=True, hide_index=True, height=240)
            st.caption("마지막 열이 1보다 크면 현재 입력한 확정금리 조건에서 그 날 "
                       "상환청구가 계속보유보다 유리하다는 뜻입니다. 이 비율만으로 "
                       "금리 변동에 따른 시간가치나 BDT 적용 여부를 확정할 수 없습니다.")
            st.info(f"행사금액 ÷ 계속보유가치 범위: {min(_rat2):.3f} ~ {max(_rat2):.3f}. "
                    + ("BDT를 적용할 수 있는 입력 조합입니다. 금리 변동성의 출처와 "
                       "TF·BDT 부채요소 차이를 검토하십시오." if not _bblk else
                       f"이 앱의 단독 BDT 계산은 지원되지 않습니다: {_bblk}. "
                       "금리위험의 중요성은 별도로 검토하십시오."))
        st.caption(inst_text(t, "현재 설정 — 조기상환권을 "
                   + ("**BDT 금리격자**로 평가합니다." if put_bdt_on(t) else
                      "**금리 고정 격자**로 평가합니다.")
                   + ("" if put_bdt_on(t) else
                      (f"  BDT 는 켤 수 없습니다 — {_bblk}." if _bblk else
                       "  BDT 는 입력화면에서 켤 수 있습니다."))))
    elif not _bdt_na:
        st.info(inst_text(t, "조기상환청구권이 없어 판단할 것이 없습니다."))

    # ── 매도청구권 : 세 방법을 나란히 ──
    st.markdown(inst_text(t, "### 매도청구권 — 어느 방법으로 잴 것인가"))
    if t.k_w > 0:
        st.caption(inst_text(t, call_type_note(t)))
        _cmp, _rec = call_compare(t, full, b2)
        _base = next((v for nm, _, v, _ in _cmp if nm.startswith("유무가치비교법 (")), None)
        _mv = [[nm, sp, v, (v - _base) if _base else 0.0,
                ((v - _base)/_base if _base else 0.0), "◀ 적용" if on else ""]
               for nm, sp, v, on in _cmp]
        st.dataframe(pd.DataFrame(
            _mv, columns=["방법", "지분·채권 구분 기준", "값", "유무가치 대비 차이", "차이율", "　"]
            ).style.format({"값": "{:,.4f}", "유무가치 대비 차이": "{:,.4f}", "차이율": "{:,.1%}"}),
            use_container_width=True, hide_index=True)
        st.caption(inst_text(t,
            "**두 방법의 결과를 억지로 같게 맞추지 않습니다.** 한공회 4.1.1 은 "
            "「유무가치비교법과 옵션차익혼합할인법은 개념적으로 그 결과가 동일하여야 하나 "
            "세부적인 구현방법에서 시장에서의 실무가 다양하게 진행되고 있어 그 차이가 종종 "
            "발생한다」고 씁니다. 차이는 방법론 · 의무보유 반영 · 조기행사 판단 · 전환확률 "
            "산출 · 할인방법에서 옵니다 — 아래에 두 조각으로 나눠 두었습니다."))
        if _rec:
            st.markdown(inst_text(t, "##### 유무가치비교법과의 차이 — 어디에서 오는가"))
            _d = _rec["유무가치비교법 (적용 계약)"] - _rec["옵션차익법 (적용 산식·적용 설정)"]
            st.dataframe(pd.DataFrame(
                [[k, v] for k, v in _rec.items()] + [["차이 (유무가치 − 옵션차익)", _d]],
                columns=["항목", "값"]).style.format({"값": "{:,.4f}"}),
                use_container_width=True, hide_index=True)
            st.caption(inst_text(t,
                "①과 ②의 합이 차이와 정확히 같습니다. ①은 두 방법이 같은 계약(의무보유 없음)을 "
                "잴 때 남는 순수한 구현 차이이고, ②는 유무가치비교법이 추가로 담는 부분입니다 — "
                "콜을 넣고 뺀 차액이라 투자자가 전환·조기상환을 못 하게 된 효과까지 값에 "
                "들어갑니다. 옵션차익법은 그 제한 자체를 별도의 가치요소로 콜에 더하지 않고, "
                "제한으로 **콜 대상물량이 행사기간 동안 존속하여 행사 가능성이 유지되는 효과만** "
                "담습니다 (참고 줄)."))
        with st.expander(inst_text(t, "옵션차익혼합할인법은 어떻게 계산하나 — 여섯 단계")):
            st.markdown(inst_text(t, CALL_HOWTO))
        st.info(inst_text(t, "판정에 따른 권고 — " + _sp["call"]["평가"].replace("**", "")))

        # 계약 우선순위가 값을 얼마나 바꾸는가. 겹치는 노드가 없거나 매도청구금액이
        # 늘 크면 두 갈래가 같은 답을 내므로 표가 한 줄로 겹친다 — 그것도 정보다.
        st.markdown(inst_text(t, "#### 조기상환청구권과 겹칠 때 — 누가 먼저인가"))
        _pcc = pc_compare(t)
        _pv2 = ([[_lb, _ca2, _cv2, "◀ 적용" if _on else ""] for _lb, _ca2, _cv2, _on in _pcc]
                if _pcc else [["투자자 조기상환 우선", ca, conv, "◀ 적용" if int(t.pc_order) == 0 else ""],
                              ["발행자 매도청구 우선", ca, conv, "◀ 적용" if int(t.pc_order) == 1 else ""]])
        st.dataframe(pd.DataFrame(
            _pv2, columns=["우선순위", inst_text(t, "매도청구권"),
                           inst_text(t, "전환권대가"), "　"]).style.format(
            {inst_text(t, "매도청구권"): "{:,.4f}",
             inst_text(t, "전환권대가"): "{:,.4f}"}),
            use_container_width=True, hide_index=True)
        _d2 = abs(_pv2[0][1] - _pv2[1][1])
        if _d2 > 1e-6:
            st.warning(inst_text(t,
                f"**우선순위에 따라 매도청구권이 {_d2:,.4f} 만큼 갈립니다.** "
                "수식이 정하는 것이 아니라 **계약이 정하는 것**입니다 — 계약서의 "
                "통지기간과 「이미 통지된 조기상환청구를 매도청구로 번복할 수 있는가」 "
                "조항을 확인하시고, 고른 근거를 조서에 남기십시오. 입력화면 "
                "매도청구권 칸에서 바꿉니다."))
        else:
            st.caption(inst_text(t,
                "두 우선순위가 같은 답을 냅니다 — 행사기간이 겹치지 않거나, 겹치는 "
                "자리에서 매도청구금액이 조기상환금액보다 크거나 같기 때문입니다. "
                "그래도 계약서의 우선순위 조항은 조서에 적어 두십시오."))
    else:
        st.info(inst_text(t, "매도청구권이 없어 판단할 것이 없습니다."))

    # ── 이자율모형 검토 — 네 관문 ──
    if not is_sha(t):
      with st.expander("이자율모형(BDT) 적용 검토 — 관측값과 판단 근거", expanded=False):
        st.caption("책 87쪽의 정성적 고려사항입니다. 수치 경계로 적용 여부를 확정하지 않습니다. 아래 민감도는 추가 계산합니다.")
        _sig = rate_signals(t)
        _bd = bdt_review(t, full, b0, b1, b2, ca, _sig)
        st.dataframe(pd.DataFrame(
            [[f"{n_}", q_, v_, ("판단 필요" if ok_ is None else "예" if ok_ else "아니오"), w_] for n_, q_, v_, ok_, w_ in _bd["관문"]],
            columns=["번호", "검토사항", "값", "상태", "설명"]), use_container_width=True, hide_index=True)
        _box = (st.success if _bd["결론"].startswith("검토했으나") or _bd["결론"] == "해당 없음"
                or _bd["결론"] == "이자율모형(BDT) 적용" else st.warning)
        _box(f"**{_bd['결론']}** — {_bd['사유']}")
        st.dataframe(pd.DataFrame([
            ["금리 수준 ±1%p (두 곡선 평행)", f"{_sig['dl']:+,.4f}", f"{abs(_sig['dl'])/max(b2,1e-9)*100:.2f}%"],
            ["신용스프레드 ±1%p (위험 곡선만)", f"{_sig['ds']:+,.4f}", f"{abs(_sig['ds'])/max(b2,1e-9)*100:.2f}%"],
            ["변동성 ±10%p", f"{_sig['dv']:+,.4f}", f"{abs(_sig['dv'])/max(b2,1e-9)*100:.2f}%"],
            ["금리 수준 ÷ 주가 민감도", f"{_sig['ratio']:.3f}", ""],
            [f"{t.T:.2f}년 신용스프레드", f"{_sig['spr']*100:.2f}%p", f"할인율 중 {_sig['share']*100:.0f}%"],
            ["정산 분포 — 전환 · 조기상환", f"{_bd['지표']['conv_share']*100:.1f}% · {_bd['지표']['put_share']*100:.1f}%", ""],
            ["구성요소 — 조기상환권 · 전환권", f"{_bd['지표']['pv']:,.4f} · {_bd['지표']['cv']:,.4f}", ""]],
            columns=["항목", "값", "비중"]), use_container_width=True, hide_index=True)
        if _bd["왜곡"]:
            st.warning(f"BDT 를 적용했습니다. BDT 부채요소 {_bd['왜곡']['bdt']:,.4f} − TF 부채요소 "
                       f"{_bd['왜곡']['tf']:,.4f} = **{_bd['왜곡']['diff']:+,.4f}** 가 전환권에서 빠져나갑니다 "
                       "(책 89쪽). 이 차이의 원인과 영향을 검토기록에 남기십시오.")
        st.markdown("**조서 문안** — 「검산요약」 시트에 같은 문장이 실립니다")
        st.code(_bd["문안"], language=None)
        st.caption("두 곡선을 함께 흔드는 이유 — 부채 부분은 위험이자율로 할인되므로 무위험 곡선만 흔들면 "
                   "스프레드 변화와 상쇄되어 노출이 최대 수십 배 과소하게 잡힙니다. "
                   "«검토하지 않았다» 와 «검토했으나 적용하지 않았다» 는 다릅니다 — 흔적이 없으면 그 자체가 "
                   "지적사항입니다.")

    # ── 회계처리 ──
    st.divider()
    st.markdown("## 회계처리 — 판정대로 배분하면")
    _rows_al, _note_al = allocate(t, full, b0, b1, b2, ca)
    st.dataframe(pd.DataFrame(
        [[k2, v2, fv2] for k2, v2, fv2 in allocate_full(t, _rows_al)],
        columns=["항목", "100 기준", "전액 기준 (원)"]).style.format(
        {"100 기준": "{:,.4f}", "전액 기준 (원)": "{:,.0f}"}),
        use_container_width=True, hide_index=True)
    st.caption(_note_al)
    st.caption("분개는 **회계처리** 탭에 있습니다. 여기서는 판정이 배분에 어떻게 "
               "닿는지만 보입니다.")

    st.divider()
    st.markdown("**조서에 옮길 문안**")
    st.code(inst_text(t, split_memo(_sp)), language=None)
    st.caption("판단 순서·근거 문단·지표가 함께 들어 있습니다. 결론만 적는 것과 "
               "달리 감사인이 다시 물을 여지를 줄입니다.")

if _detail_section == _detail_sections[3]:
    RF, CR = curves(t)
    dt_ = t.T/t.n
    rows = []
    for k in range(9):
        tt = t.T*k/8
        i = min(t.n-1, round(tt/dt_))
        fr = forward_rate(RF, i*dt_, (i+1)*dt_)
        fc = forward_rate(CR, i*dt_, (i+1)*dt_)
        rows.append([tt, RF(tt), fr, CR(tt), fc, fc-fr])
    cdf = pd.DataFrame(rows, columns=["시점(년)", "무위험 현물", "무위험 선도",
                                      "위험 현물", "위험 선도", "스프레드"])
    st.dataframe(cdf.style.format({"시점(년)": "{:.2f}", "무위험 현물": "{:.2%}",
                                   "무위험 선도": "{:.2%}", "위험 현물": "{:.2%}",
                                   "위험 선도": "{:.2%}", "스프레드": "{:.2%}"}),
                 use_container_width=True, hide_index=True)
    st.line_chart(cdf.set_index("시점(년)")[["무위험 현물", "위험 현물", "위험 선도"]])
    if len(t.cr_curve) >= 2 and t.y_type == "par":
        st.markdown("**부트스트래핑 과정**")
        dfs = bootstrap_df(t.cr_curve, t.T, t.cmp_cr)
        bt = pd.DataFrame([[d[0], _lin(t.cr_curve, d[0]), d[1], -math.log(d[1])/d[0]]
                           for d in dfs if d[0] > 0],
                          columns=["만기(년)", "만기수익률", "할인계수", "현물이자율(연속)"])
        st.dataframe(bt.style.format({"만기(년)": "{:.2f}", "만기수익률": "{:.2%}",
                                      "할인계수": "{:.6f}", "현물이자율(연속)": "{:.4%}"}),
                     use_container_width=True, hide_index=True, height=260)
        st.caption("각 이표 시점마다 1 = 이자 × 앞선 할인계수 합 + 그 시점 할인계수 를 풀어 "
                   "할인계수를 앞에서부터 순차로 구합니다. 현물이자율은 −LN(할인계수) ÷ 만기입니다.")
    step_df = math.exp(-sum(forward_rate(CR, i*dt_, (i+1)*dt_)*dt_ for i in range(t.n)))
    ok = abs(step_df - math.exp(-CR(t.T)*t.T)) < 1e-8
    st.caption(f"검산 — 스텝별 선도이자율을 {t.n}번 곱한 값 {step_df:.8f} 과 "
               f"만기 현물 할인계수 {math.exp(-CR(t.T)*t.T):.8f} 가 "
               + ("일치합니다." if ok else "어긋납니다. 곡선 입력을 확인하십시오."))
    st.caption("선도이자율  f(t, t+Δt) = [ r(t+Δt)×(t+Δt) − r(t)×t ] ÷ Δt. "
               "격자의 한 스텝 할인이 이 값을 씁니다.")

if _detail_section == _detail_sections[4]:
  if not vol_listed():
    # 비상장 — 대상회사 주가가 없다. 피어별 변동성을 보여 준다
    _pe = st.session_state.get("peers") or []
    _po = st.session_state.get("peer_opt") or {}
    if not _pe:
        st.info("비상장사로 두셨습니다. 「입력·시장자료 → 주가·변동성·금리 자료 → 변동성 산출」에서 피어 주가를 받거나 "
                "여러 종목 종가 파일을 넣으십시오.")
    else:
        _pvv = [(nm, vol_from(px, _po.get("tdays", 250), _po.get("drop", True)), px) for nm, px in _pe]
        st.dataframe(pd.DataFrame(
            [[nm, x["annual"], x["daily"], x["n"], x["removed"], px[0][0], px[-1][0]]
             for nm, x, px in _pvv if x],
            columns=["피어", "연 변동성", "일 변동성", "수익률", "제외", "시작", "끝"]).style.format(
            {"연 변동성": "{:.2%}", "일 변동성": "{:.2%}"}), use_container_width=True, hide_index=True)
        _ag = st.session_state.get("peer_agg")
        st.caption(f"연 거래일수 {_po.get('tdays', 250)}일 · 이상치 제거 "
                   f"{'함' if _po.get('drop', True) else '안 함'} · 조회 종료일 {_po.get('asof', '')}"
                   + (f" · 피어 종합 {_ag*100:.2f}%" if _ag is not None else "")
                   + f" · 적용 σ {t.sig*100:.2f}%")
  else:
    if not st.session_state.prices:
        st.info("「입력·시장자료 → 주가·변동성·금리 자료 → 변동성 산출」에서 주가를 받거나 파일을 넣으십시오. "
                "야후 파이낸스의 수정종가를 사용합니다.")
    else:
        px = st.session_state.prices
        pxdf = pd.DataFrame(px, columns=["날짜", "종가"])
        c1, c2, c3 = st.columns(3)
        c1.metric("종가 개수", f"{len(px):,}")
        c2.metric("기간", f"{px[0][0]} ~ {px[-1][0]}")
        c3.metric("마지막 종가", f"{px[-1][1]:,.0f}")
        st.caption("출처 " + st.session_state.get("px_src", ""))
        if pxdf["날짜"].iloc[0]:
            st.line_chart(pxdf.set_index("날짜")["종가"])
        _vo = st.session_state.get('vol_opt') or {}
        v = vol_from(px, _vo.get('tdays', 250), True); v0 = vol_from(px, _vo.get('tdays', 250), False)
        st.dataframe(pd.DataFrame([
            ["이상치 제거", v["annual"], v["daily"], v["n"]-v["removed"], v["removed"]],
            ["이상치 포함", v0["annual"], v0["daily"], v0["n"], 0]],
            columns=["구분", "연 변동성", "일 변동성", "관측", "제거"]).style.format(
            {"연 변동성": "{:.2%}", "일 변동성": "{:.2%}"}),
            use_container_width=True, hide_index=True)
        st.caption(f"정상범위 {v['lo']*100:.2f}% ~ {v['hi']*100:.2f}% — "
                   "일별 로그수익률의 중앙값에서 중앙값 절대편차의 2.5배를 벗어난 값을 뺍니다.")
        st.markdown("**최근 10일**")
        st.dataframe(pxdf.tail(10).iloc[::-1].style.format({"종가": "{:,.0f}"}),
                     use_container_width=True, hide_index=True)

if _detail_section == _detail_sections[5]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    # 한글 글꼴이 없으면 글자가 네모로 나온다. 찾으면 쓰고, 없으면 영문으로 그린다.
    _kf = use_korean_font()
    _L = (dict(x="스텝", y="주가 수준",
               lg=["전환", "조기상환", "매도청구", "보유", "만기상환"]) if _kf else
          dict(x="step", y="stock level",
               lg=["Convert", "Put", "Call", "Hold", "Redeem"]))
    n = t.n
    idx = {}
    for k, v in full["memo"].items():
        kk = (k[0], k[1])
        if kk not in idx: idx[kk] = v
    code_map = {"conv": 1, "auto": 1, "ipo": 1, "put": 2, "call": 3, "hold": 4, "mat": 5}
    grid = np.full((n+1, n+1), np.nan)
    for (i, j), o in idx.items():
        if j <= i: grid[n-j, i] = code_map[o["kind"]]
    cmap = ListedColormap(["#1b6b5a", "#7a4b1e", "#a3312a", "#e4e8ec", "#9aa4ae"])
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.imshow(grid, aspect="auto", cmap=cmap, vmin=0.5, vmax=5.5, interpolation="nearest")
    ax.set_xlabel(_L["x"]); ax.set_ylabel(_L["y"])
    ax.set_yticks([]); ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=[Patch(facecolor=c, label=l) for c, l in
                       zip(["#1b6b5a", "#7a4b1e", "#a3312a", "#e4e8ec", "#9aa4ae"],
                           _L["lg"])],
              loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=5, frameon=False)
    st.pyplot(fig, use_container_width=True)
    if not _kf:
        st.caption("한글 글꼴이 없어 그림만 영문으로 그렸습니다. 표와 설명은 그대로입니다. "
                   "한글로 보시려면 저장소에 `packages.txt` 를 만들어 `fonts-nanum` "
                   "한 줄을 넣으십시오. 다만 apt 설치가 실패하면 앱이 아예 뜨지 "
                   "않으므로, 넣으신 뒤 재시작이 되는지 확인하십시오.")
    D = full["dist"]; tot = D["conv"]+D["put"]+D["call"]+D["mat"] or 1
    if bw_cash(t):
        # 사채가 어떻게 끝나는지와 신주인수권을 행사하는지는 다른 사건이다.
        # 분리형이면 사채를 상환받아도 신주인수권이 남아 둘이 겹칠 수 있다.
        _w, _tw = D.get("wex", 0.0), D.get("tw", 0.0)
        st.dataframe(pd.DataFrame([
            [LB["put"], D["put"]/tot, D["tp"]/D["put"]/full["mper"] if D["put"] else None],
            [LB["call"], D["call"]/tot, D["tk"]/D["call"]/full["mper"] if D["call"] else None],
            ["만기 상환", D["mat"]/tot, t.T*12],
            ["— 신주인수권 행사 (사채와 별개)", _w,
             _tw/_w/full["mper"] if _w else None]],
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        st.caption("위 세 줄은 **사채**가 어떻게 끝나는지의 분포이고 합이 100% 입니다. "
                   "마지막 줄은 **신주인수권**을 행사할 확률로, 행사해도 사채가 남으므로 "
                   "위 분포와 따로 셉니다"
                   + (" — 분리형이라 사채를 상환받아도 신주인수권은 남습니다."
                      if int(t.bw_detach) == 1 else
                      " — 비분리형이라 사채가 소멸하는 순간 미행사분은 사라집니다.")
                   + " 기준일 주가에서 잰 위험중립확률이라 실제 행사 예측이 아닙니다.")
    else:
        # 「그중 매도청구 대응」은 전환에서 빼지 않고 겹쳐 센다. 들여쓴 줄이라
        # 합계에 들어가지 않는다 — 위 세 줄과 만기 줄만 더해 100% 다.
        _cc = D.get("conv_called", 0.0)
        _rows = [["전환", D["conv"]/tot,
                  D["tc"]/D["conv"]/full["mper"] if D["conv"] else None]]
        if _cc > 1e-12:
            _rows.append(["　— 그중 매도청구 대응 전환", _cc/tot,
                          D["tcc"]/_cc/full["mper"]])
        _rows += [
            [LB["put"], D["put"]/tot, D["tp"]/D["put"]/full["mper"] if D["put"] else None],
            [LB["call"], D["call"]/tot, D["tk"]/D["call"]/full["mper"] if D["call"] else None],
            ["만기 상환", D["mat"]/tot, t.T*12]]
        st.dataframe(pd.DataFrame(_rows,
            columns=["유형", "비중", "평균 시점(개월)"]).style.format(
            {"비중": "{:.1%}", "평균 시점(개월)": "{:,.1f}"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        st.caption("거의 모든 경로가 만기 전에 끝나면 기대만기가 계약만기보다 짧다는 뜻이고, "
                   "장기 할인율의 영향이 줄어듭니다."
                   + ("  만기에 존속기간이 만료되어 **보통주로 자동전환**되는 몫은 "
                      "「전환」에 들어갑니다 — 「만기 상환」 줄은 그때 상환청구권을 "
                      "골라 현금으로 끝난 몫입니다." if auto_conv(t) else ""))
        if _cc > 1e-12:
            st.caption(f"**「그중 매도청구 대응 전환」은 전환 {D['conv']/tot:.1%} 안에 "
                       f"들어 있는 몫**이라 합계에 두 번 세지 않습니다. 발행자가 "
                       "매도청구하지 않았다면 그 노드에서 투자자는 전환하지 않았을 "
                       "것입니다 — 콜이 상방을 눌러 전환을 앞당긴 자리입니다. "
                       "이 비중이 높으면 **매도청구권이 기대만기를 짧게 만들고 "
                       "있다**는 뜻이므로, 계약서의 매도청구 조건을 다시 보십시오.")

if _detail_section == _detail_sections[6]:
  _ah6 = acc_host(t, full, b0, b1, b2, ca)
  if holder_on(t):
    st.info("**투자자 관점이라 상각표를 만들지 않습니다.** 복합계약 전체를 당기손익-공정가치로 "
            "측정하므로 유효이자율로 상각할 대상이 없습니다(제1109호 문단 4.3.2 · 4.1.4). "
            "분리형 BW 의 사채를 상각후원가로 분류했다면 그 상각표는 발행 조건으로 따로 "
            "만드십시오.")
  elif acc_mode(t) == "fv_only":
    st.warning(FV_ONLY_NOTE)
  elif _ah6 is None and not fvpl_on(t):
    st.warning(HOST_NONPOS_NOTE)
  elif _ah6 is None:
    st.info("**복합계약 전체를 당기손익-공정가치로 지정**하셨으므로 유효이자율 "
            "상각표를 만들지 않습니다.\n\n"
            "상각표는 「상각후원가로 측정하는 주계약」이 있어야 성립합니다. 전체를 "
            "공정가치로 재면 그 주계약이 없습니다 — 배분표가 한 줄이고, 후속측정은 "
            "상각이 아니라 **전체를 매 결산 공정가치로 다시 재는 것**입니다. "
            "이자비용도 유효이자율로 계산한 금액이 아니라 공정가치 변동에 녹아 듭니다.\n\n"
            "표면이자를 따로 표시하실 거라면 계약상 지급액을 그대로 쓰시고, "
            "자기신용위험 변동분은 기타포괄손익으로 나누셔야 합니다 (제1109호 "
            "문단 5.7.7). 이 앱은 그 분해를 하지 않습니다.")
    st.caption("지정을 해제하시면 요소별 배분과 상각표가 다시 나옵니다. "
               "전환권을 **자본**으로 두셨다면 애초에 지정할 수 없어(문단 4.2.2) "
               "이 화면이 뜨지 않습니다.")
  else:
    _ex = eir_expect(t)
    r_eir, rows_eir, red, nper = eir_table(t, _ah6, _ex)
    if _ex is not None:
        st.info(f"**조기상환권을 분리하지 않으므로 기대만기로 상각합니다.** 첫 조기상환 가능일"
                f"(발행일 기준 {_ex[2]:,.1f}개월 · 평가기준일부터 {_ex[0]:.2f}년)을 만기로, 그 시점 "
                f"행사금액 {_ex[1]:,.4f} 를 만기 현금흐름으로 두고 유효이자율을 구합니다. 계약만기 "
                "현금흐름으로 구하면 첫 조기상환일의 행사금액과 장부금액이 벌어져 이자비용·부채가 "
                "과소계상됩니다 (B4.3.5(5)(가)의 «행사가격 ≈ 상각후원가» 와 어긋납니다).")
    st.dataframe(pd.DataFrame([
        ["주계약 (옵션 없는 사채)", f"{b0:,.2f}"],
        [("기대만기 상환금액 (첫 조기상환 가능일 행사금액)" if _ex is not None else "만기상환금액"), f"{red:,.2f}"],
        ["표면이자 (회당)", f"{100*eff_cpn(t)*t.ipay/12:,.2f}"], ["상각 횟수", f"{nper}회"],
        ["유효이자율 (연, 이산복리)", f"{r_eir:.2%}"]], columns=["항목", "값"]),
        use_container_width=True, hide_index=True)
    amdf = pd.DataFrame(rows_eir, columns=["회차", "경과연수", "기초 장부금액",
                                           "이자비용", "지급이자", "기말 장부금액"])
    st.dataframe(amdf.style.format({"경과연수": "{:.2f}", "기초 장부금액": "{:,.2f}",
                                    "이자비용": "{:,.2f}", "지급이자": "{:,.2f}",
                                    "기말 장부금액": "{:,.2f}"}),
                 use_container_width=True, hide_index=True, height=320)
    st.caption("기말 장부금액이 만기에 상환금액과 일치해야 합니다.")

if _detail_section == _detail_sections[7] and st.button("상세 민감도 계산"):
    rows = []
    for dvv in (-0.15, -0.075, 0.0, 0.075, 0.15):
        tt = Terms(**asdict(t)); tt.sig = max(0.01, t.sig+dvv)
        rows.append([tt.sig, pick(engine(tt, call=False), t.model)])
    base = rows[2][1]
    st.dataframe(pd.DataFrame([[r[0], r[1], r[1]-base] for r in rows],
                              columns=["변동성", "전체 가치", "변화"]).style.format(
        {"변동성": "{:.1%}", "전체 가치": "{:,.2f}", "변화": "{:+,.2f}"}),
        use_container_width=True, hide_index=True)
    rows2 = []
    for dvv in (-0.05, -0.025, 0.0, 0.025, 0.05):
        tt = Terms(**asdict(t))
        tt.cr_curve = [(x, y+dvv) for x, y in t.cr_curve]
        tt.cr_curve_b = [(x, y+dvv) for x, y in t.cr_curve_b]
        rows2.append([dvv, pick(engine(tt, call=False), t.model)])
    base2 = rows2[2][1]
    st.dataframe(pd.DataFrame([[r[0], r[1], r[1]-base2] for r in rows2],
                              columns=["할인율 조정", "전체 가치", "변화"]).style.format(
        {"할인율 조정": "{:+.1%}", "전체 가치": "{:,.2f}", "변화": "{:+,.2f}"}),
        use_container_width=True, hide_index=True)
    st.caption("입력변수별 민감도를 비교하고 중요한 비관측 투입변수와 공시 요구사항을 별도로 검토하십시오.")

if _detail_section == _detail_sections[8]:
    st.write("**계산이 성립하는지**만 봅니다. 무엇을 분리하고 어떻게 잴지는 "
             "「분리 판단」 탭으로 옮겼습니다.")
    imm = 100*t.S0/t.K0
    tot_al = allocate(t, full, b0, b1, b2, ca)[0][-1][1]
    checks = [("위험중립가중치 q · 첫 구간", f"{full['q']:.4f}", 0 < full["q"] < 1),
              ("위험중립가중치 q · 전 구간 범위",
               f"[{full['qmin']:.4f}, {full['qmax']:.4f}]  (구간 {full['n']}개)",
               not full["qbad"]),
              ("상승계수 u", f"{full['u']:.4f}", full["u"] > 1),
              ("전체 가치 ≥ 순수사채가치", f"{b2:,.2f} ≥ {full['host']:,.2f}",
               b2 >= full["host"]-1e-6),
              ("전체 가치 ≥ 즉시 전환가치", f"{b2:,.2f} ≥ {imm:,.2f}",
               not (t.cv_s <= 0 and b2 < imm-1e-6)),
              ("배분 합계 = 100", f"{tot_al:,.2f}", abs(tot_al-100) < 0.01)]
    st.dataframe(pd.DataFrame([[k, v, "적합" if ok else "확인 필요"] for k, v, ok in checks],
                              columns=["항목", "값", "판정"]),
                 use_container_width=True, hide_index=True)
    st.caption("위험중립가중치가 0과 1을 벗어나면 현재 금리·변동성과 격자 간격의 조합을 검토해야 합니다. "
               "구간마다 선도이자율로 다시 계산되므로 **첫 구간만 보아서는 안 됩니다** — "
               "전 구간의 범위를 함께 표시합니다.")

    # ── 남이 이 격자를 검토한다면 ──
    # 격자 모델 리뷰에서 실제로 묻는 것들. 앱이 답할 수 있는 것은 채워 두고,
    # 계약을 읽어야 답하는 것만 사용자에게 남긴다.
    with st.expander("격자모형 검토 항목"):
        _ovq = pc_overlap(t) if t.k_w > 0 else []
        _ovd = [x for x in _ovq if x[2] > x[3] + 1e-9]
        # 지분·부채로 갈랐을 때 어느 쪽도 음수가 되면 안 된다. 음수가 나오면
        # 노드 결정과 D/E 재분류가 어긋났다는 뜻이다.
        _neg = min([min(o["E"], o["B"]) for o in full["memo"].values()] or [0.0])
        _flags = sum(1 for i in range(full["n"]+1)
                     if full["kstrike"](i) is not None)
        # ── 여기부터는 «설명» 이 아니라 실제로 재는 검산이다 ──
        # 결정과 지분·부채 배정이 어긋난 노드. 전환이면 부채가 0, 상환이면 지분이
        # 0 이어야 한다. 신주인수권부사채는 사채와 신주인수권이 따로라 상환
        # 노드에도 지분이 남으므로 그 상품은 빼고 센다.
        _bw = bw_cash(t)
        _mis = _ill = 0
        for _o in full["memo"].values():
            _kd = _o["kind"]
            if _kd in ("conv", "auto", "ipo") and abs(_o["B"]) > 1e-9: _mis += 1
            if (not _bw) and _kd in ("put", "call", "mat") and abs(_o["E"]) > 1e-9:
                _mis += 1
            # 행사할 수 없는 자리에서 그 결정이 났는가
            if _kd == "put" and _o.get("pv", 0.0) <= 0: _ill += 1
            if _kd == "call" and _o.get("kv", math.inf) == math.inf: _ill += 1
            if _kd in ("conv", "auto", "ipo") and _o.get("cv", 0.0) <= 0: _ill += 1
        # 순차 차감으로 잰 권리 값이 음수는 아닌가 (풋·전환·매도청구권)
        _rt = full["memo"][full["root"]]
        _neg2 = [nm for nm, v in (("조기상환청구권", b1-b0), ("전환권", b2-b1),
                                  (LB["call"], ca)) if v < -1e-9]
        _q = [
            ("풋과 콜이 동시에 가능한 스텝은 어디인가",
             ("없다 — 두 행사기간이 겹치지 않는다" if not _ovq else
              f"{len(_ovq)}개 노드. 첫 자리는 발행일 기준 {_ovq[0][1]:,.0f}개월"
              f"(스텝 {_ovq[0][0]})")),
            ("그때 누가 먼저 결정하는가",
             ("해당 없음" if not _ovq else
              ("발행자 매도청구 우선" if int(t.pc_order) == 1
               else "투자자 조기상환 우선"))),
            ("계약서상 그 우선순위가 맞는가",
             ("해당 없음" if not _ovq else
              ("**확인 필요** — 값이 갈리는 자리가 "
               f"{len(_ovd)}개 있다 (첫 자리 조기상환 {_ovd[0][2]:,.4f} 대 "
               f"매도청구 {_ovd[0][3]:,.4f})" if _ovd else
               "겹치지만 매도청구금액이 늘 크거나 같아 값이 갈리지 않는다"))),
            ("매도청구를 당하면 전환권이 남는가",
             ("해당 없음 — 매도청구권이 없다" if t.k_w <= 0 else
              "남는다. 격자가 전환가치와 매도청구금액을 함께 견준다 — "
              "매도청구가 걸려도 전환이 더 크면 전환을 고른다")),
            ("조기상환을 고른 뒤 매도청구가 또 작동하지는 않는가",
             "않는다. 한 노드에서 한 갈래만 고르고 그 자리에서 계약이 끝난다"),
            ("최종 상태가 지분·부채로 맞게 갈렸는가",
             (f"**어긋난 노드 {_mis}개** · 음수 노드 없음 — 전환이면 (전환가치, 0), "
              "상환이면 (0, 상환금액), 보유면 각각 다른 이자율로 할인한 값이다. "
              f"{len(full['memo']):,}개 노드를 전부 확인했다"
              if (_mis == 0 and _neg >= -1e-9) else
              f"**확인 필요** — 결정과 지분·부채가 어긋난 노드 {_mis}개"
              + (f", 음수가 나온 노드도 있다 (최소 {_neg:,.4f})"
                 if _neg < -1e-9 else ""))),
            ("행사할 수 없는 자리에서 권리가 작동하지는 않는가",
             (f"작동하지 않는다 — {len(full['memo']):,}개 노드를 전부 확인했고 "
              f"어긋난 곳이 없다. 계약일 이후 첫 노드부터 주기마다만 열리고, "
              f"매도청구가 열리는 노드는 {_flags}개다"
              if _ill == 0 else f"**확인 필요** — 어긋난 노드 {_ill}개")),
            ("지분·부채 분해가 모형과 맞는가",
             (f"맞는다. 한 노드가 두 모형을 함께 담는다 — **지분+부채는 TF**"
              f"({_rt['E']+_rt['B']:,.4f} = 결과 {full['TF']:,.4f}), "
              f"**V 는 GS**({_rt['V']:,.4f} = 결과 {full['GS']:,.4f}). "
              "V ≠ 지분+부채 인 것은 결함이 아니라 두 모형이 다른 답을 낸다는 뜻이다"
              if (abs(full["TF"] - (_rt["E"]+_rt["B"])) < 1e-9
                  and abs(full["GS"] - _rt["V"]) < 1e-9)
              else "**확인 필요** — 뿌리 노드가 결과와 어긋난다")),
            ("권리 값이 음수는 아닌가",
             (f"모두 0 이상 — 조기상환청구권 {b1-b0:,.4f} · 전환권 {b2-b1:,.4f} · "
              f"{LB['call']} {ca:,.4f}. 권리를 더하면 값이 올라가고 발행자 권리를 "
              "빼면 내려가야 한다"
              if not _neg2 else
              f"**확인 필요** — 음수인 권리: {', '.join(_neg2)}")),
        ]
        st.dataframe(pd.DataFrame(_q, columns=["질문", "답"]),
                     use_container_width=True, hide_index=True)
        if _ovd:
            st.warning("세 번째 줄이 **확인 필요**입니다. 계약서의 통지기간과 "
                       "우선순위 조항을 보시고, 「권리·금리 분석」의 비교표에서 두 "
                       "갈래의 값 차이를 확인하십시오.")
        st.caption('이 표는 구현된 격자의 내부 일관성을 점검한 결과입니다. 계약상 행사조건·우선순위, 자료의 적정성과 회계분류는 원문 및 독립 검산자료로 별도 검토하십시오.')
        if st.button('추가 수치 검산', key='btn_model_checks'):
            _mc = model_checks(t, full, b0, b1, b2, ca, eir_or_none(t, full, b0, b1, b2, ca))
            st.dataframe(pd.DataFrame(_mc, columns=['항목', '값', '결과', '설명']),
                         use_container_width=True, hide_index=True)
            _mcbad = [nm for nm, _, vd, _ in _mc if vd == '확인 필요']
            if _mcbad:
                st.warning('확인할 사항: ' + ', '.join(_mcbad))

        # ── 극단 시험 — 격자를 두 번 더 돌리므로 눌렀을 때만 ──
        st.markdown("**극단에서 값이 붙는가**")
        st.caption("주가를 아주 낮추면 전환권이 무가치해져 **전체 = 사채 + 조기상환권** "
                   "이어야 하고, 아주 높이면 **전체 ÷ 전환가치 = 1** 로 붙어야 합니다. "
                   "격자를 두 번 더 돌리므로 누르셨을 때만 평가합니다.")
        if st.button("극단 두 곳에서 재 본다", key="btn_extreme"):
            with st.spinner("격자를 두 번 더 돌립니다"):
                _lo = Terms(**asdict(t)); _lo.S0 = t.K0*0.001; derive(_lo)
                _hi = Terms(**asdict(t)); _hi.S0 = t.K0*100.0; derive(_hi)
                _fl, _l0, _l1, _l2, _lc, _ = decompose(_lo)
                _fh, _h0, _h1, _h2, _hc, _ = decompose(_hi)
            _cvh = 100*_hi.S0/_hi.K0
            _gap1, _gap2 = abs(_l2 - _l1), abs(_h2/_cvh - 1.0)
            st.dataframe(pd.DataFrame([
                [f"주가 {_lo.S0:,.2f}원 (인수가액의 0.1%)",
                 f"전체 {_l2:,.4f}", f"사채+조기상환권 {_l1:,.4f}",
                 ("붙는다" if _gap1 < 1e-4 else f"차이 {_gap1:,.4f} — 확인 필요")],
                [f"주가 {_hi.S0:,.0f}원 (인수가액의 100배)",
                 f"전체 ÷ 전환가치 {_h2/_cvh:,.6f}", "1.000000",
                 ("붙는다" if _gap2 < 1e-4 else f"차이 {_gap2:,.6f} — 확인 필요")]],
                columns=["극단", "잰 값", "가야 할 곳", "판정"]),
                use_container_width=True, hide_index=True)
            st.caption("붙지 않으면 전환가치·상환금액 배선이나 리픽싱 하한을 "
                       "의심하십시오. 매도청구권은 이 시험에 넣지 않습니다 — "
                       "한도·의무보유가 걸려 있어 극단에서도 단순한 값으로 "
                       "붙지 않습니다.")

    st.markdown("**신용스프레드가 발행조건과 맞는가**")
    st.caption('거래가격이 공정가치이며 동일한 권리 범위라는 전제를 검토한 경우, 목표 100과 모형가치의 차이를 금리 보정으로 비교합니다. 거래가격 차이의 원인이 신용스프레드라고 단정하지 않습니다.')
    if t.elapsed_m > 0.01:
        st.info(f"평가기준일이 발행일보다 {t.elapsed_m:.1f}개월 뒤입니다. 그 사이 주가와 "
                "신용도가 바뀌었으므로 전체가 100 을 벗어나는 것이 정상입니다. "
                f"현재 전체 {b2:,.2f}. 역산은 발행일 평가에서만 돌립니다.")
    elif len(t.cr_curve) < 2:
        st.info("위험 곡선을 두 점 이상 넣으셔야 역산할 수 있습니다.")
    else:
        _lv = [y for _, y in t.cr_curve]
        _sh = lambda d: [(x, y+d) for x, y in t.cr_curve]

        def _tot(d):
            tt = Terms(**asdict(t)); tt.cr_curve = _sh(d)
            derive(tt); return decompose(tt)[3]
        try:
            _a, _b = -min(_lv)+1e-4, 0.60          # 곡선을 평행이동할 폭
            if _tot(_b) > 100:
                # 스프레드를 아무리 올려도 못 내려간다 — 사채요소가 아니라
                # 전환조건이 값을 떠받치고 있다는 뜻이다.
                _flr = _tot(_b)
                st.warning(inst_text(t,
                    f"신용스프레드를 60%p 올려도 전체가 {_flr:,.2f} 아래로 "
                    f"내려가지 않습니다 (현재 {b2:,.2f}). 사채요소를 거의 0 으로 "
                    "만들어도 그만큼이 남는다는 뜻이므로, **원인은 신용이 아니라 "
                    "전환조건**입니다. 주가 ÷ 전환가액 "
                    f"{t.S0/max(t.K0,1e-9):.2f}, 변동성 {t.sig:.1%}, "
                    f"리픽싱 {'있음' if t.rfx_mode else '없음'} 을 먼저 보십시오. "
                    "메자닌은 투자자에게 유리하게 발행되는 경우가 많아 실제로 "
                    "100 을 넘기도 합니다 — 그러면 그 사실을 조서에 적으면 됩니다."))
            elif _tot(_a) < 100:
                st.warning(
                    f"스프레드를 0 까지 낮춰도 전체가 100 에 못 미칩니다 "
                    f"(현재 {b2:,.2f}). 만기보장수익률이나 행사조건이 빠지지 "
                    "않았는지 확인하십시오.")
            else:
                for _ in range(36):
                    _m = (_a+_b)/2
                    if _tot(_m) > 100: _a = _m
                    else: _b = _m
                _d = (_a+_b)/2
                _t2 = Terms(**asdict(t)); _t2.cr_curve = _sh(_d)
                derive(_t2)
                _f2, _z0, _z1, _z2, _zc, _zv = decompose(_t2)
                _RF2, _CR2 = curves(_t2)
                st.dataframe(pd.DataFrame([
                    ["입력한 위험이자율 (잔존만기)", f"{CRc(t.T)*100:.2f}%",
                     f"전체 {b2:,.2f}"],
                    ["역산된 위험이자율", f"{_CR2(t.T)*100:.2f}%", "전체 100.00"],
                    ["차이 (곡선 평행이동)", f"{_d*100:+.2f}%p", ""],
                    ["역산 상태의 조기상환권", f"{_z1-_z0:,.2f}",
                     f"현재 {b1-b0:,.2f}"],
                    ["역산 상태의 전환권대가", f"{_zv:,.2f}", f"현재 {conv:,.2f}"]],
                    columns=["항목", "값", "참고"]),
                    use_container_width=True, hide_index=True)
                if abs(b2-100) < 1.0:
                    st.info('모형가치와 원금 100의 차이가 1 미만입니다. 금액의 일치만으로 거래가격이나 투입변수의 적정성을 확인할 수는 없습니다.')
                else:
                    st.warning(f'모형가치와 원금 100의 차이는 {b2-100:+,.2f}입니다. 최초 거래의 조건, 권리별 대가, 평가기준일 및 시장자료를 대조하십시오. 역산 금리만으로 입력 스프레드의 적정성을 판단하지 않습니다.')
        except Exception as _ex:
            st.info(f"역산하지 못했습니다 — {_ex}")
        st.caption('역산 금리는 입력 가정과 목표가격 간 차이를 확인하는 참고값입니다. 관측 시장자료를 대체하려면 거래의 정상성 및 모형 보정 근거를 별도로 검토하십시오.')

if _detail_section == _detail_sections[9]:
    st.write("가정 · 트리 시트 · 이자율곡선 · 결과 · 회계처리 · 상각표로 이루어진 조서를 만듭니다. "
             "트리 하나가 시트 하나이고, 모든 시트의 머리 17행이 같은 형태입니다.")
    kind = st.radio("조서 형식", ["값", "수식"], horizontal=True,
                    format_func=lambda x: "값 조서 — 계산 결과 스냅샷"
                    if x == "값" else "수식 조서 — 엑셀에서 다시 계산됨")
    if kind == "값":
        st.caption("앱이 계산한 값을 그대로 담습니다. 셀을 바꿔도 다시 계산되지 않으므로 "
                   "입력 가정과 검토기록을 확인한 후 사용하십시오. 상태확장을 포함한 모든 설정에서 만들 수 있습니다.")
    else:
        st.caption("가정 시트의 노란 셀을 바꾸면 엑셀 안에서 트리가 다시 계산됩니다. "
                   "선도이자율만 값으로 들어갑니다. 노드 수와 리픽싱 주기는 격자 구조라 바꿀 수 없습니다.")
        if t.carry == 0 and t.rfx_mode > 0:
            st.warning("상태확장은 한 노드에 전환가격이 여럿이라 수식으로 펼 수 없습니다. "
                       "같은 계산방법의 값 조서를 사용하십시오. 근사 수식 조서가 필요하면 계산방법을 명시적으로 변경한 후 다시 평가하십시오.")
    # 산출해 놓고 적용하지 않은 변동성이 있으면 여기서 막아 세운다. 조서를
    # 만드는 자리가 마지막 관문이라, 입력화면 경고를 놓쳐도 여기서는 보인다.
    _unap = []
    _pv = (st.session_state.get("prices") or []) if vol_listed() else []
    _pa = st.session_state.get("peer_agg") if not vol_listed() else None
    if _pa is not None and abs(_pa - t.sig) > 5e-5:
        _unap.append(f"피어 종합 변동성 — 산출 **{_pa*100:.2f}%** / "
                     f"조서에 들어가는 값 **{t.sig*100:.2f}%**")
    if _pv:
        _vv = vol_from(_pv, (st.session_state.get("vol_opt") or {}).get("tdays", 250),
                       (st.session_state.get("vol_opt") or {}).get("drop", True))
        if _vv and abs(_vv["annual"] - t.sig) > 5e-5:
            _unap.append(f"주가 변동성 — 산출 **{_vv['annual']*100:.2f}%** / "
                         f"조서에 들어가는 값 **{t.sig*100:.2f}%**")
    _rv = st.session_state.get("rate_series") or []
    if _rv and put_bdt_on(t):
        _ro = st.session_state.get("rate_opt") or {}
        _vr = rate_vol(_rv, _ro.get("tdays", 250), _ro.get("drop", True))
        if _vr and abs(_vr["annual"] - t.bdt_sig) > 5e-5:
            _unap.append(f"BDT 단기이자율 변동성 — 산출 **{_vr['annual']*100:.2f}%** / "
                         f"조서에 들어가는 값 **{t.bdt_sig*100:.2f}%**")
    if _unap:
        st.warning("**산출해 놓고 적용하지 않은 변동성이 있습니다.**\n\n"
                   + "\n".join(f"- {x}" for x in _unap)
                   + "\n\n왼쪽 칸의 「이 변동성 적용」을 누르셔야 조서에 들어갑니다. "
                     "지금 만들면 위의 **조서에 들어가는 값**으로 계산됩니다.")

    c1, c2 = st.columns([1, 2])
    if c1.button("조서 만들기", type="primary", use_container_width=True,
                 disabled=kind == "수식" and t.carry == 0 and t.rfx_mode > 0):
        try:
            with st.spinner("엑셀 작성 중"):
                # 산출내역을 조서 안에 함께 싣는다. 수식 조서에서는 종가·고시
                # 수익률이 트리까지 이어져, 한 파일 안에서 인풋을 흔들 수 있다.

                _rt = ([(st.session_state.get("rate_src") or "금리",
                         st.session_state.rate_series)]
                       if st.session_state.get("rate_series") else None)
                _att = dict(px=vol_attach(),
                            rate=(_rt, st.session_state.get("rate_opt")) if _rt else None,
                            rate_how=st.session_state.get("rate_how", ""),
                            ir=bool(len(t.rf_curve) >= 2 and len(credit_curve(t)) >= 2))
                if kind == "값":
                    data = build_xlsx(t, full, b0, b1, b2, ca, conv,
                                      eir_or_none(t, full, b0, b1, b2, ca),
                                      attach=_att)
                    fn = f"{LB['short']}평가조서_값{tranche_tag(t)}_{dt.date.today()}.xlsx"
                else:
                    tf = Terms(**asdict(t))
                    if tf.carry == 0 and tf.rfx_mode > 0:
                        raise ValueError("상태확장 리픽싱의 계산방법을 조서 생성 중 바꿀 수 없습니다. 값 조서를 사용하십시오.")
                    ff, f0, f1, f2, fca, fconv = decompose(tf)
                    data = build_xlsx_formula(tf, ff, f0, f1, f2, fca, fconv,
                                              eir_or_none(tf, ff, f0, f1, f2, fca),
                                              attach=_att)
                    fn = f"{LB['short']}평가조서_수식{tranche_tag(t)}_{dt.date.today()}.xlsx"
            data = export_with_sources(data, t)
            st.session_state.report = (fn, data, _stamp(t, kind))
        except ModuleNotFoundError:
            st.error("openpyxl 이 없습니다.  pip install openpyxl  을 실행하고 다시 시도하십시오.")
        except Exception as ex:
            st.error(f"조서를 만들지 못했습니다 — {ex}")

    rep = st.session_state.get("report")
    if rep and len(rep) == 3 and rep[2] != _stamp(t, kind):
        # 조서를 만든 뒤 인풋이 바뀌었다. 예전 파일을 그대로 내주면 트리와
        # 상각표가 서로 다른 계약으로 계산된 조서가 손에 남는다.
        st.warning("**조서를 만든 뒤 인풋이 바뀌었습니다.** 예전 파일은 지웠으니 "
                   "「조서 만들기」를 다시 누르십시오. 그대로 두면 트리와 상각표가 "
                   "서로 다른 계약으로 계산된 조서가 나갑니다.")
        st.session_state.pop("report", None)
        rep = None
    if rep:
        fn, data = rep[0], rep[1]
        st.download_button(f"{fn} 내려받기  ({len(data)/1024:,.0f} KB)", data, fn,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", key="dl_report", use_container_width=True)
        st.caption("버튼이 보이지 않거나 눌러도 반응이 없으면 브라우저의 팝업·다운로드 차단을 확인하십시오.")

    st.divider()
    st.caption(
        "**산출내역은 이 조서 안에 함께 들어갑니다.** 따로 내려받아 철하실 것이 "
        "없습니다. 조서 뒤쪽에 「σ 표지 · σ 회사별」(변동성), 「σr …」(금리변동성), "
        "「IR 표지 · IR 입력곡선 · IR 곡선별 산출 · IR 선도이자율」(이자율) 시트가 "
        "붙습니다. 수식 조서에서는 트리 11·12행이 「IR 선도이자율」 표를 참조하므로, "
        "고시 수익률을 고치면 부트스트래핑 → 선도이자율 → 트리 → 배분까지 한 파일 "
        "안에서 따라 움직입니다. 변동성은 산출값과 적용값이 같을 때만 이어 붙입니다 "
        "— 다르면 값으로 두어 값 조서와 수식 조서가 갈라지지 않게 합니다.")
