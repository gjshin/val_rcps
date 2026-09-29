"""Reproducible volatility evidence. No network calls and no silent peer drops."""
import copy
import datetime as dt
import hashlib
import json
import math
from .legacy import vol_from


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def query_spec(listed, lines, days, asof, file_sha256=''):
    peers = []
    for line in lines:
        parts = [s.strip() for s in line.replace('\t', ',').split(',')]
        if parts[0]:
            peers.append({'code': parts[0], 'name': parts[1] if len(parts) > 1 and parts[1] else parts[0]})
    result = dict(listed=bool(listed), peers=peers, days=days, asof=asof,
                  origin='file' if file_sha256 else 'yahoo', file_sha256=file_sha256)
    validate_query(result)
    return result


def validate_query(query):
    if not isinstance(query, dict) or set(query) != {'listed', 'peers', 'days', 'asof', 'origin', 'file_sha256'}:
        raise ValueError('조회 조건 기록을 확인하십시오.')
    if type(query['listed']) is not bool or type(query['days']) is not int or query['days'] < 10:
        raise ValueError('조회 일수는 10 이상의 정수여야 합니다.')
    dt.date.fromisoformat(query['asof'])
    if query['origin'] not in {'file', 'yahoo'} or not isinstance(query['file_sha256'], str):
        raise ValueError('자료 취득 경로를 확인하십시오.')
    if query['origin'] == 'file' and (len(query['file_sha256']) != 64 or any(c not in '0123456789abcdef' for c in query['file_sha256'])):
        raise ValueError('원본 파일의 SHA256이 필요합니다.')
    peers = query['peers']
    if not isinstance(peers, list) or any(not isinstance(p, dict) or set(p) != {'code', 'name'} or
            any(not isinstance(s, str) or not s.strip() for s in p.values()) for p in peers):
        raise ValueError('종목코드와 이름을 확인하십시오.')
    if len({p['code'] for p in peers}) != len(peers) or len({p['name'] for p in peers}) != len(peers):
        raise ValueError('종목코드 또는 회사명이 중복됩니다.')
    if query['origin'] == 'yahoo' and not peers:
        raise ValueError('조회할 종목을 입력하십시오.')
    if query['listed'] and query['origin'] == 'yahoo' and len(peers) != 1:
        raise ValueError('상장사 자체 주가 산출에는 한 종목을 입력하십시오.')


def aggregate(values, pick):
    values = sorted(values)
    if pick == 'mean':
        return sum(values) / len(values)
    if pick == 'min':
        return values[0]
    if pick == 'max':
        return values[-1]
    n = len(values)
    return values[n//2] if n % 2 else (values[n//2-1] + values[n//2]) / 2


def parse_price_table(text):
    """Accept one or more price columns, but never silently remove bad cells."""
    import csv
    import io
    import re
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError('종가 파일이 비어 있습니다.')
    delimiter = '\t' if '\t' in lines[0] else ';' if ';' in lines[0] else ','
    table = list(csv.reader(io.StringIO('\n'.join(lines)), delimiter=delimiter))
    headers = [s.strip().lstrip('\ufeff') for s in table[0]]
    if len(headers) < 2 or any(not s for s in headers[1:]) or len(set(headers[1:])) != len(headers)-1:
        raise ValueError('날짜 열과 중복 없는 종목별 머리글이 필요합니다.')
    out = [[name, []] for name in headers[1:]]
    for index, cells in enumerate(table[1:], start=2):
        if len(cells) != len(headers):
            raise ValueError(f'{index}행: 열 수가 머리글과 다릅니다.')
        try:
            parts = re.split(r'[-./]', cells[0].strip())
            day = dt.date(*map(int, parts)).isoformat()
            for target, cell in zip(out, cells[1:]):
                price = float(cell.replace(',', '').replace('원', '').strip())
                if not math.isfinite(price) or price <= 0:
                    raise ValueError('invalid price')
                target[1].append([day, price])
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f'{index}행: 날짜 또는 주가가 누락되었거나 잘못되었습니다. 해당 행을 고쳐 다시 읽으십시오.') from exc
    dates = [d for d, _ in out[0][1]]
    if len(set(dates)) != len(dates):
        raise ValueError('종가 파일에 중복 날짜가 있습니다.')
    if dates == sorted(dates, reverse=True):
        for _, prices in out:
            prices.reverse()
    elif dates != sorted(dates):
        raise ValueError('종가 파일의 날짜가 오름차순 또는 내림차순이어야 합니다.')
    return out


def _statistics(series, query, options):
    validate_query(query)
    if not isinstance(options, dict) or set(options) != {'tdays', 'drop', 'pick', 'asof', 'days'}:
        raise ValueError('연환산·이상치·종합 조건을 확인하십시오.')
    if options['asof'] != query['asof'] or options['days'] != query['days']:
        raise ValueError('조회 조건이 바뀌었습니다. 현재 조건으로 주가를 다시 받거나 파일을 다시 읽으십시오.')
    if type(options['tdays']) is not int or not 1 <= options['tdays'] <= 366 or type(options['drop']) is not bool or options['pick'] not in {'median', 'mean', 'min', 'max'}:
        raise ValueError('연 거래일수(1~366)·이상치 제거 여부·종합 방법을 확인하십시오.')
    if not isinstance(series, list) or not series:
        raise ValueError('수신한 주가가 없습니다.')
    names, stats, warnings = [], [], []
    end = dt.date.fromisoformat(query['asof'])
    for item in series:
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[0], str) or not item[0].strip():
            raise ValueError('회사명과 날짜별 주가 형식을 확인하십시오.')
        name, prices = item
        if name in names:
            raise ValueError('주가 자료의 회사명이 중복됩니다.')
        names.append(name)
        if not isinstance(prices, (list, tuple)) or len(prices) < query['days']:
            raise ValueError(f'{name}: 요청한 {query["days"]}개 관측치보다 자료가 적습니다. 기간 또는 피어 선정 근거를 검토하고 다시 받으십시오.')
        if len(prices) != query['days']:
            raise ValueError(f'{name}: 조회 일수와 관측치 수가 다릅니다. 사용할 기간을 명시하여 자료를 다시 준비하십시오.')
        prev = None
        for row in prices:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError('날짜·종가 두 열이 필요합니다.')
            day = dt.date.fromisoformat(row[0])
            price = row[1]
            if (prev and day <= prev) or day > end:
                raise ValueError(f'{name}: 중복·역순 날짜 또는 기준일 이후 주가가 있습니다.')
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise ValueError(f'{name}: 주가는 유한한 양수여야 합니다.')
            prev = day
        if (end - prev).days > 7:
            raise ValueError(f'{name}: 마지막 주가가 기준일보다 7일 넘게 오래되었습니다. 거래정지·자료 누락 여부를 확인하십시오.')
        result = vol_from(prices, options['tdays'], options['drop'])
        if not result or result['n'] - result['removed'] < 2 or not math.isfinite(result['annual']) or result['annual'] <= 0:
            raise ValueError(f'{name}: 남은 수익률 수 또는 변동성을 확인하십시오.')
        if result['removed']:
            warnings.append(f'{name}: 수익률 {result["n"]}개 중 {result["removed"]}개 제외. 실제 변동을 제거했는지 검토하십시오.')
        stats.append([name, result['annual'], result['n'], result['removed']])
    if query['origin'] == 'yahoo' and names != [p['name'] for p in query['peers']]:
        raise ValueError('요청한 피어와 수신 자료가 다릅니다. 일부 종목의 실패를 제외한 채 적용할 수 없습니다.')
    if query['listed'] and len(names) != 1:
        raise ValueError('상장사 자체 주가 산출에는 한 종목만 사용할 수 있습니다.')
    return stats, aggregate([s[1] for s in stats], options['pick']), warnings


def make_pack(series, query, *, tdays, drop, pick, source, retrieved_at=None):
    options = dict(tdays=tdays, drop=drop, pick=pick, asof=query['asof'], days=query['days'])
    stats, sigma, warnings = _statistics(series, query, options)
    return dict(kind='vol_pack', version=2, listed=query['listed'], sigma=sigma, opt=options,
                query=copy.deepcopy(query), query_sha256=digest(query), source=source,
                made_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                retrieved_at=retrieved_at or dt.datetime.now(dt.timezone.utc).isoformat(),
                series=copy.deepcopy(series), data_sha256=digest(series), per_company=stats, warnings=warnings)


def validate_pack(pack, *, asof=None, current_query=None):
    if not isinstance(pack, dict) or pack.get('kind') != 'vol_pack' or pack.get('version') != 2:
        raise ValueError('조회 조건·원본 주가를 포함한 새 변동성 패키지가 필요합니다. 이전 패키지는 다시 산출하십시오.')
    required = {'kind', 'version', 'listed', 'sigma', 'opt', 'query', 'query_sha256', 'source',
                'made_at', 'retrieved_at', 'series', 'data_sha256', 'per_company', 'warnings'}
    if set(pack) != required:
        raise ValueError('변동성 패키지의 필수 기록을 확인하십시오.')
    if any(not isinstance(pack[k], str) or not pack[k].strip() for k in ('source', 'made_at', 'retrieved_at')):
        raise ValueError('자료 출처·취득시각을 기록하십시오.')
    stats, sigma, warnings = _statistics(pack['series'], pack['query'], pack['opt'])
    if pack['data_sha256'] != digest(pack['series']) or pack['query_sha256'] != digest(pack['query']):
        raise ValueError('주가 또는 조회 조건의 식별값이 일치하지 않습니다.')
    if isinstance(pack['sigma'], bool) or not isinstance(pack['sigma'], (int, float)) or not math.isfinite(pack['sigma']) or not math.isclose(sigma, pack['sigma'], rel_tol=1e-12, abs_tol=1e-14) or stats != pack['per_company'] or warnings != pack['warnings'] or pack['listed'] != pack['query']['listed']:
        raise ValueError('저장된 변동성이 원본 주가의 재산출값과 다릅니다.')
    if asof is not None and asof != pack['opt']['asof']:
        raise ValueError('변동성 산출 기준일과 평가기준일이 다릅니다. 기준일을 맞춘 후 적용하십시오.')
    if current_query is not None and digest(current_query) != pack['query_sha256']:
        raise ValueError('조회 조건이 바뀌었습니다. 자료를 다시 받으십시오.')
    return True
