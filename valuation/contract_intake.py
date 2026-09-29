"""Review notes supplied with a case. No upload, OCR, AI or network client."""
import re


def validate_review(review):
    if not review:
        return
    if not isinstance(review, dict) or set(review) != {'documents', 'findings', 'open_items'}:
        raise ValueError('계약 검토안에는 documents, findings, open_items가 필요합니다.')
    if any(not isinstance(review[k], list) for k in review):
        raise ValueError('계약 검토안의 각 항목은 목록이어야 합니다.')
    for doc in review['documents']:
        if not isinstance(doc, dict) or set(doc) != {'name', 'sha256'} or not isinstance(doc['name'], str) or not doc['name'].strip() or not isinstance(doc['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', doc['sha256']):
            raise ValueError('참고자료의 파일명과 SHA-256 식별값을 확인하십시오.')
    from .case import FIELDS
    from .evidence import TOPICS
    topics = {t['id'] for t in TOPICS}
    names = {d['name'] for d in review['documents']}
    for row in review['findings']:
        expected = {'document', 'clause', 'quote', 'fields', 'topics', 'interpretation', 'action'}
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError('조항 검토안의 필수 항목을 확인하십시오.')
        if any(not isinstance(row[k], str) for k in expected - {'fields', 'topics'}):
            raise ValueError('조항 검토안의 설명은 문자열이어야 합니다.')
        if row['document'] not in names or not row['clause'].strip() or not row['quote'].strip():
            raise ValueError('검토안에는 참고자료, 조항·쪽수 및 원문 발췌가 필요합니다.')
        if not isinstance(row['fields'], list) or any(not isinstance(k, str) or k not in FIELDS for k in row['fields']):
            raise ValueError('검토안의 연결 입력항목을 확인하십시오.')
        if not isinstance(row['topics'], list) or any(not isinstance(k, str) or k not in topics for k in row['topics']):
            raise ValueError('검토안의 연결 검토주제를 확인하십시오.')
    if any(not isinstance(x, str) or not x.strip() for x in review['open_items']):
        raise ValueError('추가 확인사항에는 내용을 입력하십시오.')


def findings_for(case, topic):
    return [r for r in case.contract_review.get('findings', []) if topic in r['topics']]
