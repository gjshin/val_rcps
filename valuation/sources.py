"""근거 — 판단 항목별 기준서 문단·질의회신·한공회 실무사례 쪽.

배포본은 **출처(자료명·문단·쪽)만** 보인다. 원문(기준서 본문·책 발췌)은 저작권·이용 권한이
확인되지 않았으므로 조서에 싣지 않고, 화면에서도 내부 사용본(환경변수
VAL_INTERNAL_SOURCES=1)에서만 펼친다. 원문 파일이 없어도 출처 표기는 동작한다.
- 기준서·질의회신: 회계위키 수록 본문 대조(valuation/refdata/kifrs.json) — 내부용
- 실무사례: 한공회 Series 11 해당 쪽 발췌(valuation/refdata/book_pages.json) — 내부용
"""
import json
import os
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).with_name('refdata')
BOOK = '한국공인회계사회 『K-IFRS 실무사례와 해설 Series 11 — 복합금융상품』(2023)'


def internal():
    """원문을 펼쳐도 되는 내부 사용본인가 (VAL_INTERNAL_SOURCES=1). 배포본은 출처만."""
    return os.environ.get('VAL_INTERNAL_SOURCES', '') == '1'

# 판단 항목 → 근거 키. 'book:26-32' 는 인쇄 쪽 범위.
REFS = {
    'split_put': ['1109:B4.3.5', '1109:4.3.3', '1109:B4.3.4', 'QNA:SSI-202412035', 'book:26-32'],
    # 복수 내재파생의 분리 정책(접근법 1·2)과 최초 판단 이어 적용
    'emb_policy': ['1109:B4.3.4', 'book:30-32'],
    'split_carry': ['1109:B4.3.11', 'book:32'],
    'split_call': ['1109:B4.3.5', '1109:4.3.3', 'book:145-146'],
    'lost_interest': ['1109:B4.3.5'],
    'fvpl_whole': ['1109:4.3.5', '1109:4.3.3'],
    'third_party_call': ['1109:4.3.1', 'QNA:2022-I-KQA006', 'QNA:SSI-38486', 'book:33-34', 'book:158-159'],
    'call_method': ['book:145-146', 'book:150-151', 'book:158-159'],
    'priority': ['1109:B4.3.4', 'book:26-32'],
    'put_exercise': ['book:87', 'book:89'],
    'bdt': ['book:87', 'book:89'],
    'classification': ['1032:11', '1032:15', '1032:16', '1032:22', '1032:28', '1032:29', 'book:20'],
    'classification_kgaap': ['KGAAP15:15.18', 'KGAAP15:15.20', 'QNA:GKQA09-024'],
    'residual': ['1032:31', '1032:32', '1032:AG31', 'book:162', 'KGAAP15:15.20'],
    'redemption_constraint': ['1032:AG25', 'book:91-92'],
    'dividends': ['1032:AG25', 'book:180-183'],
    'refixing': ['1032:16', '1032:22'],
    'dilution_backsolve': ['book:98', 'book:99-103'],
    'holder': ['1109:4.1.4', 'book:45-46', 'book:166', 'QNA:SSI-38486'],
    'embedded': ['1109:4.3.3', '1109:B4.3.4', 'book:26-32'],
    'fair_value_inputs': ['1113:61', '1113:64', '1113:72', '1113:89'],
    'method_change': ['book:150-151'],
    'day1': ['1109:5.1.1A', '1109:B5.1.1', '1109:B5.1.2A', 'QNA:2019-I-KQA018', '1113:58', '1113:59', '1113:60',
             '1113:64', '1113:B4',
             'book:34-36', 'book:99-103', 'book:208-211'],
    'day1_kgaap': ['KGAAP6:6.12', 'KGAAP6:6.13'],
    'rcps_equity': ['KGAAP15:15.18', 'KGAAP15:15.20', 'QNA:GKQA09-024', 'book:180-183'],
}


@lru_cache(maxsize=1)
def _paragraphs():
    data = json.loads((DIR / 'kifrs.json').read_text(encoding='utf-8'))
    return {row['key']: row for row in data['paragraphs']}, data


@lru_cache(maxsize=1)
def _book():
    data = json.loads((DIR / 'book_pages.json').read_text(encoding='utf-8'))
    return {row['printed_page']: row for row in data['pages']}, data


def book_title():
    return _book()[1]['title']


def lookup(key):
    """근거 키 하나 → [{kind, title, location, text, url}] (책 범위는 쪽마다 하나)."""
    if key.startswith('book:'):
        span = key[5:]
        first, last = (int(x) for x in span.split('-')) if '-' in span else (int(span), int(span))
        pages, meta = _book()
        out = []
        for page in range(first, last + 1):
            row = pages.get(page)
            if row:
                out.append(dict(kind='한공회 실무사례', title=row['section'],
                                location=f"책 {page}쪽 (PDF {row['pdf_page']}쪽)", text=row['text'],
                                url='', source=meta['title']))
        if not out:
            raise KeyError(key)
        return out
    rows, meta = _paragraphs()
    row = rows[key]
    kind = '질의회신' if row['std'] == 'QNA' else ('일반기업회계기준' if row['std'].startswith('KGAAP') else 'K-IFRS')
    return [dict(kind=kind, title=row['title'], location=row['paragraph'], text=row['text'],
                 url=row['url'], source=meta['source'])]


def label(key):
    """짧은 표기. 기준서는 「1109 B4.3.5」(문단), 책은 「실무사례 26-32쪽」 — 둘이 섞여 보이지 않게."""
    if key.startswith('book:'):
        return f"실무사례 {key[5:]}쪽"
    std, para = key.split(':', 1)
    if std == 'QNA':
        return f"질의회신 {para}"
    if std.startswith('KGAAP'):
        return f"일반기준 {para}"
    return f"{std} {para}"


def citation(key):
    """출처 한 줄 — 자료명과 문단·쪽을 풀어 쓴다. 원문 파일 없이 만든다."""
    if key.startswith('book:'):
        return f"{BOOK} {key[5:].replace('-', '~')}쪽"
    std, para = key.split(':', 1)
    if std == 'QNA':
        return f"회계기준원·금융감독원 질의회신 {para}"
    if std.startswith('KGAAP'):
        return f"일반기업회계기준 제{std[5:]}장 문단 {para}"
    return f"K-IFRS 제{std}호 문단 {para}"


def refs(topic):
    """판단 항목의 근거 키 목록."""
    return list(REFS.get(topic, []))


def cite(topic):
    """조서·화면 한 줄용 근거 표기: '1109 B4.3.5 · 실무사례 26-32쪽'."""
    return ' · '.join(label(k) for k in refs(topic))


def texts(topic):
    out = []
    for key in refs(topic):
        out.extend(lookup(key))
    return out
