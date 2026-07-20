#!/usr/bin/env python3
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

DATA = Path("data/apt-listings.json")
OUT = Path("docs/index.html")


def esc(value):
    return html.escape(str(value or ""))


def money(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{v / 10000:.2f}억" if v >= 10000 else f"{v:,}만"


def main():
    listings = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    listings.sort(key=lambda x: (x.get("status", ""), x.get("price", 0), x.get("articleNo", "")))
    counts = Counter(x.get("status", "UNKNOWN") for x in listings)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    for x in listings:
        status = x.get("status", "UNKNOWN")
        url = esc(x.get("url", "#"))
        rows.append(f"""
        <tr>
          <td><span class="badge {status.lower()}">{esc(status)}</span></td>
          <td>{money(x.get('price'))}</td>
          <td>{esc(x.get('buildingName'))}</td>
          <td>{esc(x.get('floor'))}</td>
          <td>{esc(x.get('areaExclusiveSqm'))}㎡</td>
          <td>{esc(x.get('featureDesc'))}</td>
          <td>{esc(x.get('firstSeenAt'))}</td>
          <td>{esc(x.get('lastSeenAt'))}</td>
          <td>{esc(x.get('missCount', 0))}</td>
          <td><a href="{url}" target="_blank" rel="noopener">매물 보기</a></td>
        </tr>""")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>대방현대1차 매물 추적</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#202124}}
main{{max-width:1500px;margin:auto;padding:28px}} h1{{margin-bottom:6px}} .sub{{color:#687078;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:20px 0}}
.card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 1px 4px #0001}} .num{{font-size:28px;font-weight:700}}
.table-wrap{{overflow:auto;background:#fff;border-radius:14px;box-shadow:0 1px 4px #0001}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:11px 12px;border-bottom:1px solid #eceff1;text-align:left;white-space:nowrap}} th{{background:#fafbfc;position:sticky;top:0}}
.badge{{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700}} .active{{background:#e8f5e9}} .relisted{{background:#e3f2fd}} .off_market_candidate{{background:#fff3e0}} .off_market{{background:#ffebee}}
a{{color:#1769aa;text-decoration:none}} .note{{margin-top:16px;color:#687078;font-size:13px;line-height:1.6}}
@media(max-width:800px){{.cards{{grid-template-columns:repeat(2,1fr)}}main{{padding:16px}}}}
</style></head><body><main>
<h1>대방현대1차 매물 추적</h1><div class="sub">마지막 생성: {now} · 네이버 부동산 노출 상태 기반</div>
<div class="cards">
<div class="card"><div>현재 노출</div><div class="num">{counts['ACTIVE'] + counts['RELISTED']}</div></div>
<div class="card"><div>재등록</div><div class="num">{counts['RELISTED']}</div></div>
<div class="card"><div>삭제 후보</div><div class="num">{counts['OFF_MARKET_CANDIDATE']}</div></div>
<div class="card"><div>거래종결 추정</div><div class="num">{counts['OFF_MARKET']}</div></div>
</div>
<div class="table-wrap"><table><thead><tr><th>상태</th><th>가격</th><th>동</th><th>층</th><th>전용</th><th>특징</th><th>최초 확인</th><th>마지막 확인</th><th>미노출 횟수</th><th>링크</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<div class="note">OFF_MARKET은 네이버 부동산에서 연속 3회 보이지 않았다는 뜻이며 실제 매매 완료를 확정하지 않습니다. 계약 취소, 중개사 철회, 가격 수정 후 재등록도 포함될 수 있습니다.</div>
</main></body></html>""", encoding="utf-8")


if __name__ == "__main__":
    main()
