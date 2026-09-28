# -*- coding: utf-8 -*-
"""주가 변동성 전용 앱 — 본 앱보다 훨씬 가볍다.

    streamlit run vol_app.py

주가를 받아(야후) 연 변동성을 재고, 결과를 **변동성 패키지(JSON)** 로 내려받는다. 이 패키지를
평가 도구(tools/run_valuation.py --vol)에 주면 σ 가 적용되고, 조서에 변동성 산출내역 시트
(종가·수익률·이상치·종합까지 수식)가 함께 실린다. 계산 함수는 본 앱(app.py)의 것을 그대로 쓴다 —
본 앱에서 잰 값과 같다.
"""
import os, json, datetime as dt
import streamlit as st
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read().split("st.set_page_config")[0]
import sys, types
_m = sys.modules.get("cbapp_vol") or types.ModuleType("cbapp_vol")
sys.modules["cbapp_vol"] = _m                                    # dataclass 가 모듈을 찾는다
exec(compile(_src, "app.py", "exec"), _m.__dict__)
A = _m.__dict__
fetch_prices, vol_from = A["fetch_prices"], A["vol_from"]
parse_prices_multi, read_upload = A["parse_prices_multi"], A["read_upload"]
build_xlsx_vol = A["build_xlsx_vol"]

PICK = {"median": "중앙값", "mean": "단순평균", "max": "최댓값", "min": "최솟값"}


def agg(vals, pick):
    a = sorted(vals)
    if not a: return None
    if len(a) == 1: return a[0]
    if pick == "mean": return sum(a)/len(a)
    if pick == "max": return a[-1]
    if pick == "min": return a[0]
    return a[len(a)//2] if len(a) % 2 else (a[len(a)//2-1]+a[len(a)//2])/2


st.set_page_config(page_title="주가 변동성", layout="centered")
st.title("주가 변동성")
st.caption("받은 결과를 **변동성 패키지(JSON)** 로 내려받아 평가 담당(Claude)에게 주십시오. "
           "평가 도구가 σ 와 산출내역 시트를 조서에 그대로 넣습니다.")

listed = st.radio("대상회사", ["비상장사 — 피어로 산출", "상장사 — 대상회사 주가"], horizontal=True) \
    .startswith("상장")
if listed:
    codes = st.text_input("종목코드 · 이름", placeholder="030190,NICE평가정보",
                          help="국내 6자리 코드(코스닥·코스피를 차례로 찾는다) 또는 해외 티커.")
    lines = [codes] if codes.strip() else []
else:
    txt = st.text_area("피어 목록 — 한 줄에 하나, `코드` 또는 `코드,이름`", height=130,
                       placeholder="377450,리파인\n030190,NICE평가정보\n092130,이크레더블")
    lines = [x for x in txt.splitlines() if x.strip()]

c1, c2 = st.columns(2)
days = int(c1.number_input("조회 일수 (거래일)", value=250, step=10, min_value=30,
                           help="종목마다 받아올 거래일 수. 전기와 같게(예: 180영업일) 맞추십시오."))
tdays = int(c2.number_input("연 거래일수", value=250, step=5,
                            help="1년에 며칠 거래하나 — 연환산에 쓴다. 조회 일수와 다르다."))
asof = st.date_input("조회 종료일 (= 평가기준일)", value=dt.date.today())
c3, c4 = st.columns(2)
drop = c3.checkbox("이상치 제거 (중앙값 절대편차 2.5배)", value=True)
pick = c4.selectbox("종합 방법", list(PICK), format_func=PICK.get, disabled=listed)
if listed: pick = "median"

b1, b2 = st.columns(2)
if b1.button("주가 받기", type="primary", use_container_width=True, disabled=not lines):
    got, fail = [], []
    with st.spinner("받는 중"):
        for line in lines:
            parts = [x.strip() for x in line.replace("\t", ",").split(",")]
            nm = parts[1] if len(parts) > 1 and parts[1] else parts[0]
            try:
                rows, src = fetch_prices(parts[0], days, "", asof.isoformat())
                got.append((nm, rows))
            except Exception as ex:
                fail.append(f"{nm} — {ex}")
    st.session_state.series, st.session_state.src = got, "야후 파이낸스 · 수정주가"
    if fail: st.error("못 받은 것: " + " / ".join(fail))
up = b2.file_uploader("또는 종가 파일 (첫 열 일자, 나머지 열 종목)", type=["xlsx", "csv", "txt", "tsv"],
                      label_visibility="collapsed")
if up is not None and st.session_state.get("_up") != (up.name, up.size):
    try:
        st.session_state.series = parse_prices_multi(read_upload(up.name, up.getvalue()))
        st.session_state.src, st.session_state._up = f"파일 {up.name}", (up.name, up.size)
    except Exception as ex:
        st.error(str(ex))

series = st.session_state.get("series") or []
if series:
    vs = [(nm, vol_from(px, tdays, drop), px) for nm, px in series]
    vs = [(nm, v, px) for nm, v, px in vs if v]
    if vs:
        st.dataframe(pd.DataFrame(
            [[nm, v["annual"], v["n"], v["removed"], px[0][0], px[-1][0]] for nm, v, px in vs],
            columns=["회사", "연 변동성", "수익률 수", "이상치 제외", "첫 일자", "끝 일자"]).style.format(
            {"연 변동성": "{:.2%}"}), use_container_width=True, hide_index=True)
        sig = agg([v["annual"] for _, v, _ in vs], pick)
        st.metric("적용 변동성" + ("" if listed else f" · {PICK[pick]}"), f"{sig*100:.2f}%")
        late = [nm for nm, _, px in vs if px[-1][0] > asof.isoformat()]
        if late: st.warning("조회 종료일 뒤 주가가 섞였습니다: " + ", ".join(late))
        opt = dict(tdays=tdays, drop=drop, pick=pick, asof=asof.isoformat(), days=days)
        pack = dict(kind="vol_pack", version=1, listed=listed, sigma=sig, opt=opt,
                    source=st.session_state.get("src", ""),
                    made_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    series=[[nm, [[d, float(p)] for d, p in px]] for nm, _, px in vs],
                    per_company=[[nm, v["annual"], v["n"], v["removed"]] for nm, v, _ in vs])
        d1, d2 = st.columns(2)
        d1.download_button("변동성 패키지 (JSON) 내려받기",
                           json.dumps(pack, ensure_ascii=False).encode(),
                           f"변동성패키지_{asof.isoformat()}.json", "application/json",
                           type="primary", use_container_width=True)
        try:
            xl = build_xlsx_vol([(nm, px) for nm, _, px in vs], tdays=tdays, drop=drop, pick=pick,
                                applied=sig, asof=asof, kind="stock")
            d2.download_button("산출내역 엑셀", xl, f"변동성산출내역_{asof.isoformat()}.xlsx",
                               use_container_width=True)
        except Exception as ex:
            d2.caption(f"산출내역 엑셀을 만들지 못했습니다 — {ex}")
