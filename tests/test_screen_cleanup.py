"""화면 정리 — 모음 입력 칸·계약조건 시나리오를 없애고, 추가 검토 항목을 출처·평가가정과 최초 인식 결과로 옮겼다."""
from streamlit.testing.v1 import AppTest

from synthetic_rcps_case import sample
from test_judgment_sources import call_case, day1_case
from test_v2_workflow import ROOT


def _app(case):
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
    app.session_state.case = case
    app.run()
    assert not app.exception, app.exception
    return app


def _evaluate(app):
    app.radio(key='_workflow_stage').set_value('평가·분석').run()
    next(b for b in app.button if b.label == '현재 입력으로 평가').click().run()
    assert not app.exception, app.exception
    return app


def test_input_screen_has_no_catch_all_and_places_moved_fields():
    app = _app(call_case())
    labels = [e.label for e in app.expander]
    assert '후속평가·역산·기타 상세 입력' not in labels
    assert not any(w.label == '추가로 표시할 입력항목' for w in app.multiselect)
    assert '회계 처리 입력 — 거래원가·전기 장부금액' in labels
    names = {w.label for kind in ('number_input', 'selectbox') for w in app.get(kind)}
    for nm in ('발행 거래원가(원)', '주계약 전기 장부금액(원금 100 기준, -1은 없음)',
               '상환·재매입 지급대가(원금 100 기준, -1은 없음)', '매도청구 통지 뒤 전환 대응'):
        assert nm in names, nm


def test_call_response_input_changes_the_saved_case():
    # 값을 바꾸는 항목(매도청구 통지 뒤 전환 대응)은 콜 상세 조건에서 바로 고칠 수 있어야 한다.
    app = _app(call_case())
    box = next(w for w in app.selectbox if w.label == '매도청구 통지 뒤 전환 대응')
    box.set_value(0).run()
    assert not app.exception, app.exception
    assert app.session_state.case.facts()['k_conv_resp'] == 0


def test_analysis_tools_drop_contract_scenarios():
    app = _evaluate(_app(sample()))
    tool = next(w for w in app.selectbox if w.label == '분석 도구')
    assert list(tool.options) == ['결과 요약', '상세 계산·회계 참고표']


def test_review_topics_live_in_sources_screen():
    app = _app(call_case())
    app.radio(key='_input_area').set_value('출처·평가가정').run()
    assert not app.exception, app.exception
    assert '평가자 메모 · 선택 기록' in [e.label for e in app.expander]
    text = ' '.join(m.value for m in app.markdown)
    topics = next(w for w in app.selectbox if w.label == '메모 주제').options
    assert '전환가액 조정 조항' in topics and '주당가치 역산 및 희석 반영' in topics
    assert '**전환가액 조정 조항**' not in text  # 일반 질문 목록을 자동으로 펼치지 않는다.


def test_day1_checks_sit_under_day1_result():
    app = _evaluate(_app(day1_case()))
    assert '최초 인식 차이 — 원인 점검 4항목' in [e.label for e in app.expander]
    text = ' '.join(m.value for m in app.markdown)
    assert '[최초 인식 차이] 거래가격이 공정가치가 아닐 수 있나요?' in text
