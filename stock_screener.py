#!/usr/bin/env python3
"""
한국 증시 종합 스크리닝 시스템
- 턴어라운드 (연간실적호전)
- 외국인/기관 동반 순매수 전환
- 국민연금 보유현황
3개 지표를 종합하여 점수화하는 웹 기반 스크리닝 시스템
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import re
import os

# ============================================================
# 1. 데이터 수집
# ============================================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://comp.fnguide.com/SVO/WooriRenewal/',
}


def normalize_stock_name(name):
    """종목명 정규화: 공백, 특수문자 통일"""
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)  # 다중 공백 → 단일 공백
    return name


def parse_table(table, exclude_last_col=True):
    """BeautifulSoup table 요소 → list of dicts"""
    headers = []
    for th in table.find('thead').find_all('th'):
        text = th.get_text(separator=' ', strip=True)
        headers.append(text)

    if exclude_last_col and headers and 'Action' in headers[-1]:
        headers = headers[:-1]

    rows = []
    for tr in table.find('tbody').find_all('tr'):
        tds = tr.find_all('td')
        row = {}
        for i, td in enumerate(tds):
            if i >= len(headers):
                break
            row[headers[i]] = td.get_text(strip=True)
        if row:
            rows.append(row)
    return rows, headers


def fetch_turnaround():
    """1. 턴어라운드 - 연간실적호전 종목 크롤링"""
    url = 'https://comp.fnguide.com/SVO/WooriRenewal/ScreenerBasics_turn.asp'
    print("[1/3] 턴어라운드(연간실적호전) 데이터 수집 중...")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'html.parser')

    # grid_A = 연간실적호전 테이블
    grid_a = soup.find('div', id='grid_A')
    if not grid_a:
        print("  ⚠ grid_A를 찾을 수 없습니다.")
        return pd.DataFrame()

    table = grid_a.find('table')
    rows, headers = parse_table(table)

    df = pd.DataFrame(rows)
    if '종목명' in df.columns:
        df['종목명'] = df['종목명'].apply(normalize_stock_name)

    print(f"  ✓ {len(df)}개 종목 수집 완료")
    return df


def fetch_supply_trend():
    """2. 외국인/기관 동반 순매수 전환 종목 크롤링"""
    url = 'https://comp.fnguide.com/SVO/WooriRenewal/SupplyTrend.asp'
    print("[2/3] 외국인/기관 동반 순매수 전환 데이터 수집 중...")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'html.parser')

    # tbl_2 = 외국인/기관 동반 순매수 전환 테이블
    tbl_2 = soup.find('div', id='tbl_2')
    if not tbl_2:
        print("  ⚠ tbl_2를 찾을 수 없습니다.")
        return pd.DataFrame()

    table = tbl_2.find('table')
    rows, headers = parse_table(table)

    df = pd.DataFrame(rows)
    if '종목명' in df.columns:
        df['종목명'] = df['종목명'].apply(normalize_stock_name)

    print(f"  ✓ {len(df)}개 종목 수집 완료")
    return df


def fetch_nps_holdings():
    """3. 국민연금공단 보유현황 크롤링"""
    url = 'https://comp.fnguide.com/SVO/WooriRenewal/inst.asp'
    print("[3/3] 국민연금공단 보유현황 데이터 수집 중...")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'html.parser')

    # 기관투자자 보유현황 테이블 (기본이 국민연금공단)
    table = soup.find('table', class_='ctb1')
    if not table:
        print("  ⚠ 테이블을 찾을 수 없습니다.")
        return pd.DataFrame()

    # 헤더 파싱
    headers = []
    thead = table.find('thead')
    if thead:
        # nested thead structure 처리
        ths = thead.find_all('th')
        for th in ths:
            text = th.get_text(separator=' ', strip=True)
            if text and text != '':
                headers.append(text)

    # 'Action' 제외
    if headers and 'Action' in headers[-1]:
        headers = headers[:-1]

    # tbody가 없을 수 있음 - tr 직접 탐색
    rows = []
    all_trs = table.find_all('tr')
    for tr in all_trs:
        tds = tr.find_all('td')
        if not tds:
            continue
        row = {}
        for i, td in enumerate(tds):
            if i >= len(headers):
                break
            row[headers[i]] = td.get_text(strip=True)
        if row:
            rows.append(row)

    df = pd.DataFrame(rows)
    if '종목명' in df.columns:
        df['종목명'] = df['종목명'].apply(normalize_stock_name)

    print(f"  ✓ {len(df)}개 종목 수집 완료")
    return df


# ============================================================
# 2. 점수 계산
# ============================================================

def calculate_scores(df_turn, df_supply, df_nps):
    """3개 데이터셋 기반 종합 점수 계산"""
    print("\n점수 계산 중...")

    stocks_turn = set(df_turn['종목명'].tolist()) if '종목명' in df_turn.columns else set()
    stocks_supply = set(df_supply['종목명'].tolist()) if '종목명' in df_supply.columns else set()
    stocks_nps = set(df_nps['종목명'].tolist()) if '종목명' in df_nps.columns else set()

    all_stocks = stocks_turn | stocks_supply | stocks_nps

    results = []
    for stock in all_stocks:
        score = 0
        sources = []

        in_turn = stock in stocks_turn
        in_supply = stock in stocks_supply
        in_nps = stock in stocks_nps

        if in_turn:
            score += 1
            sources.append('연간실적호전')
        if in_supply:
            score += 1
            sources.append('순매수전환')
        if in_nps:
            score += 1
            sources.append('국민연금')

        # 각 소스별 상세 정보 수집
        detail = {'종목명': stock, '종합점수': score, '출처': ', '.join(sources)}

        # 턴어라운드 상세
        if in_turn:
            row = df_turn[df_turn['종목명'] == stock].iloc[0]
            for col in df_turn.columns:
                if col not in ('No.', '종목명'):
                    detail[f'[턴]{col}'] = row.get(col, '')

        # 수급 상세
        if in_supply:
            row = df_supply[df_supply['종목명'] == stock].iloc[0]
            for col in df_supply.columns:
                if col not in ('No.', '종목명'):
                    detail[f'[수급]{col}'] = row.get(col, '')

        # 국민연금 상세
        if in_nps:
            row = df_nps[df_nps['종목명'] == stock].iloc[0]
            for col in df_nps.columns:
                if col not in ('No.', '종목명'):
                    detail[f'[연금]{col}'] = row.get(col, '')

        results.append(detail)

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values(['종합점수', '종목명'], ascending=[False, True]).reset_index(drop=True)
    result_df.index = result_df.index + 1  # 1부터 시작

    count_3 = len(result_df[result_df['종합점수'] == 3])
    count_2 = len(result_df[result_df['종합점수'] == 2])
    count_1 = len(result_df[result_df['종합점수'] == 1])
    print(f"  3점: {count_3}개 | 2점: {count_2}개 | 1점: {count_1}개 | 총: {len(result_df)}개")

    return result_df, {
        'turn_count': len(stocks_turn),
        'supply_count': len(stocks_supply),
        'nps_count': len(stocks_nps),
        'total': len(all_stocks),
        'score_3': count_3,
        'score_2': count_2,
        'score_1': count_1,
    }


# ============================================================
# 3. HTML 생성
# ============================================================

def generate_html(result_df, df_turn, df_supply, df_nps, stats, output_path):
    """결과를 단일 HTML 파일로 생성"""
    print(f"\nHTML 파일 생성 중: {output_path}")

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 메인 테이블 HTML
    main_rows_html = ""
    for idx, row in result_df.iterrows():
        score = row['종합점수']
        if score == 3:
            row_class = 'score-3'
        elif score == 2:
            row_class = 'score-2'
        else:
            row_class = 'score-1'

        badge = f'<span class="badge badge-{score}">{score}점</span>'
        sources_html = ''
        for src in row['출처'].split(', '):
            if src == '연간실적호전':
                sources_html += '<span class="tag tag-turn">연간실적호전</span> '
            elif src == '순매수전환':
                sources_html += '<span class="tag tag-supply">순매수전환</span> '
            elif src == '국민연금':
                sources_html += '<span class="tag tag-nps">국민연금</span> '

        # 상세 정보 구성
        detail_parts = []
        for col in result_df.columns:
            if col.startswith('[턴]') and pd.notna(row.get(col)) and row.get(col, '') != '':
                label = col.replace('[턴]', '')
                detail_parts.append(f'<span class="detail-item turn">{label}: {row[col]}</span>')
            elif col.startswith('[수급]') and pd.notna(row.get(col)) and row.get(col, '') != '':
                label = col.replace('[수급]', '')
                detail_parts.append(f'<span class="detail-item supply">{label}: {row[col]}</span>')
            elif col.startswith('[연금]') and pd.notna(row.get(col)) and row.get(col, '') != '':
                label = col.replace('[연금]', '')
                detail_parts.append(f'<span class="detail-item nps">{label}: {row[col]}</span>')

        details_html = ' '.join(detail_parts)

        main_rows_html += f"""
        <tr class="{row_class}" data-score="{score}">
            <td class="center">{idx}</td>
            <td class="stock-name"><strong>{row['종목명']}</strong></td>
            <td class="center">{badge}</td>
            <td>{sources_html}</td>
            <td class="details">{details_html}</td>
        </tr>"""

    # 개별 데이터셋 테이블 생성 함수
    def make_sub_table(df, table_id):
        if df.empty:
            return '<p>데이터 없음</p>'
        cols = [c for c in df.columns if c != 'No.']
        header = ''.join(f'<th>{c}</th>' for c in cols)
        rows = ''
        for _, row in df.iterrows():
            cells = ''.join(f'<td>{row.get(c, "")}</td>' for c in cols)
            rows += f'<tr>{cells}</tr>'
        return f"""<table id="{table_id}" class="sub-table">
            <thead><tr>{header}</tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    turn_table = make_sub_table(df_turn, 'turn-table')
    supply_table = make_sub_table(df_supply, 'supply-table')
    nps_table = make_sub_table(df_nps, 'nps-table')

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>한국 증시 종합 스크리닝</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
            background: #f0f2f5;
            color: #1a1a2e;
            line-height: 1.6;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

        /* Header */
        .header {{
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: white;
            padding: 30px 40px;
            border-radius: 16px;
            margin-bottom: 24px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.8; font-size: 14px; }}

        /* Stats Cards */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            transition: transform 0.2s;
        }}
        .stat-card:hover {{ transform: translateY(-2px); }}
        .stat-card .number {{ font-size: 32px; font-weight: 700; }}
        .stat-card .label {{ font-size: 13px; color: #666; margin-top: 4px; }}
        .stat-card.highlight {{ border-left: 4px solid #22c55e; }}
        .stat-card.s3 .number {{ color: #16a34a; }}
        .stat-card.s2 .number {{ color: #d97706; }}
        .stat-card.s1 .number {{ color: #6b7280; }}

        /* Filter */
        .filter-bar {{
            background: white;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 20px;
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }}
        .filter-bar label {{ font-weight: 600; font-size: 14px; }}
        .filter-btn {{
            padding: 8px 16px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            background: white;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }}
        .filter-btn:hover {{ border-color: #302b63; }}
        .filter-btn.active {{ background: #302b63; color: white; border-color: #302b63; }}
        .search-input {{
            padding: 8px 16px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            font-size: 14px;
            min-width: 200px;
            outline: none;
        }}
        .search-input:focus {{ border-color: #302b63; }}

        /* Tab navigation */
        .tab-nav {{
            display: flex;
            gap: 4px;
            margin-bottom: 0;
            background: white;
            border-radius: 12px 12px 0 0;
            padding: 8px 8px 0;
            box-shadow: 0 -2px 8px rgba(0,0,0,0.04);
        }}
        .tab-btn {{
            padding: 12px 24px;
            border: none;
            background: transparent;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            color: #666;
            border-radius: 8px 8px 0 0;
            transition: all 0.2s;
        }}
        .tab-btn:hover {{ color: #302b63; background: #f8f9fa; }}
        .tab-btn.active {{ color: #302b63; background: #f0f2f5; border-bottom: 3px solid #302b63; }}

        /* Tables */
        .table-container {{
            background: white;
            border-radius: 0 0 12px 12px;
            overflow-x: auto;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        thead {{ background: #f8f9fa; position: sticky; top: 0; z-index: 10; }}
        th {{
            padding: 14px 16px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
            white-space: nowrap;
        }}
        td {{
            padding: 12px 16px;
            border-bottom: 1px solid #f3f4f6;
        }}
        tr:hover {{ background: #f8fafc; }}
        .center {{ text-align: center; }}

        /* Score colors */
        .score-3 {{ background: #f0fdf4; }}
        .score-3:hover {{ background: #dcfce7 !important; }}
        .score-2 {{ background: #fffbeb; }}
        .score-2:hover {{ background: #fef3c7 !important; }}
        .score-1 {{ background: white; }}

        /* Badges & Tags */
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 13px;
        }}
        .badge-3 {{ background: #dcfce7; color: #16a34a; }}
        .badge-2 {{ background: #fef3c7; color: #d97706; }}
        .badge-1 {{ background: #f3f4f6; color: #6b7280; }}

        .tag {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            margin: 1px;
        }}
        .tag-turn {{ background: #dbeafe; color: #2563eb; }}
        .tag-supply {{ background: #fce7f3; color: #db2777; }}
        .tag-nps {{ background: #d1fae5; color: #059669; }}

        .stock-name {{ white-space: nowrap; }}

        .details {{ font-size: 12px; }}
        .detail-item {{
            display: inline-block;
            padding: 2px 6px;
            margin: 2px;
            border-radius: 4px;
            font-size: 11px;
            white-space: nowrap;
        }}
        .detail-item.turn {{ background: #eff6ff; color: #1d4ed8; }}
        .detail-item.supply {{ background: #fff1f2; color: #be123c; }}
        .detail-item.nps {{ background: #ecfdf5; color: #047857; }}

        /* Sub tables */
        .sub-table {{ font-size: 13px; }}
        .sub-table th {{ background: #f1f5f9; font-size: 13px; padding: 10px 12px; }}
        .sub-table td {{ padding: 8px 12px; }}

        .footer {{
            text-align: center;
            padding: 20px;
            color: #9ca3af;
            font-size: 12px;
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            .container {{ padding: 12px; }}
            .header {{ padding: 20px; }}
            .header h1 {{ font-size: 20px; }}
            .stats-grid {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
            .filter-bar {{ flex-direction: column; }}
            .search-input {{ min-width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>한국 증시 종합 스크리닝 시스템</h1>
            <p>턴어라운드(연간실적호전) + 외국인/기관 동반 순매수 전환 + 국민연금 보유 | 데이터 수집: {now}</p>
        </div>

        <div class="stats-grid">
            <div class="stat-card s3 highlight">
                <div class="number">{stats['score_3']}</div>
                <div class="label">3점 (3개 모두 해당)</div>
            </div>
            <div class="stat-card s2">
                <div class="number">{stats['score_2']}</div>
                <div class="label">2점 (2개 해당)</div>
            </div>
            <div class="stat-card s1">
                <div class="number">{stats['score_1']}</div>
                <div class="label">1점 (1개 해당)</div>
            </div>
            <div class="stat-card">
                <div class="number">{stats['turn_count']}</div>
                <div class="label">연간실적호전</div>
            </div>
            <div class="stat-card">
                <div class="number">{stats['supply_count']}</div>
                <div class="label">순매수전환</div>
            </div>
            <div class="stat-card">
                <div class="number">{stats['nps_count']}</div>
                <div class="label">국민연금 보유</div>
            </div>
        </div>

        <div class="filter-bar">
            <label>점수 필터:</label>
            <button class="filter-btn active" onclick="filterScore('all')">전체</button>
            <button class="filter-btn" onclick="filterScore(3)">3점</button>
            <button class="filter-btn" onclick="filterScore(2)">2점 이상</button>
            <button class="filter-btn" onclick="filterScore(1)">1점 이상</button>
            <input type="text" class="search-input" placeholder="종목명 검색..." oninput="searchStock(this.value)">
        </div>

        <div class="tab-nav">
            <button class="tab-btn active" onclick="showTab('main')">종합 결과</button>
            <button class="tab-btn" onclick="showTab('turn')">연간실적호전 ({stats['turn_count']})</button>
            <button class="tab-btn" onclick="showTab('supply')">순매수전환 ({stats['supply_count']})</button>
            <button class="tab-btn" onclick="showTab('nps')">국민연금 ({stats['nps_count']})</button>
        </div>

        <div class="table-container">
            <div id="tab-main" class="tab-content active">
                <table id="main-table">
                    <thead>
                        <tr>
                            <th style="width:50px" class="center">No.</th>
                            <th style="width:140px">종목명</th>
                            <th style="width:80px" class="center">점수</th>
                            <th style="width:200px">해당 항목</th>
                            <th>상세 정보</th>
                        </tr>
                    </thead>
                    <tbody>
                        {main_rows_html}
                    </tbody>
                </table>
            </div>
            <div id="tab-turn" class="tab-content">
                <h3 style="padding: 16px 16px 0; color: #2563eb;">연간실적호전 종목 (단위: 억원, 배)</h3>
                {turn_table}
            </div>
            <div id="tab-supply" class="tab-content">
                <h3 style="padding: 16px 16px 0; color: #db2777;">외국인/기관 동반 순매수 전환 종목</h3>
                {supply_table}
            </div>
            <div id="tab-nps" class="tab-content">
                <h3 style="padding: 16px 16px 0; color: #059669;">국민연금공단 보유 종목</h3>
                {nps_table}
            </div>
        </div>

        <div class="footer">
            <p>데이터 출처: FnGuide (comp.fnguide.com) | 본 자료는 투자 참고용이며, 투자의 최종 책임은 투자자 본인에게 있습니다.</p>
        </div>
    </div>

    <script>
        function filterScore(minScore) {{
            const rows = document.querySelectorAll('#main-table tbody tr');
            const btns = document.querySelectorAll('.filter-btn');
            btns.forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');

            rows.forEach(row => {{
                const score = parseInt(row.dataset.score);
                if (minScore === 'all') {{
                    row.style.display = '';
                }} else {{
                    row.style.display = score >= minScore ? '' : 'none';
                }}
            }});
        }}

        function searchStock(query) {{
            const rows = document.querySelectorAll('#main-table tbody tr');
            const q = query.trim().toLowerCase();
            rows.forEach(row => {{
                const name = row.querySelector('.stock-name').textContent.toLowerCase();
                row.style.display = name.includes(q) ? '' : 'none';
            }});
        }}

        function showTab(tabName) {{
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');
        }}
    </script>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✓ HTML 파일 생성 완료: {output_path}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 60)
    print("  한국 증시 종합 스크리닝 시스템")
    print("=" * 60)
    print()

    # 데이터 수집
    df_turn = fetch_turnaround()
    df_supply = fetch_supply_trend()
    df_nps = fetch_nps_holdings()

    if df_turn.empty and df_supply.empty and df_nps.empty:
        print("\n❌ 데이터를 수집하지 못했습니다.")
        return

    # 점수 계산
    result_df, stats = calculate_scores(df_turn, df_supply, df_nps)

    # HTML 생성
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_screening_result.html')
    generate_html(result_df, df_turn, df_supply, df_nps, stats, output_path)

    # 결과 요약 출력
    print("\n" + "=" * 60)
    print("  결과 요약")
    print("=" * 60)

    if stats['score_3'] > 0:
        print(f"\n★ 3점 종목 (3개 항목 모두 해당):")
        for _, row in result_df[result_df['종합점수'] == 3].iterrows():
            print(f"  - {row['종목명']}")

    if stats['score_2'] > 0:
        print(f"\n● 2점 종목 (2개 항목 해당):")
        for _, row in result_df[result_df['종합점수'] == 2].iterrows():
            print(f"  - {row['종목명']} ({row['출처']})")

    print(f"\n결과 파일: {output_path}")


if __name__ == '__main__':
    main()
