import sys, os, json, base64, urllib.request, subprocess, re
sys.stdout.reconfigure(encoding='utf-8')

TOKEN = os.environ.get('GH_TOKEN', '')
if not TOKEN:
    _tf = r'C:\Users\309se\AppData\Local\Temp\.gh_token'
    try:
        with open(_tf, 'rb') as _f: TOKEN = _f.read().decode('utf-8-sig').strip()
    except: pass
GH = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/vnd.github+json',
      'User-Agent': 'fix', 'Content-Type': 'application/json'}

import csv, io, urllib.parse
from collections import defaultdict

SHEET_ID = '1b3BVT4PyEl8v78NH2CIRyRQFV6Rp1ntGozfqBOssTPA'
DATA_LOCAL = os.environ.get('DATA_JSON_PATH', r'C:\Users\309se\OneDrive\Desktop\클로드 폴더\보고(주간)\dashboard\data.json')

def fetch_sheet(name):
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=' + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    r = urllib.request.urlopen(req, timeout=15)
    return list(csv.reader(io.StringIO(r.read().decode('utf-8'))))

def pv(v):
    v = str(v).strip().replace(',','')
    if not v or v=='-': return 0
    try: return int(float(v))
    except: return 0

from datetime import date as Date, timedelta

def parse_date(s):
    """YYYY-MM-DD 또는 YYYY.MM.DD 형식 모두 처리"""
    s = str(s).strip()[:10]
    if len(s) < 10: return None
    s = s.replace('.', '-')
    try: return Date.fromisoformat(s)
    except: return None

def get_week_structure():
    """목~수 기준 주차 구조 생성.
    로컬 data.json의 기존 레이블+날짜를 기준으로 하고,
    새 주차는 자동 생성 (목요일 시작월 기준)."""
    with open(DATA_LOCAL, encoding='utf-8') as f:
        local = json.load(f)

    # 로컬 data.json에서 기존 주차 파싱
    ref_weeks = local.get('weeks', [])   # ['4월1주','4월2주',...]
    ref_dates = local.get('week_dates', [])  # ['3/26~4/1','4/2~4/8',...]

    def parse_dr(ds, yr=2026):
        parts = ds.split('~')
        def pd(s):
            mo,dy = s.strip().split('/')
            return Date(yr, int(mo), int(dy))
        s=pd(parts[0]); e=pd(parts[1])
        if e<s: e=Date(e.year+1,e.month,e.day)
        return s,e

    # 기존 주차 목록 (레이블→날짜 범위)
    known = {}
    for lbl,dr in zip(ref_weeks, ref_dates):
        s,e = parse_dr(dr)
        known[lbl] = {'label':lbl,'dates':dr,'start':s,'end':e,
                      'month': lbl.replace('주','')[:-1]+'월' if lbl[-1]=='주' else lbl[:2],
                      'week': int(re.search(r'\d+(?=주)',lbl).group())}

    # 주간_백데이타 최신 날짜 확인 → 기존 마지막 주 이후 새 주차 추가
    rows = fetch_sheet('주간_백데이타')
    all_dates = []
    for row in rows[2:]:
        ds=row[1].strip() if len(row)>1 else ''
        dt=parse_date(ds)
        if dt: all_dates.append(dt)

    if not all_dates: all_dates = [Date(2026,6,3)]
    max_d = max(all_dates)

    # 목요일 기준 주 시작일 계산
    def week_start(d):
        return d - timedelta(days=(d.weekday()-3)%7)

    # 기존 마지막 주 끝날 이후 새 주차 생성
    last_end = max(wc['end'] for wc in known.values()) if known else Date(2026,6,3)
    extra_cols = []
    # 월별 카운터 (기존 주차 기반)
    month_cnt = {}
    for lbl,wc in sorted(known.items(), key=lambda x:x[1]['start']):
        m = wc['month']
        month_cnt[m] = max(month_cnt.get(m,0), wc['week'])

    cur = last_end + timedelta(days=1)  # 다음 목요일부터
    while cur <= max_d + timedelta(days=7):
        ws = week_start(cur)
        if ws <= last_end: cur += timedelta(days=7); continue
        we = ws + timedelta(days=6)
        # 레이블 월 결정: 수요일 월 (단, 수요일이 다음달 첫날이면 목요일 월)
        lbl_month_num = we.month if we.day > 3 else ws.month
        month_kr = str(lbl_month_num)+'월'
        month_cnt[month_kr] = month_cnt.get(month_kr,0)+1
        dr = f'{ws.month}/{ws.day}~{we.month}/{we.day}'
        lbl = month_kr+str(month_cnt[month_kr])+'주'
        extra_cols.append({'label':lbl,'dates':dr,'start':ws,'end':we,
                           'month':month_kr,'week':month_cnt[month_kr]})
        cur = ws + timedelta(days=7)
        if cur > max_d + timedelta(days=7): break

    # 전체 주차 목록: 기존 + 신규
    all_cols = sorted(list(known.values())+extra_cols, key=lambda x:x['start'])
    # 최근 10주만
    all_cols = all_cols[-10:]

    issues = local.get('issues', [])
    return all_cols, issues

def build_weekly_from_txn(week_cols):
    """주간_백데이타(거래 원본)에서 주차별 집계"""
    rows = fetch_sheet('주간_백데이타')
    # 헤더 탐색
    hdr_idx=0
    for i,row in enumerate(rows[:5]):
        if any('송금일' in c for c in row): hdr_idx=i; break
    hdr=rows[hdr_idx]
    def fi(kws):
        for k in kws:
            for i,h in enumerate(hdr):
                if k in h.replace(' ',''): return i
        return -1
    dc=fi(['송금일']); cc=fi(['계정과목']); ic=fi(['입금']); oc=fi(['출금'])
    if dc<0: dc=1
    if cc<0: cc=3
    if ic<0: ic=6
    if oc<0: oc=7

    def map_inc(cat):
        c=cat.lower().replace(' ','').replace('(','').replace(')','')
        if '공구' in c or '공동구매' in c: return '공동구매 매출'
        if 'pb' in c: return 'PB 매출'
        if '화장품' in c: return '화장품 매출'
        if '업체' in c: return '업체 매출'
        if 'mcn' in c: return 'MCN 매출'
        if '이자' in c: return '이자수익'
        if '매출' in c: return '기타매출'
        return None

    def map_exp(cat):
        c=cat.lower().replace(' ','').replace('(','').replace(')','')
        if '판매수수료' in c and '-' not in c: return '판매수수료'
        if '상품매입' in c or ('상품' in c and '매입' in c): return '상품매입'
        if '인건비' in c or ('급여' in c and '퇴직' not in c): return '인건비'
        if '퇴직' in c: return '퇴직급여'
        if '광고' in c: return '광고선전비'
        if '지급수수료' in c: return '지급수수료'
        if '법인카드' in c: return '법인카드'
        if '포장' in c: return '포장비'
        if '임차보증금' in c: return '임차보증금'
        if '임차' in c or '렌트' in c: return '임차료'
        if '세금' in c or '공과' in c: return '세금'
        if '자산' in c or '인테리어' in c: return '자산(인테리어)'
        # 기타 세부항목
        if '개인경비' in c: return '기타_개인경비'
        if '복리후생' in c: return '기타_복리후생비'
        if '통신' in c: return '기타_통신비'
        if '여비' in c or '교통비' in c: return '기타_여비교통비'
        if '전력' in c: return '기타_전력비'
        if '수도' in c or '광열' in c: return '기타_수도광열비'
        if '이자비용' in c: return '기타_이자비용'
        if '소모품' in c: return '기타_소모품비'
        if '보험료' in c: return '기타_보험료'
        if '경조' in c: return '기타_경조금'
        if '운반' in c: return '기타_운반비'
        if '상여' in c: return '기타_상여금'
        if '법인세' in c: return '기타_법인세'
        if '잡손실' in c: return '기타_잡손실'
        if '선급금' in c: return '기타_선급금'
        if '예수금' in c: return '기타_예수금'
        return '기타비용'

    N = len(week_cols)
    inc_raw = defaultdict(lambda:[0.0]*N)
    exp_raw = defaultdict(lambda:[0.0]*N)

    for row in rows[hdr_idx+1:]:
        if len(row)<=max(dc,cc,ic,oc): continue
        dt=parse_date(row[dc].strip())
        if not dt: continue
        # 해당 주차 찾기
        wi=None
        for i,wc in enumerate(week_cols):
            if wc['start']<=dt<=wc['end']: wi=i; break
        if wi is None: continue
        cat=row[cc].strip()
        ia=pv(row[ic]); oa=pv(row[oc])
        if ia!=0: inc_raw[map_inc(cat) or '기타'][wi]+=ia
        if oa!=0: exp_raw[map_exp(cat)][wi]+=oa

    # 백만원 변환
    income={k:[round(v/1e6) for v in vals] for k,vals in inc_raw.items() if any(v!=0 for v in vals)}
    expense={k:[round(v/1e6) for v in vals] for k,vals in exp_raw.items() if any(v!=0 for v in vals)}
    return income, expense

# ══ 구글시트에서 직접 파싱 ════════════════════════════════
print('구글시트 로딩 중...')

# ① 주간현황(자금) ← 주간_백데이터 (요약 시트)
def parse_weekly_summary():
    rows = fetch_sheet('주간_백데이터')
    hdr = rows[0]
    week_cols=[]; cur_month=None
    for c,cell in enumerate(hdr):
        cell=cell.strip()
        m=re.match(r'(\d+)월\s*(\d+)주차\(([^)]+)\)',cell)
        if m:
            cur_month=m.group(1)+'월'
            week_cols.append({'col':c,'month':cur_month,'week':int(m.group(2)),'dates':m.group(3),'label':cur_month+m.group(2)+'주'})
            continue
        m2=re.match(r'(\d+)주차\(([^)]+)\)',cell)
        if m2 and cur_month:
            week_cols.append({'col':c,'month':cur_month,'week':int(m2.group(1)),'dates':m2.group(2),'label':cur_month+m2.group(1)+'주'})
            continue
        if '합계' in cell: break

    income,expense,issues={},{},[]
    in_inc=False; in_exp=False; in_iss=False
    for row in rows[1:]:
        lab=row[2].strip() if len(row)>2 else ''
        if not lab: continue
        if lab=='입금': in_inc=True;in_exp=False;continue
        if lab=='출금': in_inc=False;in_exp=True;continue
        if lab=='가감': in_inc=False;in_exp=False;continue
        if '이슈' in (row[1] if len(row)>1 else ''): in_iss=True;continue
        if in_iss:
            tag=row[1].strip() if len(row)>1 else ''; ttl=row[2].strip() if len(row)>2 else ''
            if tag and ttl and ttl not in ['내용','구분','']:
                lv=row[3].strip() if len(row)>3 else ''; desc=row[4].strip() if len(row)>4 else ''
                issues.append({'tag':tag,'title':ttl,'level':'high' if '상' in lv else 'mid' if '중' in lv else 'high','desc':desc})
            continue
        vals=[pv(row[wc['col']]) if wc['col']<len(row) else 0 for wc in week_cols]
        if all(v==0 for v in vals): continue
        if in_inc: income[lab]=vals
        elif in_exp: expense[lab]=vals

    weeks=[wc['label'] for wc in week_cols]
    week_dates=[wc['dates'] for wc in week_cols]
    month_groups={}
    for i,wc in enumerate(week_cols):
        month_groups.setdefault(wc['month'],[]).append(i)
    return {'weeks':weeks,'income':income,'expense':expense,
            'week_dates':week_dates,'month_groups':month_groups,'issues':issues}

# ① 기존 주차: 로컬 data.json의 검증된 값 사용
# ② 새 주차(로컬에 없는 주차): 주간_백데이타 집계
week_cols_info, issues_raw = get_week_structure()

with open(DATA_LOCAL, encoding='utf-8') as f:
    local_bak = json.load(f)

local_weeks = local_bak.get('weeks', [])
local_income = local_bak.get('income', {})
local_expense = local_bak.get('expense', {})

N = len(week_cols_info)

# 전체 주차를 raw 트랜잭션에서 재집계 (최신 map_exp 카테고리 반영)
inc_all, exp_all = build_weekly_from_txn(week_cols_info)

# 입금: raw 결과 우선, 없으면 data.json 백업값 사용
income_w = {}
for k in set(list(inc_all.keys()) + list(local_income.keys())):
    if k in inc_all:
        income_w[k] = inc_all[k]
    else:
        li_vals = local_income[k]
        income_w[k] = [0]*N
        for i, wc in enumerate(week_cols_info):
            if wc['label'] in local_weeks:
                li = local_weeks.index(wc['label'])
                if li < len(li_vals): income_w[k][i] = li_vals[li]

# 출금: 항상 raw 재집계값 사용 (새 기타_* 카테고리 반영)
expense_w = dict(exp_all)

new_week_idxs = [i for i,wc in enumerate(week_cols_info) if wc['label'] not in local_weeks]

weeks       = [wc['label']  for wc in week_cols_info]
week_dates  = [wc['dates']  for wc in week_cols_info]
month_groups = {}
for i,wc in enumerate(week_cols_info):
    month_groups.setdefault(wc['month'],[]).append(i)

weekly = {'weeks':weeks,'income':income_w,'expense':expense_w,
          'week_dates':week_dates,'month_groups':month_groups,
          'issues':local_bak.get('issues',[])}

print(f'주간 집계완료: {len(weeks)}주 (기존={len(local_weeks)}주, 신규={len(new_week_idxs)}주)')
for i,w in enumerate(weeks):
    ti=sum(v[i] for v in income_w.values() if i<len(v))
    te=sum(v[i] for v in expense_w.values() if i<len(v))
    src='새주차' if w not in local_weeks else '기존'
    print(f'  {w}({week_dates[i]}): 입금={ti}, 출금={te} [{src}]')

# 월간_백데이타 파싱 → monthly_cashflow (입출금_백데이타 형식, 확정된 월만 포함)
def fetch_monthly_from_txn(sheet_name):
    rows = fetch_sheet(sheet_name)
    # 헤더 찾기 (송금일/입금 있는 행)
    hdr_idx = 0
    for i, row in enumerate(rows[:5]):
        if any('송금일' in c or '입금' in c for c in row):
            hdr_idx = i; break
    hdr = rows[hdr_idx]
    def fi(kws):
        for k in kws:
            for i,h in enumerate(hdr):
                if k in h.replace(' ',''): return i
        return -1
    dc=fi(['송금일','날짜','일자']); cc=fi(['계정과목','과목'])
    ic=fi(['입금']); oc=fi(['출금'])
    if dc<0: dc=1
    if cc<0: cc=3
    if ic<0: ic=6
    if oc<0: oc=7

    def pa(v):
        v=str(v).strip().replace(',','')
        if not v or v=='-': return 0
        try: return float(v)
        except: return 0

    def map_inc(cat):
        c=cat.lower().replace(' ','').replace('(','').replace(')','')
        if '공구' in c or '공동구매' in c: return '공동구매 매출'
        if 'pb' in c: return 'PB 매출'
        if '화장품' in c: return '화장품 매출'
        if '업체' in c: return '업체 매출'
        if 'mcn' in c: return 'MCN 매출'
        if '이자' in c: return '이자수익'
        if '매출' in c: return '기타매출'
        return None

    def map_exp(cat):
        c=cat.lower().replace(' ','').replace('(','').replace(')','')
        if '판매수수료' in c and '-' not in c: return '판매수수료'
        if '상품매입' in c or ('상품' in c and '매입' in c): return '상품매입'
        if '인건비' in c or ('급여' in c and '퇴직' not in c): return '인건비'
        if '퇴직' in c: return '퇴직급여'
        if '광고' in c: return '광고선전비'
        if '지급수수료' in c: return '지급수수료'
        if '법인카드' in c: return '법인카드'
        if '포장' in c: return '포장비'
        if '임차보증금' in c: return '임차보증금'
        if '임차' in c or '렌트' in c: return '임차료'
        if '세금' in c or '공과' in c: return '세금'
        if '자산' in c or '인테리어' in c: return '자산(인테리어)'
        if '개인경비' in c: return '기타_개인경비'
        if '복리후생' in c: return '기타_복리후생비'
        if '통신' in c: return '기타_통신비'
        if '여비' in c or '교통비' in c: return '기타_여비교통비'
        if '전력' in c: return '기타_전력비'
        if '수도' in c or '광열' in c: return '기타_수도광열비'
        if '이자비용' in c: return '기타_이자비용'
        if '소모품' in c: return '기타_소모품비'
        if '보험료' in c: return '기타_보험료'
        if '경조' in c: return '기타_경조금'
        if '운반' in c: return '기타_운반비'
        if '상여' in c: return '기타_상여금'
        if '법인세' in c: return '기타_법인세'
        if '잡손실' in c: return '기타_잡손실'
        if '선급금' in c: return '기타_선급금'
        if '예수금' in c: return '기타_예수금'
        return '기타비용'

    bm = defaultdict(lambda: {'income':defaultdict(float),'expense':defaultdict(float)})
    for row in rows[hdr_idx+1:]:
        if len(row)<=max(dc,cc,ic,oc): continue
        dt=parse_date(row[dc].strip())
        if not dt: continue
        mkey=str(dt)[:7]
        cat=row[cc].strip()
        ia=pa(row[ic]); oa=pa(row[oc])
        if ia!=0: bm[mkey]['income'][map_inc(cat) or '기타']+=ia
        if oa!=0: bm[mkey]['expense'][map_exp(cat)]+=oa

    months=sorted(bm.keys())
    if not months: return None
    in_cats=sorted(set(k for m in months for k in bm[m]['income']))
    ex_cats=sorted(set(k for m in months for k in bm[m]['expense']))
    # 원 단위로 저장 (JS에서 합산 후 백만원 변환 → 정확한 총합)
    return {
        'months': months,
        'income':  {k:[int(bm[m]['income'].get(k,0)) for m in months] for k in in_cats},
        'expense': {k:[int(bm[m]['expense'].get(k,0)) for m in months] for k in ex_cats}
    }

mcf = fetch_monthly_from_txn('월간_백데이타')
print('월간_백데이타 집계:')
if mcf:
    for i,m in enumerate(mcf['months']):
        ti=sum(v[i] for v in mcf['income'].values())
        te=sum(v[i] for v in mcf['expense'].values())
        print(f'  {m}: 입금={ti:,}백만, 출금={te:,}백만')
else:
    print('  데이터 없음')

# 월간손익 시트 직접 파싱 (2025+2026 전체)
def fetch_pl_sheet():
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=' + urllib.parse.quote('월간 손익')
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    r = urllib.request.urlopen(req, timeout=10)
    rows = list(csv.reader(io.StringIO(r.read().decode('utf-8'))))
    hdr = rows[0]

    # 연도별 (레이블 컬럼, 데이터 컬럼 목록) 찾기
    # 헤더에서 "YYYY년MM월" 패턴 컬럼 탐색 → 각 연도의 레이블 컬럼 = 해당 그룹 직전 '과 목' 셀
    sections = {}  # year → {'label_col': c, 'month_cols': [(c,'YYYY-MM'),...]}
    cur_label_col = 0
    for c, cell in enumerate(hdr):
        cell = cell.strip()
        if cell in ('과 목', '과목'): cur_label_col = c
        m = re.match(r'(\d{4})년\s*(\d{1,2})월', cell)
        if m:
            y, mo = m.group(1), int(m.group(2))
            if y not in sections:
                sections[y] = {'label_col': cur_label_col, 'month_cols': []}
            sections[y]['month_cols'].append((c, f'{y}-{mo:02d}'))

    if not sections: return None

    KEY_MAP = {
        'Ⅰ. 매출액':'매출액','Ⅱ. 매출원가':'매출원가',
        'Ⅲ. 매출총이익':'매출총이익','Ⅳ. 판매비와 관리비':'판매비와관리비',
        'Ⅴ. 영업이익':'영업이익'
    }
    SUB_MAP = {
        '직원급여':' ├ 직원급여','퇴직급여':' ├ 퇴직급여',
        '복리후생비':' ├ 복리후생비','판매수수료':' ├ 판매수수료',
        '포장비':' ├ 포장비','지급수수료':' ├ 지급수수료',
        '광고선전비':' ├ 광고선전비','지급임차료':' ├ 지급임차료',
        '세금과공과금':' ├ 세금과공과금','소모품비':' ├ 소모품비',
        '경상연구개발비':' ├ 경상연구개발비'
    }
    SUB_ORDER = [' ├ 직원급여',' ├ 퇴직급여',' ├ 복리후생비',' ├ 판매수수료',
                 ' ├ 포장비',' ├ 지급수수료',' ├ 광고선전비',' ├ 지급임차료',
                 ' ├ 세금과공과금',' ├ 소모품비',' ├ 경상연구개발비']

    def pv(v):
        v=str(v).strip().replace(',','')
        if not v or v=='-': return 0
        if v.startswith('(') and v.endswith(')'): v='-'+v[1:-1]
        try: return int(float(v))
        except: return 0

    # 연도별 각각 파싱 → 행 위치가 달라도 정확히 매핑
    raw_by_year = {}
    for yr, sec in sections.items():
        lc = sec['label_col']
        mcs = sec['month_cols']
        N = len(mcs)
        raw = {}
        for row in rows[1:]:
            lb = row[lc].strip() if lc < len(row) else ''
            if not lb: continue
            vals = [pv(row[mc[0]]) if mc[0]<len(row) else 0 for mc in mcs]
            if KEY_MAP.get(lb): raw[KEY_MAP[lb]] = vals
            elif lb in SUB_MAP: raw[SUB_MAP[lb]] = vals
        # 기타판관비
        if '판매비와관리비' in raw:
            sg = raw['판매비와관리비']
            known = [sum(raw.get(k,[0]*N)[i] for k in SUB_ORDER) for i in range(N)]
            raw[' └ 기타판관비'] = [sg[i]-known[i] for i in range(N)]
        raw_by_year[yr] = raw

    # 전체 월 순서 합치기
    all_months = []
    for yr in sorted(sections.keys()):
        all_months += [mc[1] for mc in sections[yr]['month_cols']]
    N_total = len(all_months)

    # 항목별 배열 합산
    ORDER = ['매출액','매출원가','매출총이익','판매비와관리비']+SUB_ORDER+[' └ 기타판관비','영업이익']
    items = {}
    for k in ORDER:
        combined = []
        for yr in sorted(sections.keys()):
            n = len(sections[yr]['month_cols'])
            combined += raw_by_year[yr].get(k, [0]*n)
        if any(v != 0 for v in combined):
            items[k] = [round(v/1e6) for v in combined]

    # 레이블
    labels=[]; prev_y=None
    for m in all_months:
        y,mo=m.split('-'); mo_i=int(mo)
        labels.append(f"'{y[2:]}.{mo_i}" if y!=prev_y else str(mo_i))
        prev_y=y

    return {'months':all_months,'labels':labels,'items':items}

pl = fetch_pl_sheet()
if pl:
    print(f'월간손익: {pl["months"][0]}~{pl["months"][-1]} ({len(pl["months"])}개월)')
else:
    with open(DATA_LOCAL, encoding='utf-8') as f:
        pl = json.load(f).get('pl')
    print('월간손익: 로컬 폴백')

with open(DATA_LOCAL, encoding='utf-8') as f:
    local = json.load(f)

data = {
    'week_label':   weekly['weeks'][-1] if weekly['weeks'] else '',
    'period':       (weekly['weeks'][0]+' ~ '+weekly['weeks'][-1]) if weekly['weeks'] else '',
    'weeks':        weekly['weeks'],
    'month_groups': weekly['month_groups'],
    'income':       weekly['income'],
    'expense':      weekly['expense'],
    'issues':       weekly['issues'],
    'unit':         '백만원',
    'pl':           pl,
    'monthly_cashflow': mcf,
    'week_dates':   weekly['week_dates'],
    'updated_at':   local.get('updated_at','')
}
print(f'데이터 빌드 완료 | weeks:{len(data["weeks"])}개')

D = json.dumps(data, ensure_ascii=False, separators=(',',':'))

# HTML 파일 경로에 JS 내용 작성
html_parts = []
html_parts.append("""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>파마브로스 | 주간 자금 현황</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<script>
(function(){
  var PW='pharma-bros',SK='pb_w2';
  function hide(){var e=document.getElementById('lockScreen');if(e)e.style.display='none';}
  function checkPw(){
    var inp=document.getElementById('pwInput');
    if(inp&&inp.value===PW){try{sessionStorage.setItem(SK,String(Date.now()+43200000));}catch(e){}hide();}
    else if(inp){var er=document.getElementById('pwError');if(er)er.textContent='비밀번호가 틀렸습니다.';inp.value='';inp.focus();}
  }
  function logout(){try{sessionStorage.removeItem(SK);}catch(e){}location.reload();}
  try{var t=sessionStorage.getItem(SK);if(t&&Date.now()<parseInt(t))document.addEventListener('DOMContentLoaded',hide);}catch(e){}
  window.checkPw=checkPw;window.logout=logout;
})();
function triggerUpdate(){
  var btn=document.getElementById('updBtn');
  var tok=localStorage.getItem('gh_update_tok');
  if(!tok){
    tok=prompt('GitHub 토큰을 입력하세요\\n(입력 후 브라우저에 저장됩니다)\\n\\n토큰 발급: github.com → Settings → Developer settings → Personal access tokens');
    if(!tok)return;
    localStorage.setItem('gh_update_tok',tok);
  }
  btn.disabled=true;btn.textContent='요청 중...';
  fetch('https://api.github.com/repos/309hsh/pharma-bros-weekly/actions/workflows/auto-update.yml/dispatches',{
    method:'POST',
    headers:{'Authorization':'Bearer '+tok,'Accept':'application/vnd.github+json','Content-Type':'application/json'},
    body:JSON.stringify({ref:'main'})
  }).then(function(r){
    if(r.ok||r.status===204){
      btn.textContent='✅ 요청완료';
      setTimeout(function(){btn.disabled=false;btn.textContent='🔄 업데이트';},3000);
      alert('업데이트 요청 완료!\n약 1~2분 후 페이지를 새로고침 하세요.');
    }else if(r.status===401||r.status===403){
      localStorage.removeItem('gh_update_tok');
      btn.disabled=false;btn.textContent='🔄 업데이트';
      alert('토큰이 유효하지 않습니다. 다시 시도해 주세요.');
    }else if(r.status===404){
      btn.disabled=false;btn.textContent='🔄 업데이트';
      alert('워크플로우가 아직 설정되지 않았습니다.\n관리자에게 문의하세요.');
    }else{
      btn.disabled=false;btn.textContent='🔄 업데이트';
      alert('오류: '+r.status);
    }
  }).catch(function(e){btn.disabled=false;btn.textContent='🔄 업데이트';alert('네트워크 오류: '+e);});
}
</script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0f1117;--sf:#1a1d27;--sf2:#22263a;--bd:#2e3350;--tx:#e8eaf6;--sub:#8b90b0;--gr:#4ade80;--rd:#f87171;--bl:#60a5fa;--pu:#a78bfa;--yw:#fbbf24;--ac:#6366f1}
body{background:var(--bg);color:var(--tx);font-family:'Noto Sans KR',-apple-system,sans-serif;min-height:100vh}
#lockScreen{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999}
.lb{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:40px 44px;width:340px;text-align:center}
.ll{font-size:22px;font-weight:700;margin-bottom:6px}.ll em{color:var(--ac);font-style:normal}
.ls{font-size:12px;color:var(--sub);margin-bottom:32px}
.li{width:100%;background:var(--sf2);border:1px solid var(--bd);color:var(--tx);font-size:20px;letter-spacing:6px;text-align:center;padding:12px 16px;border-radius:8px;outline:none}
.li:focus{border-color:var(--ac)}
.lbtn{width:100%;margin-top:14px;background:var(--ac);color:#fff;border:none;font-size:14px;font-weight:600;padding:12px;border-radius:8px;cursor:pointer}
.lerr{color:var(--rd);font-size:12px;margin-top:10px;min-height:18px}
.hd{background:var(--sf);border-bottom:1px solid var(--bd);padding:0 28px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-size:17px;font-weight:700}.logo em{color:var(--ac);font-style:normal}
.badge{background:var(--ac);color:#fff;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px}
.hr{display:flex;align-items:center;gap:12px}
.pb{background:var(--sf2);border:1px solid var(--bd);color:var(--sub);font-size:12px;padding:4px 12px;border-radius:6px}
.up{font-size:11px;color:var(--sub);display:flex;align-items:center;gap:5px}
.dl{width:6px;height:6px;border-radius:50%;background:var(--gr);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.bsm{background:none;border:1px solid var(--bd);color:var(--sub);font-size:11px;padding:4px 10px;border-radius:6px;cursor:pointer}
.nav{background:var(--sf);border-bottom:1px solid var(--bd);padding:0 28px;display:flex;gap:4px}
.nb{background:none;border:none;color:var(--sub);font-size:14px;font-weight:500;padding:14px 18px;cursor:pointer;border-bottom:2px solid transparent}
.nb.active{color:var(--tx);border-bottom-color:var(--ac)}
.main{padding:24px 28px;max-width:1600px;margin:0 auto}
.kg{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}
.kc{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:20px 24px;position:relative;overflow:hidden}
.kc::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.kc.g::before{background:var(--gr)}.kc.r::before{background:var(--rd)}.kc.b::before{background:var(--bl)}.kc.p::before{background:var(--pu)}
.kl{font-size:12px;color:var(--sub);margin-bottom:10px}
.kv{font-size:28px;font-weight:700;line-height:1;margin-bottom:8px}
.kc.g .kv{color:var(--gr)}.kc.r .kv{color:var(--rd)}.kc.b .kv{color:var(--bl)}.kc.p .kv{color:var(--pu)}
.ks{font-size:12px;color:var(--sub)}
.cg2{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:24px}
.cg3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px}
.cc{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:24px}
.ct{font-size:14px;font-weight:600;margin-bottom:16px}
.cs{font-size:11px;color:var(--sub)}
.tc{background:var(--sf);border:1px solid var(--bd);border-radius:12px;overflow:hidden;margin-bottom:24px}
.th{padding:18px 24px;border-bottom:1px solid var(--bd);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.ts{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:var(--sf2);color:var(--sub);font-weight:600;font-size:11px;padding:10px 14px;text-align:right;border-bottom:1px solid var(--bd);white-space:nowrap;line-height:1.5}
thead th:first-child{text-align:left}
tbody tr{border-bottom:1px solid rgba(46,51,80,.5)}
tbody tr:hover{background:var(--sf2)}
tbody tr.it td{background:rgba(74,222,128,.07);font-weight:700}
tbody tr.et td{background:rgba(248,113,113,.07);font-weight:700}
tbody tr.nr td{background:rgba(96,165,250,.1);font-weight:700}
td{padding:10px 14px;text-align:right;color:var(--sub);white-space:nowrap}
td:first-child{text-align:left;padding-left:24px;color:var(--tx)}
td.il{padding-left:40px}
.pos{color:var(--gr)!important}.neg{color:var(--rd)!important}
.ig{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:24px}
.ic{background:var(--sf);border:1px solid var(--bd);border-radius:10px;padding:16px 20px;display:flex;gap:14px}
.id{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}
.id.high{background:var(--rd);box-shadow:0 0 8px var(--rd)}.id.mid{background:var(--yw)}.id.low{background:var(--gr)}
.itag{font-size:10px;font-weight:600;color:var(--ac);background:rgba(167,139,250,.1);padding:2px 7px;border-radius:4px;display:inline-block;margin-bottom:6px}
.ititle{font-size:13px;font-weight:600;color:var(--tx);margin-bottom:4px}
.idesc{font-size:12px;color:var(--sub);line-height:1.5}
.tp{display:none}.tp.active{display:block}
.plsb{background:none;border:none;color:var(--sub);font-size:13px;font-weight:500;padding:10px 22px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.plsb.on{color:var(--tx);font-weight:600;border-bottom-color:var(--ac)}
.mmb{background:var(--sf2);border:1px solid var(--bd);color:var(--sub);font-size:12px;font-weight:500;padding:7px 16px;border-radius:20px;cursor:pointer;transition:all .15s}
.mmb.on{background:var(--ac);border-color:var(--ac);color:#fff}
.mmb:hover:not(.on){border-color:var(--ac);color:var(--tx)}
.mwc{background:var(--sf);border:1px solid var(--bd);border-radius:10px;margin-bottom:10px;overflow:hidden}
.mwh{padding:12px 18px;display:flex;align-items:center;justify-content:space-between;background:var(--sf2);border-bottom:1px solid var(--bd)}
.mta{width:100%;background:var(--sf);color:var(--tx);border:none;padding:14px 18px;font-size:13px;line-height:1.7;min-height:72px;resize:vertical;outline:none;font-family:inherit}
.mta::placeholder{color:var(--sub);opacity:.5}
.mta:focus{background:rgba(99,102,241,.04)}
.msv{font-size:11px;color:var(--gr);opacity:0;transition:opacity .4s}
.msv.show{opacity:1}
.iac{display:flex;gap:6px;margin-top:10px}
.ib{background:none;border:1px solid var(--bd);color:var(--sub);font-size:11px;padding:3px 10px;border-radius:5px;cursor:pointer;transition:all .15s}
.ib:hover{border-color:var(--ac);color:var(--tx)}
.ib.del:hover{border-color:var(--rd);color:var(--rd)}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px)}
.md{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:32px 36px;width:460px;max-width:95vw}
.mdtt{font-size:16px;font-weight:700;margin-bottom:24px}
.mdf{margin-bottom:16px}
.mdl{font-size:12px;color:var(--sub);margin-bottom:6px;font-weight:500}
.mdi{width:100%;background:var(--sf2);border:1px solid var(--bd);color:var(--tx);font-size:13px;padding:9px 12px;border-radius:8px;outline:none;font-family:inherit}
.mdi:focus{border-color:var(--ac)}
.mds{background:var(--sf2);border:1px solid var(--bd);color:var(--tx);font-size:13px;padding:9px 12px;border-radius:8px;outline:none;cursor:pointer}
.mdbr{display:flex;gap:10px;margin-top:24px;justify-content:flex-end}
.mdbtn{font-size:13px;font-weight:600;padding:9px 22px;border-radius:8px;border:none;cursor:pointer}
.mdbtn.ok{background:var(--ac);color:#fff}
.mdbtn.cc{background:var(--sf2);border:1px solid var(--bd);color:var(--sub)}
@media(max-width:1200px){.kg{grid-template-columns:repeat(2,1fr)}.cg2{grid-template-columns:1fr}.cg3{grid-template-columns:1fr 1fr}}
@media(max-width:768px){.main{padding:16px}.kg{grid-template-columns:1fr 1fr}.ig{grid-template-columns:1fr}}
</style>
</head>
<body>
<div id="lockScreen">
  <div class="lb">
    <div class="ll">파마브로스<em>.</em></div>
    <div class="ls">주간 자금 현황 대시보드</div>
    <div style="font-size:12px;color:var(--sub);margin-bottom:10px;text-align:left">🔒 비밀번호를 입력하세요</div>
    <input class="li" id="pwInput" type="password" placeholder="••••••" autocomplete="off" onkeydown="if(event.key==='Enter')checkPw()">
    <div class="lerr" id="pwError"></div>
    <button class="lbtn" onclick="checkPw()">입장</button>
    <div style="font-size:11px;color:var(--sub);margin-top:20px;opacity:.6">접근 권한이 있는 분만 이용 가능합니다</div>
  </div>
</div>
<header class="hd">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">파마브로스<em>.</em></div><span class="badge">주간 자금</span>
  </div>
  <div class="hr">
    <span class="pb" id="periodBadge">—</span>
    <span class="up"><span class="dl"></span><span id="weekLabel">로딩중</span></span>
    <span class="up" id="lastUpdated"></span>
    <button class="bsm" id="updBtn" onclick="triggerUpdate()">🔄 업데이트</button>
    <button class="bsm" onclick="logout()">🔓 로그아웃</button>
  </div>
</header>
<nav class="nav">
  <button class="nb active" onclick="showTab('weekly',this)">주간 현황 (자금)</button>
  <button class="nb" onclick="showTab('monthly',this)">월간 현황 (자금)</button>
  <button class="nb" onclick="showTab('issues',this)">이슈 관리</button>
  <button class="nb" onclick="showTab('pl',this)">월간 손익</button>
</nav>
<div class="main">
  <div id="tab-weekly" class="tp active">
    <div class="kg">
      <div class="kc g"><div class="kl">누적 총 입금</div><div class="kv" id="k_ti">—</div><div class="ks" id="k_wk">—</div></div>
      <div class="kc r"><div class="kl">누적 총 출금</div><div class="kv" id="k_te">—</div><div class="ks">주간 합산</div></div>
      <div class="kc b"><div class="kl">누적 순현금흐름</div><div class="kv" id="k_nt">—</div><div class="ks">입금 - 출금</div></div>
      <div class="kc p"><div class="kl">주간 평균 입금</div><div class="kv" id="k_av">—</div><div class="ks">주차 평균</div></div>
    </div>
    <div class="cg2">
      <div class="cc"><div class="ct">주별 입금 / 출금 추이 <span class="cs">단위: 백만원</span></div><div style="position:relative;height:260px"><canvas id="cBar"></canvas></div></div>
      <div class="cc"><div class="ct">순현금흐름 추이</div><div style="position:relative;height:260px"><canvas id="cNet"></canvas></div></div>
    </div>
    <div class="cg3">
      <div class="cc"><div class="ct">입금 구성</div><div style="position:relative;height:220px"><canvas id="cID"></canvas></div></div>
      <div class="cc"><div class="ct">출금 구성</div><div style="position:relative;height:220px"><canvas id="cED"></canvas></div></div>
      <div class="cc"><div class="ct">주차별 입금 구성</div><div style="position:relative;height:220px"><canvas id="cSB"></canvas></div></div>
    </div>
    <div class="tc"><div class="th"><span style="font-size:14px;font-weight:600">주간 자금 입출금 상세</span><span style="font-size:12px;color:var(--sub)">단위: 백만원</span></div><div class="ts"><table id="tWeekly"></table></div></div>
  </div>
  <div id="tab-monthly" class="tp">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px" id="mnKpiNet"></div>
    <div class="kg">
      <div class="kc g"><div class="kl" id="k_m1i">—</div><div class="kv" id="k_m1iv">—</div></div>
      <div class="kc r"><div class="kl" id="k_m1e">—</div><div class="kv" id="k_m1ev">—</div></div>
      <div class="kc g"><div class="kl" id="k_m2i">—</div><div class="kv" id="k_m2iv">—</div></div>
      <div class="kc r"><div class="kl" id="k_m2e">—</div><div class="kv" id="k_m2ev">—</div></div>
    </div>
    <div class="cg2">
      <div class="cc"><div class="ct">월별 입출금 비교</div><div style="position:relative;height:280px"><canvas id="cMB"></canvas></div></div>
      <div class="cc"><div class="ct">매출 항목별 월간 비교</div><div style="position:relative;height:280px"><canvas id="cMI"></canvas></div></div>
    </div>
    <div class="tc"><div class="th"><span style="font-size:14px;font-weight:600">월별 자금 비교표</span><span style="font-size:12px;color:var(--sub)">단위: 백만원</span></div><div class="ts"><table id="tMonthly"></table></div></div>
  </div>
  <div id="tab-issues" class="tp">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
      <span style="font-size:13px;font-weight:600;color:var(--sub)">📌 주요 이슈</span>
      <button class="mdbtn ok" style="padding:7px 16px;font-size:12px" onclick="openIssueModal(-1)">+ 이슈 추가</button>
    </div>
    <div class="ig" id="issueGrid"></div>
    <div id="issueModal" class="ov" style="display:none" onclick="if(event.target===this)closeIssueModal()">
      <div class="md">
        <div class="mdtt" id="modalTitle">이슈 추가</div>
        <div class="mdf"><div class="mdl">태그</div><input class="mdi" id="iTag" placeholder="예: 재무, 운영, 법무"></div>
        <div class="mdf"><div class="mdl">제목</div><input class="mdi" id="iTitle" placeholder="이슈 제목을 입력하세요"></div>
        <div class="mdf"><div class="mdl">중요도</div>
          <select class="mdi mds" id="iLevel">
            <option value="high">상 (긴급)</option>
            <option value="mid">중 (주의)</option>
            <option value="low">하 (모니터링)</option>
          </select>
        </div>
        <div class="mdf"><div class="mdl">설명</div><textarea class="mdi" id="iDesc" rows="4" placeholder="상세 내용을 입력하세요" style="resize:vertical"></textarea></div>
        <div class="mdbr">
          <button class="mdbtn cc" onclick="closeIssueModal()">취소</button>
          <button class="mdbtn ok" onclick="saveIssue()">저장</button>
        </div>
      </div>
    </div>
    <div style="margin-top:32px;border-top:1px solid var(--bd);padding-top:28px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px">
        <span style="font-size:14px;font-weight:600">📝 주차별 메모</span>
        <span style="font-size:11px;color:var(--sub)">브라우저에 자동 저장됩니다</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:18px" id="memoMonthBtns"></div>
      <div id="memoContent"></div>
    </div>
  </div>
  <div id="tab-pl" class="tp">
    <div style="display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--bd)">
      <button class="plsb on" id="psb-all" onclick="plSub('all')">📈 전체 요약</button>
      <button class="plsb" id="psb-qtr" onclick="plSub('qtr')">📅 분기별 비교</button>
      <button class="plsb" id="psb-mon" onclick="plSub('mon')">📊 월별 상세</button>
    </div>
    <div id="pl-all">
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px" id="plKpi"></div>
      <div class="cc" style="margin-bottom:24px"><div class="ct">매출액 / 영업이익 월별 추이 <span class="cs">단위: 백만원</span></div><div style="position:relative;height:320px"><canvas id="cPL"></canvas></div></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px">
        <div class="cc"><div class="ct">매출총이익률 <span class="cs">%</span></div><div style="position:relative;height:240px"><canvas id="cGP"></canvas></div></div>
        <div class="cc"><div class="ct">영업이익률 <span class="cs">%</span></div><div style="position:relative;height:240px"><canvas id="cOP"></canvas></div></div>
      </div>
      <div class="tc"><div class="th"><span style="font-size:14px;font-weight:600">전체 월별 손익 상세</span><span style="font-size:12px;color:var(--sub)">단위: 백만원</span></div><div style="overflow-x:auto"><table id="tPL" style="width:100%;border-collapse:collapse;font-size:12px"></table></div></div>
    </div>
    <div id="pl-qtr" style="display:none">
      <div style="background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:20px 24px;margin-bottom:20px">
        <div style="font-size:13px;font-weight:600;margin-bottom:16px">비교 분기 설정</div>
        <div style="display:flex;align-items:flex-end;gap:24px;flex-wrap:wrap">
          <div>
            <div style="font-size:11px;color:#60a5fa;font-weight:700;margin-bottom:6px;letter-spacing:.5px">구분 1</div>
            <select id="qSel1" onchange="renderQtrPair()" style="background:var(--bg);border:2px solid #60a5fa;color:var(--tx);font-size:13px;font-weight:600;padding:8px 16px;border-radius:8px;cursor:pointer;outline:none;min-width:150px"><option value="">분기 선택</option></select>
          </div>
          <div style="font-size:20px;color:var(--sub);padding-bottom:6px;line-height:1">vs</div>
          <div>
            <div style="font-size:11px;color:#f59e0b;font-weight:700;margin-bottom:6px;letter-spacing:.5px">구분 2</div>
            <select id="qSel2" onchange="renderQtrPair()" style="background:var(--bg);border:2px solid #f59e0b;color:var(--tx);font-size:13px;font-weight:600;padding:8px 16px;border-radius:8px;cursor:pointer;outline:none;min-width:150px"><option value="">분기 선택</option></select>
          </div>
        </div>
      </div>
      <div id="qtrCmpArea"></div>
    </div>
    <div id="pl-mon" style="display:none">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;padding:16px 20px;background:var(--sf);border:1px solid var(--bd);border-radius:10px">
        <span style="font-size:13px;font-weight:600">기준 월 선택</span>
        <select id="plSel" onchange="buildPlMonth()" style="background:var(--sf2);border:1px solid var(--bd);color:var(--tx);font-size:13px;padding:6px 14px;border-radius:8px;outline:none;cursor:pointer"></select>
        <span style="font-size:12px;color:var(--sub)">전월 · 전년 동월 자동 비교</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px" id="plMonKpi"></div>
      <div class="tc" style="margin-bottom:24px">
        <div class="th"><span style="font-size:14px;font-weight:600">항목별 전월 / 전년 동월 비교</span><span style="font-size:12px;color:var(--sub)">▲증가 ▼감소</span></div>
        <div style="overflow-x:auto" id="plMonTbl"></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="cc"><div class="ct" id="plCT1">매출액 비교</div><div style="position:relative;height:260px"><canvas id="cPC1"></canvas></div></div>
        <div class="cc"><div class="ct" id="plCT2">영업이익 비교</div><div style="position:relative;height:260px"><canvas id="cPC2"></canvas></div></div>
      </div>
    </div>
  </div>
</div>
<script>
""")

html_parts.append(f"const D = {D};\n")

html_parts.append("""const WEEKS=D.weeks||[],INCOME=D.income||{},EXPENSE=D.expense||{};
const ISSUES=D.issues||[],MG=D.month_groups||{},WD=D.week_dates||[];
const PL=D.pl;

document.getElementById('weekLabel').textContent=D.week_label||WEEKS[WEEKS.length-1]||'';
if(D.period)document.getElementById('periodBadge').textContent='2026. '+D.period;

let tI=[],tE=[],net=[],APR=[],MAY=[],ai=0,ae=0,mi=0,me=0;
const C={};
const CLR=['#4ade80','#f87171','#60a5fa','#a78bfa','#fbbf24','#fb923c','#2dd4bf','#f472b6','#818cf8'];
const GC='rgba(46,51,80,.8)';
Chart.defaults.color='#8b90b0';
Chart.defaults.font.family="'Noto Sans KR',sans-serif";
const F=n=>(!n&&n!==0)?'-':Math.round(n).toLocaleString('ko-KR');
const FN=n=>(!n&&n!==0)?'-':(n<0?'':'+')+Math.round(n).toLocaleString('ko-KR');

// ── 달력 기반 월별 집계 (시작일 기준: 4/30 시작 → 4월) ──────
var CAPR=[],CMAY=[],M1N=4,M2N=5,MCF_M1=0,MCF_M2=1,MCF_HAS_M2=false;
function initCalGroups(){
  // week_dates 시작일 기준으로 월 분류
  // "4/30~5/6" → 시작일 4월 → 4월 그룹
  // "3/26~4/1" → 시작일 3월 → 제외
  var groups={};
  WD.forEach(function(dr,wi){
    if(!dr||dr.indexOf('~')<0)return;
    var startM=parseInt(dr.split('~')[0].split('/')[0]);
    if(!startM)return;
    var key=startM+'월';
    if(!groups[key])groups[key]=[];
    groups[key].push(wi);
  });
  return groups;
}
function idxSum(vals,idxArr){
  return idxArr.reduce(function(s,i){return s+(vals[i]||0);},0);
}
function idxSumAll(data,idxArr){
  return Object.values(data).reduce(function(s,v){return s+idxSum(v,idxArr);},0);
}

function showTab(name,btn){
  document.querySelectorAll('.tp').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nb').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
  if(name==='pl')buildPlAll();
}

function plSub(name){
  ['all','qtr','mon'].forEach(n=>{
    document.getElementById('pl-'+n).style.display=n===name?'':'none';
    document.getElementById('psb-'+n).classList.toggle('on',n===name);
  });
  if(name==='mon')buildPlMonth();
  if(name==='qtr')buildPlQtr();
}

function init(){
  const mn=Object.keys(MG),N=WEEKS.length;
  APR=MG[mn[0]]||Array.from({length:Math.ceil(N/2)},(_,i)=>i);
  MAY=MG[mn[1]]||Array.from({length:Math.floor(N/2)},(_,i)=>Math.ceil(N/2)+i);
  tI=WEEKS.map((_,i)=>Object.values(INCOME).reduce((s,v)=>s+(v[i]||0),0));
  tE=WEEKS.map((_,i)=>Object.values(EXPENSE).reduce((s,v)=>s+(v[i]||0),0));
  net=WEEKS.map((_,i)=>tI[i]-tE[i]);
  // monthly_cashflow(백데이터) 우선, 없으면 주간 집계 fallback
  var MCF=D.monthly_cashflow;
  if(MCF&&MCF.months&&MCF.months.length>=1){
    var mgKeys=Object.keys(MG),want1=parseInt(mgKeys[0])||4,want2=parseInt(mgKeys[1])||5;
    var baseYr=MCF.months[MCF.months.length-1].split('-')[0];
    var tgt1=baseYr+'-'+String(want1).padStart(2,'0');
    var tgt2=baseYr+'-'+String(want2).padStart(2,'0');
    MCF_M1=MCF.months.indexOf(tgt1);
    MCF_M2=MCF.months.indexOf(tgt2);
    if(MCF_M1<0) MCF_M1=MCF.months.length-1;
    MCF_HAS_M2=MCF_M2>=0;
    M1N=parseInt(MCF.months[MCF_M1].split('-')[1])||want1;
    M2N=MCF_HAS_M2?(parseInt(MCF.months[MCF_M2].split('-')[1])||want2):0;
    // 원 단위 합산 후 백만원으로 변환 (정확한 반올림)
    ai=Math.round(Object.values(MCF.income||{}).reduce(function(s,v){return s+(v[MCF_M1]||0);},0)/1e6);
    ae=Math.round(Object.values(MCF.expense||{}).reduce(function(s,v){return s+(v[MCF_M1]||0);},0)/1e6);
    mi=MCF_HAS_M2?Math.round(Object.values(MCF.income||{}).reduce(function(s,v){return s+(v[MCF_M2]||0);},0)/1e6):0;
    me=MCF_HAS_M2?Math.round(Object.values(MCF.expense||{}).reduce(function(s,v){return s+(v[MCF_M2]||0);},0)/1e6):0;
  } else {
    // fallback: 시작일 기준 달력 월 그룹
    var calG=WD.length?initCalGroups():{};
    M1N=parseInt(mn[0])||4;M2N=parseInt(mn[1])||5;
    CAPR=calG[M1N+'월']||APR;
    CMAY=calG[M2N+'월']||MAY;
    ai=idxSumAll(INCOME,CAPR);ae=idxSumAll(EXPENSE,CAPR);
    mi=idxSumAll(INCOME,CMAY);me=idxSumAll(EXPENSE,CMAY);
  }
  renderKpi();buildCharts();buildWeeklyTable();buildMonthlyTable();buildIssues();
  const now=new Date();
  document.getElementById('lastUpdated').textContent=now.getHours().toString().padStart(2,'0')+':'+now.getMinutes().toString().padStart(2,'0')+' 기준';
}

function renderKpi(){
  const ti=tI.reduce((s,v)=>s+v,0),te=tE.reduce((s,v)=>s+v,0),tn=net.reduce((s,v)=>s+v,0);
  document.getElementById('k_ti').textContent=ti.toLocaleString()+' 백만';
  document.getElementById('k_te').textContent=te.toLocaleString()+' 백만';
  const ne=document.getElementById('k_nt');
  ne.textContent=(tn<0?'':'+')+tn.toLocaleString()+' 백만';ne.style.color=tn>=0?'var(--bl)':'var(--rd)';
  document.getElementById('k_av').textContent=Math.round(ti/(WEEKS.length||1)).toLocaleString()+' 백만';
  document.getElementById('k_wk').textContent=WEEKS.length+'주 합산';
  var mn=Object.keys(MG);
  var m1=M1N>0?M1N+'월':(mn[0]||'4월');
  var m2=M2N>0?M2N+'월':(mn[1]||'5월');
  document.getElementById('k_m1i').textContent=m1+' 총입금';
  document.getElementById('k_m1e').textContent=m1+' 총출금';
  document.getElementById('k_m1iv').textContent=ai.toLocaleString()+' 백만';
  document.getElementById('k_m1ev').textContent=ae.toLocaleString()+' 백만';
  // M2 (5월) - 데이터 있을 때만 표시
  var m2kc2=document.getElementById('k_m2i')&&document.getElementById('k_m2i').closest('.kc');
  var m2kce=document.getElementById('k_m2e')&&document.getElementById('k_m2e').closest('.kc');
  if(MCF_HAS_M2){
    document.getElementById('k_m2i').textContent=m2+' 총입금';
    document.getElementById('k_m2e').textContent=m2+' 총출금';
    document.getElementById('k_m2iv').textContent=mi.toLocaleString()+' 백만';
    document.getElementById('k_m2ev').textContent=me.toLocaleString()+' 백만';
  } else {
    if(m2kc2)m2kc2.style.display='none';
    if(m2kce)m2kce.style.display='none';
  }
  var m1n=ai-ae,m2n=mi-me;
  var netItems=MCF_HAS_M2?[{m:m1,n:m1n,ti:ai,te:ae},{m:m2,n:m2n,ti:mi,te:me}]:[{m:m1,n:m1n,ti:ai,te:ae}];
  document.getElementById('mnKpiNet').innerHTML=netItems.map(function(x){
    var c=x.n>=0?'var(--gr)':'var(--rd)';var bt=x.n>=0?'#4ade80':'#f87171';
    return '<div style="background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:22px 28px;border-top:3px solid '+bt+';position:relative;overflow:hidden">'
      +'<div style="font-size:12px;color:var(--sub);margin-bottom:10px">'+x.m+' 순현금흐름</div>'
      +'<div style="font-size:30px;font-weight:700;color:'+c+';line-height:1;margin-bottom:8px">'+(x.n>=0?'+':'')+x.n.toLocaleString()+' 백만</div>'
      +'<div style="font-size:12px;color:var(--sub)">총입금 '+x.ti.toLocaleString()+' &minus; 총출금 '+x.te.toLocaleString()+'</div>'
      +'</div>';
  }).join('');
}

function mk(id,type,d,opts,pl){if(C[id])C[id].destroy();C[id]=new Chart(document.getElementById(id),{type,data:d,options:opts,plugins:pl||[]});}
const SC=()=>({x:{grid:{color:GC},ticks:{font:{size:11}}},y:{grid:{color:GC},ticks:{callback:v=>v.toLocaleString()+'백만'}}});

function buildCharts(){
  mk('cBar','bar',{labels:WEEKS,datasets:[{label:'입금',data:tI,backgroundColor:'rgba(74,222,128,.7)',borderColor:'#4ade80',borderWidth:1,borderRadius:4},{label:'출금',data:tE,backgroundColor:'rgba(248,113,113,.7)',borderColor:'#f87171',borderWidth:1,borderRadius:4}]},{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:12,padding:16}}},scales:SC()});
  mk('cNet','line',{labels:WEEKS,datasets:[{label:'순현금',data:net,borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,.12)',fill:true,tension:.4,pointBackgroundColor:net.map(v=>v>=0?'#4ade80':'#f87171'),pointRadius:5}]},{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:SC()});
  const ik=Object.keys(INCOME),iv=ik.map(x=>INCOME[x].reduce((s,n)=>s+n,0));
  mk('cID','doughnut',{labels:ik,datasets:[{data:iv,backgroundColor:CLR,borderWidth:2,borderColor:'#1a1d27'}]},{responsive:true,maintainAspectRatio:false,cutout:'65%',plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:10,font:{size:11}}}}});
  const ek=Object.keys(EXPENSE),ev=ek.map(x=>EXPENSE[x].reduce((s,n)=>s+n,0));
  mk('cED','doughnut',{labels:ek,datasets:[{data:ev,backgroundColor:[...CLR].reverse(),borderWidth:2,borderColor:'#1a1d27'}]},{responsive:true,maintainAspectRatio:false,cutout:'65%',plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:10,font:{size:11}}}}});
  const sk=Object.keys(INCOME).slice(0,5);
  mk('cSB','bar',{labels:WEEKS,datasets:sk.map((x,i)=>({label:x,data:INCOME[x],backgroundColor:CLR[i],borderWidth:0,borderRadius:2}))},{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:8,font:{size:10}}}},scales:{x:{stacked:true,grid:{color:GC},ticks:{font:{size:10}}},y:{stacked:true,grid:{color:GC},ticks:{callback:v=>v.toLocaleString()}}}});
  var mcfRef=D.monthly_cashflow;
  var m1c=M1N>0?M1N+'월':(Object.keys(MG)[0]||'4월');
  var m2c=M2N>0?M2N+'월':(Object.keys(MG)[1]||'5월');
  var mbLabels=MCF_HAS_M2?[m1c,m2c]:[m1c];
  var mbIn=MCF_HAS_M2?[ai,mi]:[ai],mbEx=MCF_HAS_M2?[ae,me]:[ae],mbNet=MCF_HAS_M2?[ai-ae,mi-me]:[ai-ae];
  mk('cMB','bar',{labels:mbLabels,datasets:[{label:'입금',data:mbIn,backgroundColor:'rgba(74,222,128,.7)',borderColor:'#4ade80',borderWidth:1,borderRadius:6},{label:'출금',data:mbEx,backgroundColor:'rgba(248,113,113,.7)',borderColor:'#f87171',borderWidth:1,borderRadius:6},{label:'순현금',data:mbNet,backgroundColor:'rgba(96,165,250,.7)',borderColor:'#60a5fa',borderWidth:1,borderRadius:6}]},{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:12,padding:16}}},scales:SC()});
  var incRef=mcfRef&&mcfRef.income||{};
  var k5=Object.keys(incRef).length?Object.keys(incRef).slice(0,5):Object.keys(INCOME).slice(0,5);
  var ds1={label:m1c,data:k5.map(function(x){return incRef[x]?Math.round((incRef[x][MCF_M1]||0)/1e6):idxSum(INCOME[x]||[],CAPR);}),backgroundColor:'rgba(74,222,128,.5)',borderColor:'#4ade80',borderWidth:1,borderRadius:4};
  var cMIDs=[ds1];
  if(MCF_HAS_M2){cMIDs.push({label:m2c,data:k5.map(function(x){return incRef[x]?Math.round((incRef[x][MCF_M2]||0)/1e6):idxSum(INCOME[x]||[],CMAY);}),backgroundColor:'rgba(96,165,250,.6)',borderColor:'#60a5fa',borderWidth:1,borderRadius:4});}
  mk('cMI','bar',{labels:k5,datasets:cMIDs},{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:12,padding:16}}},scales:SC()});
}

function buildWeeklyTable(){
  const t=document.getElementById('tWeekly');
  let h=`<thead><tr><th style="min-width:110px">구분</th><th style="min-width:90px">항목</th>`;
  WEEKS.forEach((w,i)=>{h+=`<th style="line-height:1.5">${w}<br><span style="font-size:10px;font-weight:400;color:#606480">${WD[i]||''}</span></th>`;});
  h+=`<th>합계</th></tr></thead><tbody>`;
  const row=(lb,d,sub,deep)=>{const s=d.reduce((a,v)=>a+v,0);const pl=deep?56:sub?40:24;h+=`<tr><td></td><td style="text-align:left;padding-left:${pl}px;color:${deep?'var(--sub)':'var(--tx)'};font-size:${deep?'12':'13'}px">${lb}</td>`;d.forEach(v=>{h+=`<td class="${v<0?'neg':''}" style="${deep?'color:var(--sub);font-size:12px':''}">${v?F(v):'-'}</td>`;});h+=`<td style="font-weight:${deep?'400':'700'};${deep?'color:var(--sub)':''}">${F(s)||'-'}</td></tr>`;};
  h+=`<tr class="it"><td colspan="2" style="padding-left:24px">▶ 입금 합계</td>`;tI.forEach(v=>{h+=`<td class="pos">${F(v)}</td>`;});h+=`<td class="pos">${F(tI.reduce((s,v)=>s+v,0))}</td></tr>`;
  // 데이터가 있는 마지막 주차 기준 내림차순 정렬 헬퍼
  let lastIdx=WEEKS.length-1;
  for(let i=WEEKS.length-1;i>=0;i--){if(tI[i]||tE[i]){lastIdx=i;break;}}
  const lastVal=arr=>arr[lastIdx]||0;
  Object.entries(INCOME)
    .sort((a,b)=>lastVal(b[1])-lastVal(a[1]))
    .forEach(([k,v])=>row(k,v,true));
  h+=`<tr class="et"><td colspan="2" style="padding-left:24px">▶ 출금 합계</td>`;tE.forEach(v=>{h+=`<td class="neg">${F(v)}</td>`;});h+=`<td class="neg">${F(tE.reduce((s,v)=>s+v,0))}</td></tr>`;
  // 기타 서브 항목 분리
  const etcSubs=Object.keys(EXPENSE).filter(k=>k.startsWith('기타_'));
  const hasSubs=etcSubs.some(k=>EXPENSE[k].some(v=>v!==0));
  const etcBase=EXPENSE['기타비용']||WEEKS.map(()=>0);
  const etcTot=WEEKS.map((_,i)=>(etcBase[i]||0)+etcSubs.reduce((s,sk)=>s+((EXPENSE[sk]||[])[i]||0),0));
  Object.entries(EXPENSE)
    .filter(([k])=>!k.startsWith('기타_')&&k!=='기타비용')
    .sort((a,b)=>lastVal(b[1])-lastVal(a[1]))
    .forEach(([k,v])=>row(k,v,true));
  // 기타비용 합계는 맨 마지막
  const etcLb=hasSubs?'기타비용 합계':'기타비용';
  const etcS=etcTot.reduce((a,x)=>a+x,0);
  h+=`<tr><td></td><td style="text-align:left;padding-left:40px;color:var(--tx)">${etcLb}</td>`;
  etcTot.forEach(x=>{h+=`<td class="${x<0?'neg':''}">${x?F(x):'-'}</td>`;});
  h+=`<td style="font-weight:700">${F(etcS)||'-'}</td></tr>`;
  if(hasSubs){
    etcSubs
      .filter(sk=>(EXPENSE[sk]||[]).some(x=>x!==0))
      .sort((a,b)=>lastVal(EXPENSE[b])-lastVal(EXPENSE[a]))
      .forEach(sk=>row(' └ '+sk.replace('기타_',''),EXPENSE[sk],true,true));
  }
  h+=`<tr class="nr"><td colspan="2" style="padding-left:24px">💡 순현금흐름</td>`;net.forEach(v=>{h+=`<td class="${v>=0?'pos':'neg'}">${FN(v)}</td>`;});const nt=net.reduce((s,v)=>s+v,0);h+=`<td class="${nt>=0?'pos':'neg'}">${FN(nt)}</td></tr></tbody>`;
  t.innerHTML=h;
}

function buildMonthlyTable(){
  var MCF=D.monthly_cashflow;
  var useMCF=MCF&&MCF.months&&MCF.months.length>=1;
  var m1Label=M1N>0?M1N+'월':'4월',m2Label=M2N>0?M2N+'월':'5월';
  const t=document.getElementById('tMonthly');
  var colH=MCF_HAS_M2?`<th>${m1Label}</th><th>${m2Label}</th><th>증감</th><th>증감율</th>`:`<th>${m1Label}</th>`;
  let h=`<thead><tr><th>구분</th><th>항목</th>${colH}</tr></thead><tbody>`;
  const row=(lb,a,m,sub)=>{
    var base=`<tr><td></td><td style="text-align:left;padding-left:${sub?'40':'24'}px;color:var(--tx)">${lb}</td><td>${F(a)||'-'}</td>`;
    if(MCF_HAS_M2){const d=m-a,r=a?(d/a*100).toFixed(1):null,dc=d>=0?'pos':'neg';base+=`<td>${F(m)||'-'}</td><td class="${dc}">${a||m?FN(d):'-'}</td><td class="${dc}">${r!==null?(d>=0?'+':'')+r+'%':'-'}</td>`;}
    h+=base+'</tr>';
  };
  // 입금 합계
  var itCols=MCF_HAS_M2?`<td>${F(ai)}</td><td>${F(mi)}</td><td class="${mi-ai>=0?'pos':'neg'}">${FN(mi-ai)}</td><td class="${mi-ai>=0?'pos':'neg'}">${ai?(((mi-ai)/ai*100).toFixed(1)>=0?'+':'')+((mi-ai)/ai*100).toFixed(1)+'%':'-'}</td>`:`<td>${F(ai)}</td>`;
  h+=`<tr class="it"><td colspan="2" style="padding-left:24px">▶ 입금 합계</td>${itCols}</tr>`;
  var r6=function(v){return Math.round((v||0)/1e6);};
  if(useMCF){
    Object.entries(MCF.income||{}).forEach(([k,v])=>row(k,r6(v[MCF_M1]),MCF_HAS_M2?r6(v[MCF_M2]):0,true));
  } else {
    Object.entries(INCOME).forEach(([k,v])=>row(k,idxSum(v,CAPR),MCF_HAS_M2?idxSum(v,CMAY):0,true));
  }
  // 출금 합계
  var etCols=MCF_HAS_M2?`<td>${F(ae)}</td><td>${F(me)}</td><td class="${me-ae>=0?'pos':'neg'}">${FN(me-ae)}</td><td class="${me-ae>=0?'pos':'neg'}">${ae?(((me-ae)/ae*100).toFixed(1)>=0?'+':'')+((me-ae)/ae*100).toFixed(1)+'%':'-'}</td>`:`<td>${F(ae)}</td>`;
  h+=`<tr class="et"><td colspan="2" style="padding-left:24px">▶ 출금 합계</td>${etCols}</tr>`;
  // 월간 출금: 기타 서브항목 처리
  var expSrc=useMCF?(MCF.expense||{}):EXPENSE;
  var etcSubsM=Object.keys(expSrc).filter(k=>k.startsWith('기타_'));
  var hasSubsM=etcSubsM.some(k=>{var v=expSrc[k];return useMCF?(v[MCF_M1]||0)!==0||(MCF_HAS_M2&&(v[MCF_M2]||0)!==0):(idxSum(v,CAPR)||idxSum(v,CMAY));});
  // 기타비용 제외 항목 최근월 기준 내림차순 정렬
  var recentVal=function(v){return MCF_HAS_M2?(useMCF?r6(v[MCF_M2]||0):idxSum(v,CMAY)):(useMCF?r6(v[MCF_M1]||0):idxSum(v,CAPR));};
  Object.entries(expSrc)
    .filter(([k])=>!k.startsWith('기타_')&&k!=='기타비용')
    .sort((a,b)=>recentVal(b[1])-recentVal(a[1]))
    .forEach(([k,v])=>{
      var a1=useMCF?r6(v[MCF_M1]):idxSum(v,CAPR);
      var a2=MCF_HAS_M2?(useMCF?r6(v[MCF_M2]):idxSum(v,CMAY)):0;
      row(k,a1,a2,true);
    });
  // 기타비용 합계는 맨 마지막
  var etcBv=expSrc['기타비용']||{};
  var etcA1=(useMCF?r6(etcBv[MCF_M1]||0):idxSum(etcBv,CAPR))+etcSubsM.reduce((s,sk)=>{var sv=expSrc[sk];return s+(useMCF?r6(sv[MCF_M1]||0):idxSum(sv,CAPR));},0);
  var etcA2=MCF_HAS_M2?(useMCF?r6(etcBv[MCF_M2]||0):idxSum(etcBv,CMAY))+etcSubsM.reduce((s,sk)=>{var sv=expSrc[sk];return s+(useMCF?r6(sv[MCF_M2]||0):idxSum(sv,CMAY));},0):0;
  row(hasSubsM?'기타비용 합계':'기타비용',etcA1,etcA2,true);
  if(hasSubsM){
    etcSubsM
      .filter(sk=>{var sv=expSrc[sk];return (useMCF?r6(sv[MCF_M1]||0):idxSum(sv,CAPR))||(MCF_HAS_M2?(useMCF?r6(sv[MCF_M2]||0):idxSum(sv,CMAY)):0);})
      .sort((a,b)=>recentVal(expSrc[b])-recentVal(expSrc[a]))
      .forEach(sk=>{var sv=expSrc[sk];var b1=useMCF?r6(sv[MCF_M1]||0):idxSum(sv,CAPR);var b2=MCF_HAS_M2?(useMCF?r6(sv[MCF_M2]||0):idxSum(sv,CMAY)):0;row(' └ '+sk.replace('기타_',''),b1,b2,true);});
  }
  const an=ai-ae,mn2=mi-me,dn=mn2-an,rn=an?(dn/Math.abs(an)*100).toFixed(1):null;
  var nrCols=MCF_HAS_M2?`<td class="${an>=0?'pos':'neg'}">${FN(an)}</td><td class="${mn2>=0?'pos':'neg'}">${FN(mn2)}</td><td class="${dn>=0?'pos':'neg'}">${FN(dn)}</td><td class="${dn>=0?'pos':'neg'}">${rn!==null?(dn>=0?'+':'')+rn+'%':'-'}</td>`:`<td class="${an>=0?'pos':'neg'}">${FN(an)}</td>`;
  h+=`<tr class="nr"><td colspan="2" style="padding-left:24px">💡 순현금흐름</td>${nrCols}</tr></tbody>`;
  t.innerHTML=h;
}

// ══ 주요 이슈 편집 ════════════════════════════════════════
var IK='pb_issues_v2';
var _editIdx=-1;
function loadIssues(){try{var s=localStorage.getItem(IK);if(s)return JSON.parse(s);}catch(e){}return ISSUES.map(function(d){return Object.assign({},d);});}
function saveIssues(arr){try{localStorage.setItem(IK,JSON.stringify(arr));}catch(e){}}
function buildIssues(){
  var arr=loadIssues();
  var lvlTxt={high:'상',mid:'중',low:'하'};
  document.getElementById('issueGrid').innerHTML=arr.map(function(d,i){
    return '<div class="ic"><div class="id '+(d.level||'high')+'"></div>'
      +'<div style="flex:1"><span class="itag">'+(d.tag||'')+'</span>'
      +'<span style="font-size:10px;color:var(--sub);margin-left:6px">중요도: '+(lvlTxt[d.level]||'상')+'</span>'
      +'<div class="ititle">'+(d.title||'')+'</div>'
      +'<div class="idesc">'+(d.desc||'').split('\\n').join('<br>')+'</div>'
      +'<div class="iac">'
      +'<button class="ib" onclick="openIssueModal('+i+')">수정</button>'
      +'<button class="ib del" onclick="deleteIssue('+i+')">삭제</button>'
      +'</div></div></div>';
  }).join('')+(arr.length===0?'<div style="color:var(--sub);font-size:13px;padding:20px">등록된 이슈가 없습니다.</div>':'');
  initMemo();
}
function openIssueModal(idx){
  _editIdx=idx;
  document.getElementById('modalTitle').textContent=idx<0?'이슈 추가':'이슈 수정';
  if(idx>=0){
    var arr=loadIssues(),d=arr[idx]||{};
    document.getElementById('iTag').value=d.tag||'';
    document.getElementById('iTitle').value=d.title||'';
    document.getElementById('iLevel').value=d.level||'high';
    document.getElementById('iDesc').value=d.desc||'';
  } else {
    document.getElementById('iTag').value='';
    document.getElementById('iTitle').value='';
    document.getElementById('iLevel').value='high';
    document.getElementById('iDesc').value='';
  }
  document.getElementById('issueModal').style.display='flex';
  document.getElementById('iTag').focus();
}
function closeIssueModal(){document.getElementById('issueModal').style.display='none';}
function saveIssue(){
  var tag=document.getElementById('iTag').value.trim();
  var title=document.getElementById('iTitle').value.trim();
  if(!title){document.getElementById('iTitle').focus();return;}
  var d={tag:tag,title:title,level:document.getElementById('iLevel').value,desc:document.getElementById('iDesc').value.trim()};
  var arr=loadIssues();
  if(_editIdx<0) arr.push(d); else arr[_editIdx]=d;
  saveIssues(arr);
  closeIssueModal();
  buildIssues();
}
function deleteIssue(idx){
  if(!confirm('이 이슈를 삭제할까요?')) return;
  var arr=loadIssues();arr.splice(idx,1);saveIssues(arr);buildIssues();
}

// ══ 주차별 메모 ════════════════════════════════════════════
const MEMO_MONTHS=['4월','5월','6월','7월','8월','9월','10월','11월','12월'];
const MEMO_WEEKS=['1주차','2주차','3주차','4주차','5주차'];

function initMemo(){
  const btns=document.getElementById('memoMonthBtns');
  if(!btns)return;
  btns.innerHTML=MEMO_MONTHS.map((m,i)=>`<button class="mmb${i===0?' on':''}" id="mmb_${m}" onclick="selectMemoMonth('${m}')">${m}</button>`).join('');
  selectMemoMonth(MEMO_MONTHS[0]);
}

function selectMemoMonth(month){
  MEMO_MONTHS.forEach(function(m){
    var b=document.getElementById('mmb_'+m);
    if(b)b.className='mmb'+(m===month?' on':'');
  });
  var content=document.getElementById('memoContent');
  if(!content)return;
  content.innerHTML=MEMO_WEEKS.map(function(w){
    var key='pb_memo_'+month+'_'+w;
    var saved='';try{saved=localStorage.getItem(key)||'';}catch(e){}
    var safeVal=saved.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return '<div class="mwc">'
      +'<div class="mwh">'
      +'<span style="font-size:13px;font-weight:600;color:var(--tx)">'+month+' '+w+'</span>'
      +'<span class="msv" id="sv_'+month+'_'+w+'">&#10003; 저장됨</span>'
      +'</div>'
      +'<textarea class="mta" id="memo_'+month+'_'+w+'" data-month="'+month+'" data-week="'+w+'" oninput="saveMemo(this.dataset.month,this.dataset.week)">'
      +safeVal
      +'</textarea>'
      +'</div>';
  }).join('');
}

function saveMemo(month,week){
  var key='pb_memo_'+month+'_'+week;
  var el=document.getElementById('memo_'+month+'_'+week);
  if(!el)return;
  try{localStorage.setItem(key,el.value);}catch(e){}
  var sv=document.getElementById('sv_'+month+'_'+week);
  if(sv){sv.classList.add('show');setTimeout(function(){sv.classList.remove('show');},2000);}
}

// ══ 월간손익 - 분기별 비교 ════════════════════════════════════════════
var _qDat=null;
var QCLR2=['#60a5fa','#f59e0b'];

function buildPlQtr(){
  if(_qDat){renderQtrPair();return;}
  if(!PL||!PL.items||!PL.months){
    document.getElementById('qtrCmpArea').innerHTML='<p style="padding:20px;color:var(--sub)">손익 데이터가 없습니다.</p>';return;
  }
  var items=PL.items,months=PL.months;
  var qMap={},qOrder=[];
  months.forEach(function(m,mi){
    var y=m.slice(0,4),mo=parseInt(m.slice(5,7));
    var q=Math.ceil(mo/3);
    var key=y+'Q'+q;
    var lbl=y+'년 '+q+'분기';
    if(!qMap[key]){qMap[key]={key:key,lbl:lbl,year:y,q:q,idxs:[]};qOrder.push(key);}
    qMap[key].idxs.push(mi);
  });
  function qSum(vals,idxs){return idxs.reduce(function(s,i){return s+(vals[i]||0);},0);}
  var qItems={};
  Object.keys(items).forEach(function(k){
    qItems[k]=qOrder.map(function(qk){return qSum(items[k],qMap[qk].idxs);});
  });
  _qDat={qMap:qMap,qOrder:qOrder,qItems:qItems};
  var s1=document.getElementById('qSel1'),s2=document.getElementById('qSel2');
  var opts='<option value="">분기 선택</option>';
  qOrder.slice().reverse().forEach(function(qk){opts+='<option value="'+qk+'">'+qMap[qk].lbl+'</option>';});
  s1.innerHTML=opts; s2.innerHTML=opts;
  if(qOrder.length>=2){s1.value=qOrder[qOrder.length-1];s2.value=qOrder[qOrder.length-2];}
  renderQtrPair();
}

function renderQtrPair(){
  var area=document.getElementById('qtrCmpArea');
  if(!_qDat)return;
  var v1=document.getElementById('qSel1').value;
  var v2=document.getElementById('qSel2').value;
  if(!v1||!v2){
    area.innerHTML='<div style="padding:40px;text-align:center;color:var(--sub);font-size:13px;background:var(--sf);border:1px solid var(--bd);border-radius:12px">구분1과 구분2를 모두 선택해주세요.</div>';
    return;
  }
  if(v1===v2){
    area.innerHTML='<div style="padding:40px;text-align:center;color:var(--sub);font-size:13px;background:var(--sf);border:1px solid var(--bd);border-radius:12px">서로 다른 분기를 선택해주세요.</div>';
    return;
  }
  var qd=_qDat,qMap=qd.qMap,qItems=qd.qItems,qOrder=qd.qOrder;
  var sel=[v1,v2];
  var selD=sel.map(function(qk){return qMap[qk];});
  function gv(key,qk){var v=qItems[key];if(!v)return 0;var qi=qOrder.indexOf(qk);return v[qi]||0;}
  var rev=sel.map(function(qk){return gv('매출액',qk);});
  var op=sel.map(function(qk){return gv('영업이익',qk);});
  var gp=sel.map(function(qk){return gv('매출총이익',qk);});
  var opR=rev.map(function(v,i){return v?+(op[i]/v*100).toFixed(1):0;});
  var gpR=rev.map(function(v,i){return v?+(gp[i]/v*100).toFixed(1):0;});

  function fmtV(v){return v<0?'('+Math.abs(v).toLocaleString()+')':v.toLocaleString();}
  function diffBadge(d,base){
    if(d===0)return '<span style="color:var(--sub)">─</span>';
    var c=d>0?'#4ade80':'#f87171';
    var pr=base?+(d/Math.abs(base)*100).toFixed(1):null;
    return '<span style="color:'+c+';font-weight:600">'+(d>0?'▲':'▼')+' '+Math.abs(Math.round(d)).toLocaleString()+'</span>'
      +(pr!==null?'<span style="font-size:11px;color:'+c+'"> ('+(d>0?'+':'')+pr+'%)</span>':'');
  }
  var dr=rev[0]-rev[1], dop=op[0]-op[1], dor=+(opR[0]-opR[1]).toFixed(1);

  // ── KPI 카드 2개 ──
  var h='<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">';
  sel.forEach(function(qk,si){
    var d=selD[si],r=rev[si],o=op[si],or2=opR[si];
    var oc=o>=0?'var(--gr)':'var(--rd)';
    var ci=QCLR2[si];
    h+='<div style="background:var(--sf);border:1px solid var(--bd);border-radius:14px;padding:20px 22px;border-top:4px solid '+ci+'">'
      +'<div style="font-size:11px;color:'+ci+';font-weight:700;letter-spacing:.8px;margin-bottom:8px">구분 '+(si+1)+'</div>'
      +'<div style="font-size:18px;font-weight:700;color:var(--tx);margin-bottom:14px">'+d.lbl+'</div>'
      +'<div style="display:flex;flex-direction:column;gap:8px">'
      +'<div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:var(--sub)">매출액</span><span style="font-size:13px;font-weight:600;color:var(--tx)">'+r.toLocaleString()+' 백만</span></div>'
      +'<div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:var(--sub)">매출총이익</span><span style="font-size:13px;font-weight:600;color:#60a5fa">'+gp[si].toLocaleString()+' 백만</span></div>'
      +'<div style="height:1px;background:var(--bd)"></div>'
      +'<div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:var(--sub)">영업이익</span><span style="font-size:15px;font-weight:700;color:'+oc+'">'+o.toLocaleString()+' 백만</span></div>'
      +'<div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:var(--sub)">영업이익률</span><span style="font-size:13px;font-weight:700;color:'+oc+'">'+or2+'%</span></div>'
      +'</div></div>';
  });
  h+='</div>';

  // ── 증감 요약 ──
  var dc_r=dr>=0?'#4ade80':'#f87171', dc_o=dop>=0?'#4ade80':'#f87171', dc_or=dor>=0?'#4ade80':'#f87171';
  var pr_r=rev[1]?+(dr/Math.abs(rev[1])*100).toFixed(1):null;
  var pr_o=op[1]?+(dop/Math.abs(op[1])*100).toFixed(1):null;
  h+='<div style="background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:16px 22px;margin-bottom:20px">'
    +'<div style="font-size:12px;color:var(--sub);font-weight:600;margin-bottom:12px">구분1 - 구분2 증감</div>'
    +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0">'
    +'<div style="text-align:center;padding:8px 4px">'
    +'<div style="font-size:11px;color:var(--sub);margin-bottom:6px">매출액</div>'
    +'<div style="font-size:15px;font-weight:700;color:'+dc_r+'">'+(dr>=0?'▲':'▼')+' '+Math.abs(Math.round(dr)).toLocaleString()+'</div>'
    +'<div style="font-size:11px;color:'+dc_r+';margin-top:2px">'+(pr_r!==null?(dr>=0?'+':'')+pr_r+'%':'─')+'</div></div>'
    +'<div style="text-align:center;padding:8px 4px;border-left:1px solid var(--bd);border-right:1px solid var(--bd)">'
    +'<div style="font-size:11px;color:var(--sub);margin-bottom:6px">영업이익</div>'
    +'<div style="font-size:15px;font-weight:700;color:'+dc_o+'">'+(dop>=0?'▲':'▼')+' '+Math.abs(Math.round(dop)).toLocaleString()+'</div>'
    +'<div style="font-size:11px;color:'+dc_o+';margin-top:2px">'+(pr_o!==null?(dop>=0?'+':'')+pr_o+'%':'─')+'</div></div>'
    +'<div style="text-align:center;padding:8px 4px">'
    +'<div style="font-size:11px;color:var(--sub);margin-bottom:6px">영업이익률</div>'
    +'<div style="font-size:15px;font-weight:700;color:'+dc_or+'">'+(dor>=0?'▲':'▼')+' '+Math.abs(dor)+'%p</div>'
    +'<div style="font-size:11px;color:'+dc_or+';margin-top:2px">'+(dor>=0?'+':'')+dor+'%p</div>'
    +'</div></div></div>';

  // ── 차트 ──
  var selLbls=sel.map(function(qk){return qMap[qk].lbl;});
  h+='<div style="display:grid;grid-template-columns:3fr 2fr;gap:16px;margin-bottom:20px">'
    +'<div class="cc"><div class="ct">주요 항목 비교 <span class="cs">단위: 백만원</span></div><div style="position:relative;height:280px"><canvas id="cQP1"></canvas></div></div>'
    +'<div class="cc"><div class="ct">이익률 비교 <span class="cs">%</span></div><div style="position:relative;height:280px"><canvas id="cQP2"></canvas></div></div>'
    +'</div>';

  // ── 상세 비교 테이블 ──
  var thS='padding:9px 12px;text-align:right;background:var(--sf2);color:var(--sub);font-size:11px;font-weight:600;border-bottom:1px solid var(--bd);white-space:nowrap;';
  var tdS='padding:8px 12px;text-align:right;border-bottom:1px solid rgba(46,51,80,.4);font-size:12px;white-space:nowrap;';
  var tdL='padding:8px 12px;text-align:left;border-bottom:1px solid rgba(46,51,80,.4);font-size:12px;font-weight:500;color:var(--tx);white-space:nowrap;';
  h+='<div class="tc"><div class="th"><span style="font-size:14px;font-weight:600">손익 항목 비교</span><span style="font-size:12px;color:var(--sub)">단위: 백만원</span></div>'
    +'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse"><thead><tr>'
    +'<th style="'+thS+'text-align:left;min-width:150px">과 목</th>'
    +'<th style="'+thS+'color:#60a5fa">구분1  '+selD[0].lbl+'</th>'
    +'<th style="'+thS+'color:#f59e0b">구분2  '+selD[1].lbl+'</th>'
    +'<th style="'+thS+'color:#a78bfa">증감 (1-2)</th>'
    +'<th style="'+thS+'color:#a78bfa">증감율</th>'
    +'</tr></thead><tbody>';
  var BOLDS=['매출액','매출총이익','영업이익'];
  Object.keys(qItems).forEach(function(k){
    var isBold=BOLDS.indexOf(k)>=0,isOp=k==='영업이익';
    var bg=isOp?'rgba(74,222,128,.04)':isBold?'rgba(99,102,241,.05)':'';
    var val1=gv(k,v1),val2=gv(k,v2);
    var dd=val1-val2,dpr=val2?+(dd/Math.abs(val2)*100).toFixed(1):null;
    var vc1=isOp?(val1>=0?'#4ade80':'#f87171'):(val1<0?'var(--rd)':'var(--sub)');
    var vc2=isOp?(val2>=0?'#4ade80':'#f87171'):(val2<0?'var(--rd)':'var(--sub)');
    var dc3=dd>0?'#4ade80':dd<0?'#f87171':'var(--sub)';
    h+='<tr style="background:'+bg+'">'
      +'<td style="'+tdL+(isBold?';font-weight:700':'')+'">'+k+'</td>'
      +'<td style="'+tdS+'color:'+vc1+(isBold?';font-weight:700':'')+'">'+fmtV(val1)+'</td>'
      +'<td style="'+tdS+'color:'+vc2+(isBold?';font-weight:700':'')+'">'+fmtV(val2)+'</td>'
      +'<td style="'+tdS+'color:'+dc3+'">'+(dd===0?'─':(dd>0?'▲ ':'▼ ')+Math.abs(Math.round(dd)).toLocaleString())+'</td>'
      +'<td style="'+tdS+'color:'+dc3+'">'+(dpr!==null?(dd>=0?'+':'')+dpr+'%':'─')+'</td>'
      +'</tr>';
  });
  // 영업이익률 행
  var dor2=+(opR[0]-opR[1]).toFixed(1),dcor=dor2>=0?'#4ade80':'#f87171';
  h+='<tr style="background:rgba(99,102,241,.03)">'
    +'<td style="'+tdL+'color:var(--sub)"> 영업이익률</td>'
    +'<td style="'+tdS+'color:'+(opR[0]>=0?'var(--gr)':'var(--rd)')+';font-weight:600">'+opR[0]+'%</td>'
    +'<td style="'+tdS+'color:'+(opR[1]>=0?'var(--gr)':'var(--rd)')+';font-weight:600">'+opR[1]+'%</td>'
    +'<td style="'+tdS+'color:'+dcor+'">'+(dor2>=0?'▲ ':'▼ ')+Math.abs(dor2)+'%p</td>'
    +'<td style="'+tdS+'color:'+dcor+'">'+(dor2>=0?'+':'')+dor2+'%p</td>'
    +'</tr>';
  h+='</tbody></table></div></div>';
  area.innerHTML=h;

  // 차트 렌더
  mk('cQP1','bar',{labels:selLbls,datasets:[
    {label:'매출액',data:rev,backgroundColor:['rgba(96,165,250,.7)','rgba(245,158,11,.7)'],borderColor:QCLR2,borderWidth:2,borderRadius:6},
    {label:'영업이익',data:op,type:'line',borderColor:op.map(function(v,i){return v>=0?QCLR2[i]:'#f87171';}),backgroundColor:'transparent',borderWidth:2,pointRadius:6,pointBackgroundColor:op.map(function(v,i){return v>=0?QCLR2[i]:'#f87171';}),tension:.2}
  ]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:34,bottom:16}},plugins:{
    legend:{position:'top',labels:{boxWidth:12,padding:14}},
    datalabels:{
      display:true,
      anchor:function(ctx){return ctx.datasetIndex===0?'end':'start';},
      align:function(ctx){return ctx.datasetIndex===0?'end':'bottom';},
      offset:function(ctx){return ctx.datasetIndex===0?4:8;},
      color:function(ctx){return ctx.datasetIndex===0?QCLR2[ctx.dataIndex]:(op[ctx.dataIndex]>=0?QCLR2[ctx.dataIndex]:'#f87171');},
      font:function(ctx){return {size:ctx.datasetIndex===0?12:11,weight:'700'};},
      formatter:function(v){return v.toLocaleString()+'백만';}
    }
  },scales:{x:{grid:{color:GC}},y:{grid:{color:GC},ticks:{callback:function(v){return v.toLocaleString()+'백만';},font:{size:10}}}}}, [ChartDataLabels]);
  mk('cQP2','bar',{labels:selLbls,datasets:[
    {label:'영업이익률',data:opR,backgroundColor:opR.map(function(v,i){return v>=0?QCLR2[i]+'cc':'rgba(248,113,113,.7)';}),borderRadius:6},
    {label:'매출총이익률',data:gpR,backgroundColor:['rgba(96,165,250,.3)','rgba(245,158,11,.3)'],borderRadius:6}
  ]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:32}},plugins:{
    legend:{position:'top',labels:{boxWidth:10,padding:12,font:{size:11}}},
    datalabels:{
      display:true,
      anchor:'end',align:'end',offset:4,
      color:function(ctx){return ctx.datasetIndex===0?(opR[ctx.dataIndex]>=0?QCLR2[ctx.dataIndex]:'#f87171'):QCLR2[ctx.dataIndex];},
      font:function(ctx){return {size:ctx.datasetIndex===0?12:11,weight:ctx.datasetIndex===0?'700':'600'};},
      formatter:function(v){return v+'%';}
    }
  },scales:{x:{grid:{color:GC}},y:{grid:{color:GC},ticks:{callback:function(v){return v+'%';},font:{size:10}}}}}, [ChartDataLabels]);
}

// ══ 월간손익 - 전체 요약 ════════════════════════════════════════════
function buildPlAll(){
  if(!PL||!PL.items||!PL.months){
    document.getElementById('plKpi').innerHTML='<p style="padding:20px;color:var(--sub)">손익 데이터가 없습니다.</p>';
    return;
  }
  const items=PL.items,months=PL.months,labels=PL.labels||months;
  const n=months.length,last=n-1;
  const rev=items['매출액']||Array(n).fill(0);
  const op=items['영업이익']||Array(n).fill(0);
  const gp=items['매출총이익']||Array(n).fill(0);
  const gpR=gp.map((v,i)=>rev[i]?+(v/rev[i]*100).toFixed(1):0);
  const opR=op.map((v,i)=>rev[i]?+(v/rev[i]*100).toFixed(1):0);

  // KPI
  document.getElementById('plKpi').innerHTML=[
    {lb:'최근월 매출액',v:rev[last],c:'#60a5fa',pv:last>0?rev[last-1]:null,isPct:false},
    {lb:'최근월 영업이익',v:op[last],c:op[last]>=0?'#4ade80':'#f87171',pv:last>0?op[last-1]:null,isPct:false},
    {lb:'최근월 영업이익률',v:opR[last],c:op[last]>=0?'#60a5fa':'#f87171',pv:last>0?opR[last-1]:null,isPct:true},
  ].map(k=>{
    let diff='';
    if(k.pv!==null){
      const d=k.isPct?+(k.v-k.pv).toFixed(1):k.v-k.pv;
      const r=!k.isPct&&k.pv?+(d/Math.abs(k.pv)*100).toFixed(1):null;
      const c=d>=0?'#4ade80':'#f87171';
      diff=`<div style="font-size:11px;color:${c};margin-top:5px">${d>=0?'▲':'▼'} ${k.isPct?Math.abs(d)+'%p':(Math.abs(Math.round(d)).toLocaleString()+(r?' ('+r+'%)':''))} 전월비</div>`;
    }
    return `<div style="background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:20px 24px;border-top:3px solid ${k.c}">
      <div style="font-size:12px;color:var(--sub);margin-bottom:6px">${k.lb} (${months[last]})</div>
      <div style="font-size:22px;font-weight:700;color:${k.c}">${k.isPct?k.v+'%':k.v.toLocaleString()+' 백만'}</div>${diff}</div>`;
  }).join('');

  mk('cPL','line',{labels,datasets:[
    {label:'매출액',data:rev,borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,.08)',fill:true,tension:.3,pointRadius:3,
     datalabels:{anchor:'end',align:'top',color:'#93c5fd',font:{size:8,weight:'600'},formatter:v=>v.toLocaleString(),rotation:-40,offset:1}},
    {label:'영업이익',data:op,borderColor:'#4ade80',fill:false,tension:.3,pointRadius:3,
     pointBackgroundColor:op.map(v=>v>=0?'#4ade80':'#f87171'),
     segment:{borderColor:ctx=>ctx.p1.parsed.y<0?'#f87171':'#4ade80'},
     datalabels:{anchor:'end',align:'bottom',color:ctx=>op[ctx.dataIndex]>=0?'#86efac':'#fca5a5',font:{size:8,weight:'600'},formatter:v=>v.toLocaleString(),rotation:-40,offset:1}},
  ]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:22,bottom:22}},plugins:{legend:{position:'top',labels:{boxWidth:12,padding:16}}},scales:{x:{grid:{color:GC},ticks:{font:{size:10}}},y:{grid:{color:GC},ticks:{callback:v=>v.toLocaleString()+'백만',font:{size:10}}}}}, [ChartDataLabels]);
  mk('cGP','bar',{labels,datasets:[{label:'매출총이익률',data:gpR,backgroundColor:gpR.map(v=>v>=0?'rgba(74,222,128,.7)':'rgba(248,113,113,.7)'),borderRadius:3}]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:18}},plugins:{legend:{display:false},datalabels:{anchor:'end',align:'end',color:ctx=>gpR[ctx.dataIndex]>=0?'#4ade80':'#f87171',font:{size:9,weight:'700'},formatter:v=>v+'%',offset:2}},scales:{x:{grid:{color:GC},ticks:{font:{size:10}}},y:{grid:{color:GC},ticks:{callback:v=>v+'%',font:{size:10}}}}}, [ChartDataLabels]);
  mk('cOP','bar',{labels,datasets:[{label:'영업이익률',data:opR,backgroundColor:opR.map(v=>v>=0?'rgba(96,165,250,.7)':'rgba(248,113,113,.7)'),borderRadius:3}]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:18}},plugins:{legend:{display:false},datalabels:{anchor:'end',align:'end',color:ctx=>opR[ctx.dataIndex]>=0?'#60a5fa':'#f87171',font:{size:9,weight:'700'},formatter:v=>v+'%',offset:2}},scales:{x:{grid:{color:GC},ticks:{font:{size:10}}},y:{grid:{color:GC},ticks:{callback:v=>v+'%',font:{size:10}}}}}, [ChartDataLabels]);

  // 전체 테이블
  const tbl=document.getElementById('tPL'),th='background:var(--sf2);color:var(--sub);padding:8px 12px;text-align:right;border-bottom:1px solid var(--bd);white-space:nowrap;font-size:11px;font-weight:600;';
  let h=`<thead><tr><th style="${th}text-align:left;min-width:130px">과 목</th>`;
  labels.forEach(l=>{h+=`<th style="${th}">${l}</th>`;});
  h+='</tr></thead><tbody>';
  Object.entries(items).forEach(([key,vals])=>{
    const bg=key==='매출총이익'||key==='매출액'?'rgba(99,102,241,.05)':key==='영업이익'?'rgba(74,222,128,.04)':'';
    const isOp=key==='영업이익';
    h+=`<tr style="background:${bg}"><td style="padding:8px 12px;color:var(--tx);font-weight:500">${key}</td>`;
    vals.forEach(v=>{h+=`<td style="padding:8px 12px;text-align:right;color:${isOp?(v>=0?'#4ade80':'#f87171'):'var(--sub)'}">${v<0?'('+Math.abs(v).toLocaleString()+')':v.toLocaleString()}</td>`;});
    h+='</tr>';
  });
  tbl.innerHTML=h+'</tbody>';

  // 월별 드롭다운 미리 준비
  const sel=document.getElementById('plSel');
  sel.innerHTML=months.map((m,i)=>`<option value="${i}" ${i===last?'selected':''}>${m}</option>`).join('');
}

// ══ 월간손익 - 월별 상세 ════════════════════════════════════════════
function buildPlMonth(){
  if(!PL||!PL.items)return;
  const items=PL.items,months=PL.months;
  const si=parseInt(document.getElementById('plSel').value);
  const curM=months[si],pMoI=si>0?si-1:null,pYrI=si>=12?si-12:null;
  const pMoM=pMoI!==null?months[pMoI]:null,pYrM=pYrI!==null?months[pYrI]:null;
  const rev=items['매출액']||[],op=items['영업이익']||[];
  const toR=(o,r)=>r?+(o/r*100).toFixed(1):0;
  const cur_r=rev[si]||0,cur_o=op[si]||0;

  const fD=(cur,prev,isPct)=>{
    if(prev===null)return'';
    const d=isPct?+(cur-prev).toFixed(1):cur-prev;
    if(d===0)return'';
    const c=d>=0?'#4ade80':'#f87171';
    const r=!isPct&&prev?+(d/Math.abs(prev)*100).toFixed(1):null;
    return `<span style="color:${c};font-size:11px"> ${d>=0?'▲':'▼'} ${isPct?Math.abs(d)+'%p':(Math.abs(Math.round(d)).toLocaleString()+(r?' ('+r+'%)':''))}</span>`;
  };

  // KPI 비교 카드
  document.getElementById('plMonKpi').innerHTML=[
    {lb:'매출액',cur:cur_r,pmo:pMoI!==null?rev[pMoI]||0:null,pyr:pYrI!==null?rev[pYrI]||0:null,c:'#60a5fa',isPct:false},
    {lb:'영업이익',cur:cur_o,pmo:pMoI!==null?op[pMoI]||0:null,pyr:pYrI!==null?op[pYrI]||0:null,c:cur_o>=0?'#4ade80':'#f87171',isPct:false},
    {lb:'영업이익률',cur:toR(cur_o,cur_r),pmo:pMoI!==null?toR(op[pMoI]||0,rev[pMoI]||0):null,pyr:pYrI!==null?toR(op[pYrI]||0,rev[pYrI]||0):null,c:cur_o>=0?'#60a5fa':'#f87171',isPct:true},
  ].map(k=>`<div style="background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:20px 24px;border-top:3px solid ${k.c}">
    <div style="font-size:12px;color:var(--sub);margin-bottom:8px">${k.lb}</div>
    <div style="font-size:22px;font-weight:700;color:${k.c};margin-bottom:8px">${k.isPct?k.cur+'%':k.cur.toLocaleString()+' 백만'}</div>
    ${k.pmo!==null?`<div style="font-size:12px;margin-bottom:3px">전월 (${pMoM}): <b>${k.isPct?k.pmo+'%':k.pmo.toLocaleString()}</b>${fD(k.cur,k.pmo,k.isPct)}</div>`:''}
    ${k.pyr!==null?`<div style="font-size:12px">전년동월 (${pYrM}): <b>${k.isPct?k.pyr+'%':k.pyr.toLocaleString()}</b>${fD(k.cur,k.pyr,k.isPct)}</div>`:''}
  </div>`).join('');

  // 비교 테이블
  const f=v=>v<0?`<span class="neg">(${Math.abs(v).toLocaleString()})</span>`:v.toLocaleString();
  const fd=(a,b)=>{if(b===null)return'-';const d=a-b;if(d===0)return'─';const r=b?+(d/Math.abs(b)*100).toFixed(1):null;const c=d>0?'pos':'neg';return`<span class="${c}">${d>0?'▲':'▼'} ${Math.abs(d).toLocaleString()}${r?' ('+r+'%)':''}</span>`;};
  const thS='padding:10px 14px;text-align:right;background:var(--sf2);color:var(--sub);font-size:11px;font-weight:600;border-bottom:1px solid var(--bd);white-space:nowrap;';
  const tdS='padding:9px 14px;text-align:right;border-bottom:1px solid rgba(46,51,80,.4);font-size:13px;white-space:nowrap;';
  const tdL='padding:9px 14px;text-align:left;border-bottom:1px solid rgba(46,51,80,.4);font-size:13px;color:var(--tx);font-weight:500;white-space:nowrap;';
  let h=`<table style="width:100%;border-collapse:collapse"><thead><tr>
    <th style="${thS}text-align:left;min-width:150px">항 목</th>
    <th style="${thS}">${curM}<br><span style="font-weight:400">당월</span></th>`;
  if(pMoM)h+=`<th style="${thS}">${pMoM}<br><span style="font-weight:400">전월</span></th><th style="${thS}color:#fbbf24">전월 대비</th>`;
  if(pYrM)h+=`<th style="${thS}">${pYrM}<br><span style="font-weight:400">전년동월</span></th><th style="${thS}color:#a78bfa">전년 대비</th>`;
  h+=`</tr></thead><tbody>`;
  const BOLDS=['매출액','매출총이익','영업이익'];
  Object.keys(items).forEach(key=>{
    const vals=items[key],cur=vals[si]||0;
    const pMo=pMoI!==null?vals[pMoI]||0:null,pYr=pYrI!==null?vals[pYrI]||0:null;
    const isBold=BOLDS.includes(key),isOp=key==='영업이익';
    const bg=isOp?'rgba(74,222,128,.04)':isBold?'rgba(99,102,241,.05)':'';
    h+=`<tr style="background:${bg}">
      <td style="${tdL}${isBold?'font-weight:700':''}">${key}</td>
      <td style="${tdS}color:${cur<0?'var(--rd)':'var(--tx)'}${isBold?';font-weight:700':''}">${f(cur)}</td>`;
    if(pMoM!==null)h+=`<td style="${tdS}color:var(--sub)">${f(pMo)}</td><td style="${tdS}">${fd(cur,pMo)}</td>`;
    if(pYrM!==null)h+=`<td style="${tdS}color:var(--sub)">${f(pYr)}</td><td style="${tdS}">${fd(cur,pYr)}</td>`;
    h+='</tr>';
  });
  // 영업이익률
  if(rev.length&&op.length){
    const crR=rev[si]?+(op[si]/rev[si]*100).toFixed(1):null;
    const pmR=pMoI!==null&&rev[pMoI]?+(op[pMoI]/rev[pMoI]*100).toFixed(1):null;
    const pyR=pYrI!==null&&rev[pYrI]?+(op[pYrI]/rev[pYrI]*100).toFixed(1):null;
    const fRa=(v,p)=>{if(p===null||v===null)return'-';const d=+(v-p).toFixed(1);if(d===0)return'─';return`<span class="${d>0?'pos':'neg'}">${d>0?'▲':'▼'} ${Math.abs(d)}%p</span>`;};
    h+=`<tr style="background:rgba(99,102,241,.05)"><td style="${tdL}color:var(--sub)"> 영업이익률</td>
      <td style="${tdS}color:${crR>=0?'var(--gr)':'var(--rd)'};font-weight:700">${crR!==null?crR+'%':'-'}</td>`;
    if(pMoM!==null)h+=`<td style="${tdS}color:${pmR>=0?'var(--gr)':'var(--rd)'}">${pmR!==null?pmR+'%':'-'}</td><td style="${tdS}">${fRa(crR,pmR)}</td>`;
    if(pYrM!==null)h+=`<td style="${tdS}color:${pyR>=0?'var(--gr)':'var(--rd)'}">${pyR!==null?pyR+'%':'-'}</td><td style="${tdS}">${fRa(crR,pyR)}</td>`;
    h+='</tr>';
  }
  document.getElementById('plMonTbl').innerHTML=h+'</tbody></table>';

  // 비교 막대차트
  if(document.getElementById('plCT1'))document.getElementById('plCT1').textContent=`매출액 비교 (${curM})`;
  if(document.getElementById('plCT2'))document.getElementById('plCT2').textContent=`영업이익 비교 (${curM})`;
  const labs=[],rD=[],oD=[],bR=[],bO=[];
  if(pYrM){labs.push(pYrM);rD.push(rev[pYrI]||0);oD.push(op[pYrI]||0);bR.push('rgba(167,139,250,.5)');bO.push((op[pYrI]||0)>=0?'rgba(167,139,250,.5)':'rgba(248,113,113,.4)');}
  if(pMoM){labs.push(pMoM);rD.push(rev[pMoI]||0);oD.push(op[pMoI]||0);bR.push('rgba(96,165,250,.5)');bO.push((op[pMoI]||0)>=0?'rgba(96,165,250,.5)':'rgba(248,113,113,.4)');}
  labs.push(curM+' ★');rD.push(rev[si]||0);oD.push(op[si]||0);bR.push('rgba(74,222,128,.85)');bO.push((op[si]||0)>=0?'rgba(74,222,128,.85)':'rgba(248,113,113,.85)');
  mk('cPC1','bar',{labels:labs,datasets:[{data:rD,backgroundColor:bR,borderRadius:6}]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:24}},plugins:{legend:{display:false},datalabels:{anchor:'end',align:'end',color:'#e2e8f0',font:{size:11,weight:'700'},formatter:v=>v.toLocaleString()+'백만',offset:2}},scales:{x:{grid:{color:GC}},y:{grid:{color:GC},ticks:{callback:v=>v.toLocaleString()+'백만',font:{size:11}}}}}, [ChartDataLabels]);
  mk('cPC2','bar',{labels:labs,datasets:[{data:oD,backgroundColor:bO,borderRadius:6}]},{responsive:true,maintainAspectRatio:false,layout:{padding:{top:24}},plugins:{legend:{display:false},datalabels:{anchor:'end',align:'end',color:ctx=>oD[ctx.dataIndex]>=0?'#4ade80':'#f87171',font:{size:11,weight:'700'},formatter:v=>v.toLocaleString()+'백만',offset:2}},scales:{x:{grid:{color:GC}},y:{grid:{color:GC},ticks:{callback:v=>v.toLocaleString()+'백만',font:{size:11}}}}}, [ChartDataLabels]);
}

init();
</script>
</body>
</html>""")

html = ''.join(html_parts)
print(f'HTML: {len(html):,} chars')

# JS 검증
scripts = re.findall(r'<script(?! src)[^>]*>([\s\S]*?)</script>', html)
main_js = scripts[-1]
print(f'스크립트 블록: {len(scripts)}개')
stub = 'const Chart={defaults:{color:"",font:{family:""}}};const document={getElementById:()=>({style:{},textContent:"",innerHTML:"",classList:{add:()=>{},remove:()=>{}},value:"0",options:[],length:0}),querySelectorAll:()=>[{classList:{add:()=>{},remove:()=>{}}}],addEventListener:()=>{},createElement:()=>({className:"",innerHTML:"",onclick:null,appendChild:()=>{}})};const window={};const sessionStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};const localStorage={getItem:()=>null,setItem:()=>{}};const location={href:"https://t.com",reload:()=>{}};'
import tempfile as _tf_mod
tmp = os.path.join(_tf_mod.gettempdir(), 'ff.mjs')
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(stub + main_js)
r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True, encoding='utf-8', errors='replace')
if r.returncode != 0:
    err = (r.stderr or r.stdout or 'unknown error')[:300]
    print('❌', err)
    sys.exit(1)
print('✅ JS OK')

req = urllib.request.Request('https://api.github.com/repos/309hsh/pharma-bros-weekly/contents/index.html', headers=GH)
with urllib.request.urlopen(req) as r2:
    sha = json.loads(r2.read())['sha']
b64 = base64.b64encode(html.encode('utf-8')).decode('ascii')
body = json.dumps({'message': '월간손익 서브탭 완전 새로 작성 v2', 'content': b64, 'sha': sha}).encode('utf-8')
req2 = urllib.request.Request('https://api.github.com/repos/309hsh/pharma-bros-weekly/contents/index.html', data=body, method='PUT', headers=GH)
with urllib.request.urlopen(req2) as r3:
    print(f'업로드: {r3.status}')
print('완료! https://309hsh.github.io/pharma-bros-weekly')
