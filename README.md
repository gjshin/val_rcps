# val_rcps — 전환사채 · 상환전환우선주 평가

`val_derivative` 의 전환사채(CB) 평가 앱을 옮겨 와, 상품 스위치로 **상환전환우선주(RCPS)** 까지
평가하도록 넓힌다. 격자 · 이자율 · 변동성 · 조서 기계는 하나를 같이 쓴다.

    streamlit run app.py

시험은 `tests/` 에 있다. 조서를 고쳤으면 넷을 모두 돌린다.

    python3 tests/배선대조.py
    python3 tests/값조서대조.py
    python3 tests/리포트대조.py
    python3 tests/조서대조.py
