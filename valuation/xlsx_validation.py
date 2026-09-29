"""Workbook integrity and optional full-grid spreadsheet-engine verification."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET


def inspect_workbook(data):
    errors, longest = [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        bad = archive.testzip()
        if bad:
            errors.append(f'ZIP 손상: {bad}')
        for name in archive.namelist():
            if name.endswith(('.xml', '.rels')):
                with archive.open(name) as stream:
                    for _, node in ET.iterparse(stream, events=('end',)):
                        if node.tag == '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f':
                            size = len(node.text or '')
                            longest = max(longest, size)
                            if size > 8192:
                                errors.append(f'{name}: 수식 {size:,}자 / 한도 8,192자')
                            if '#REF!' in (node.text or ''):
                                errors.append(f'{name}: 잘못된 시트·셀 참조')
                        node.clear()
    return {'errors': errors, 'max_formula_length': longest}


def recalculate_and_compare(data, executable=None):
    """Recalculate the ACTUAL export, without changing inputs or step count."""
    import openpyxl
    exe = executable or os.environ.get('VALUATION_SOFFICE') or shutil.which('libreoffice') or shutil.which('soffice')
    if not exe:
        raise ValueError('수식 재계산 검사에는 LibreOffice가 필요합니다. 설치 후 실행하거나 VALUATION_SOFFICE 경로를 지정하십시오.')
    check = inspect_workbook(data)
    if check['errors']:
        raise ValueError('; '.join(check['errors']))
    with tempfile.TemporaryDirectory(prefix='valuation-xlsx-') as folder:
        base = Path(folder); source = base/'source.xlsx'; source.write_bytes(data)
        out = base/'recalculated'; out.mkdir()
        result = subprocess.run([exe, '-env:UserInstallation='+ (base/'profile').as_uri(),
            '--headless', '--convert-to', 'xlsx', '--outdir', str(out), str(source)],
            capture_output=True, text=True, timeout=1800)
        target = out/source.name
        if result.returncode or not target.exists():
            raise ValueError('엑셀 재계산 실패: '+(result.stderr or result.stdout)[-2000:])
        wb = openpyxl.load_workbook(target, data_only=True, read_only=True)
        if '수식대사' not in wb:
            wb.close()
            raise ValueError('수식대사 시트가 없는 출력입니다. 상세 수식 조서로 검사하십시오.')
        rows = list(wb['수식대사'].values)[1:]
        errors = [f'{r[0]}: 앱 {r[1]} / 수식 {r[2]}' for r in rows if r[4] != '일치']
        for sheet in wb:
            for row in sheet:
                for cell in row:
                    if cell.data_type == 'e':
                        errors.append(f'{sheet.title}!{cell.coordinate}: {cell.value}')
        wb.close()
        if errors:
            raise ValueError('수식 대사 불일치: '+'; '.join(errors[:20]))
        return {**check, 'comparison': rows, 'engine': 'LibreOffice', 'same_grid': True}
