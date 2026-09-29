"""Numerical option comparisons; no contract-review or approval workflow."""
import copy
import pandas as pd
import streamlit as st
from valuation import legacy


def main(run):
    t, r = run.terms, run.raw
    key = run.summary['calculation_key']
    st.subheader('권리·금리 분석')
    st.caption('행사금액, 상각후원가, 금리격자 및 콜 평가방법 간 수치를 비교합니다. 표시한 비율로 분리 여부나 모형을 자동 결정하지 않습니다.')
    if t.p_s <= t.p_e:
        if st.button('상환금액·상각후원가 비교'):
            rate, rows, _, _ = legacy.eir_table(t, r['b0'])
            e = legacy.exercise_amounts(t, t.n, t.T/t.n)
            result = []
            lo, hi = legacy.step_mapper(t, t.n, t.T/t.n)
            period = max(1, round(t.p_f*t.n/(t.T*12)))
            for i in range(max(0,lo(t.p_s)), min(t.n,hi(t.p_e))+1):
                if not (e['p_on'](i) if e['p_on'] else (i-lo(t.p_s)) % period == 0):
                    continue
                month = e['cmonth'](i)
                amount = e['put'](i)
                time = i*t.T/t.n
                book = next((row[-1] for row in rows if row[1]>=time-1e-9), r['b0'])
                result.append([i, time, amount, book, amount-book, amount/book-1 if book else None])
            st.session_state.numeric_put = (key,result)
        saved = st.session_state.get('numeric_put')
        if saved and saved[0] == key:
            st.dataframe(pd.DataFrame(saved[1],columns=['스텝','경과연수','행사금액(100)','주계약 상각후원가(100)','차이','차이율']),hide_index=True)
        allowed = legacy.put_bdt_avail(t)
        if not allowed:
            st.caption('BDT 비교 지원 조건: TF 모형, 전환권 자본 분류 가정, 투자자 상환청구권 있음.')
        if st.button('TF·BDT 부채요소 비교', disabled=not allowed):
            fixed = copy.deepcopy(t);fixed.put_bdt=0
            tf = legacy.pick(legacy.engine(fixed,conv=False,put=True,call=False),fixed.model)
            bdt = legacy.bond_bdt(t,True)
            st.session_state.numeric_bdt=(key,tf,bdt)
        saved = st.session_state.get('numeric_bdt')
        if saved and saved[0] == key:
            _,tf,bdt=saved
            st.dataframe(pd.DataFrame([['TF 확정금리',tf],['BDT 금리격자',bdt],['차이',bdt-tf]],columns=['방법','부채요소(100)']),hide_index=True)
            st.caption(f'금리 변동성 {t.bdt_sig:.2%}. 현재 BDT는 부채요소 비교에 사용하며 전체 RCPS 주가 격자와 결합한 2요인 모형은 아닙니다.')
    if t.k_w>0 and not legacy.issuer_redeem(t):
        if st.button('제3자 콜 평가방법 비교'):
            values=[]
            for method in ((0,1,2) if t.model == 'TF' else (0,1)):
                alt=copy.deepcopy(t);alt.k_method=method
                result=legacy.decompose(alt)
                values.append([['콜 유무 가치 비교','옵션차익 혼합할인율','옵션차익 지분·부채 분리할인'][method],result[4]])
            st.session_state.numeric_call=(key,values)
        saved=st.session_state.get('numeric_call')
        if saved and saved[0]==key:
            st.dataframe(pd.DataFrame(saved[1],columns=['방법','콜 가치(100)']),hide_index=True)
