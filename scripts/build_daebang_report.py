#!/usr/bin/env python3
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DATA = Path("data/apt-listings.json")
OUT = Path("docs/index.html")
TARGET_COMPLEX_NO = "368"

# 같은 집을 중개사 두 곳이 각각 올리거나, 층 표기가 "15층"에서 "중층"으로 바뀌어
# 재등록되면 매물번호가 달라 별건으로 쌓인다. 설명 유사도로 이를 묶는다.
DESC_SIMILAR = 0.55
BAND_OF = {"저": "L", "중": "M", "고": "H"}


def esc(value):
    return html.escape(str(value or ""))


def money(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{v / 10000:.2f}억" if v >= 10000 else f"{v:,}만"


def when(value):
    """2026-08-02T13:33:31.644797 -> 2026-08-02 13:33"""
    text = str(value or "")
    return text[:16].replace("T", " ") if len(text) >= 16 else text


def parse_floor(raw):
    """'15/23' -> (15, 23), '중/23' -> ('M', 23)"""
    m = re.match(r"\s*([0-9]+|저|중|고)\s*/\s*([0-9]+)", raw or "")
    if not m:
        return (None, None)
    low, total = m.group(1), int(m.group(2))
    return ((int(low) if low.isdigit() else BAND_OF[low]), total)


def band_span(band, total):
    """저/중/고가 가리키는 실제 층 범위. 경계는 ±1 여유를 둔다."""
    third, two_thirds = round(total / 3), round(2 * total / 3)
    return {"L": (1, third + 1), "M": (third, two_thirds + 1), "H": (two_thirds, total)}[band]


def band_of(floor, total):
    """정확한 층을 저/중/고 하나로 환산(겹침 없이)."""
    if floor is None:
        return None
    if isinstance(floor, str):
        return floor
    third, two_thirds = round(total / 3), round(2 * total / 3)
    return "L" if floor <= third else ("M" if floor <= two_thirds else "H")


def floor_compatible(a, b):
    fa, ta = a
    fb, tb = b
    if fa is None or fb is None or ta != tb:
        return False
    if isinstance(fa, int) and isinstance(fb, int):
        return fa == fb
    if isinstance(fa, str) and isinstance(fb, str):
        return fa == fb
    num, band = (fa, fb) if isinstance(fa, int) else (fb, fa)
    low, high = band_span(band, ta)
    return low <= num <= high


def bigrams(raw):
    text = re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", raw or "").lower())
    return {text[i:i + 2] for i in range(len(text) - 1)}


def desc_similarity(a, b):
    """짧은 쪽이 긴 쪽에 얼마나 포함되는지. 중개사마다 문구를 덧붙이므로 교집합 기준."""
    ga, gb = bigrams(a), bigrams(b)
    if len(ga) < 4 or len(gb) < 4:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def identity(x):
    return (
        x.get("buildingName") or "",
        round(x.get("areaExclusiveSqm") or 0, 1),
        round(x.get("areaSupplySqm") or 0, 1),
    )


def same_home(a, b):
    """같은 동·같은 면적·층 호환을 전제로, 동일 매물로 볼 근거가 있는지."""
    if identity(a) != identity(b):
        return False
    fa, fb = parse_floor(a.get("floor")), parse_floor(b.get("floor"))
    if not floor_compatible(fa, fb):
        return False
    if desc_similarity(a.get("featureDesc"), b.get("featureDesc")) >= DESC_SIMILAR:
        return True
    # 한 동의 같은 층·같은 평형은 보통 한 세대뿐이라, 정확한 층이 맞으면 근거가 충분하다.
    return isinstance(fa[0], int) and fa[0] == fb[0] and a.get("price") == b.get("price")


def build_clusters(listings):
    """동일 매물로 추정되는 묶음들. 2건 이상 묶인 것만 반환."""
    parent = {x["articleNo"]: x["articleNo"] for x in listings}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for i, a in enumerate(listings):
        for b in listings[i + 1:]:
            if same_home(a, b):
                ra, rb = find(a["articleNo"]), find(b["articleNo"])
                if ra != rb:
                    parent[ra] = rb

    members = defaultdict(list)
    for x in listings:
        members[find(x["articleNo"])].append(x)
    return [group for group in members.values() if len(group) > 1]


def find_lookalikes(listings, clustered):
    """묶을 근거는 부족하지만 조건이 겹쳐 눈으로 확인할 만한 2건 짝."""
    buckets = defaultdict(list)
    for x in listings:
        if x["articleNo"] in clustered or len(bigrams(x.get("featureDesc"))) < 4:
            continue
        band = band_of(*parse_floor(x.get("floor")))
        if band is None:
            continue
        buckets[identity(x) + (band, x.get("price"))].append(x)
    # 조건이 겹치는 매물이 3건 이상이면 비슷한 세대가 많을 뿐이므로 제외한다.
    return [group for group in buckets.values() if len(group) == 2]


STATUS_ORDER = {"ACTIVE": 0, "RELISTED": 1, "OFF_MARKET_CANDIDATE": 2, "OFF_MARKET": 3}


def sort_key(x):
    return (STATUS_ORDER.get(x.get("status"), 9), x.get("price", 0), x.get("articleNo", ""))


CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#202124}
main{max-width:1500px;margin:auto;padding:28px} h1{margin-bottom:6px} .sub{color:#687078;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;margin:20px 0}
.card{background:#fff;padding:18px;border-radius:14px;box-shadow:0 1px 4px #0001} .num{font-size:28px;font-weight:700}
.card .hint{color:#687078;font-size:12px;margin-top:2px}
.table-wrap{overflow:auto;background:#fff;border-radius:14px;box-shadow:0 1px 4px #0001}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:11px 12px;border-bottom:1px solid #eceff1;text-align:left;white-space:nowrap}
th{background:#fafbfc;position:sticky;top:0}
.badge{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700}
.active{background:#e8f5e9} .relisted{background:#e3f2fd}
.off_market_candidate{background:#fff3e0} .off_market{background:#ffebee}
.grp{padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}
.grp.maybe{background:#eceff1;color:#546e7a}
.g0{background:#ede7f6;color:#4527a0} .g1{background:#e0f2f1;color:#00695c} .g2{background:#fff8e1;color:#a1620a}
.g3{background:#fce4ec;color:#ad1457} .g4{background:#e1f5fe;color:#01579b} .g5{background:#f1f8e9;color:#33691e}
tr.grouped td{background:#fbfaff} tr.grouped.g1 td{background:#f7fcfb} tr.grouped.g2 td{background:#fffdf5}
tr.grouped.g3 td{background:#fdf6f9} tr.grouped.g4 td{background:#f6fcff} tr.grouped.g5 td{background:#f9fcf5}
tr.grouped td:first-child{border-left:3px solid #b0bec5}
a{color:#1769aa;text-decoration:none} .note{margin-top:16px;color:#687078;font-size:13px;line-height:1.6}
.dups{margin:8px 0 0;padding-left:18px} .dups li{margin:3px 0}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}main{padding:16px}}
"""


def main():
    all_listings = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    listings = [x for x in all_listings if str(x.get("hscpNo", "")) == TARGET_COMPLEX_NO]
    counts = Counter(x.get("status", "UNKNOWN") for x in listings)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    clusters = sorted(build_clusters(listings), key=lambda g: min(sort_key(x) for x in g))
    labels = {}
    for idx, group in enumerate(clusters):
        label = chr(ord("A") + idx) if idx < 26 else f"#{idx + 1}"
        for x in group:
            labels[x["articleNo"]] = (label, len(group), idx % 6)

    lookalikes = find_lookalikes(listings, labels)
    lookalike_ids = {x["articleNo"] for pair in lookalikes for x in pair}

    live_listings = [x for x in listings if x.get("status") in ("ACTIVE", "RELISTED")]
    live_homes = len({labels.get(x["articleNo"], (x["articleNo"],))[0] for x in live_listings})

    # 묶인 매물끼리 표에서 붙어 나오도록 묶음의 대표값으로 먼저 정렬한다.
    group_rank = {}
    for x in listings:
        key = labels.get(x["articleNo"], (x["articleNo"],))[0]
        group_rank[key] = min(group_rank.get(key, sort_key(x)), sort_key(x))
    listings.sort(key=lambda x: (group_rank[labels.get(x["articleNo"], (x["articleNo"],))[0]], sort_key(x)))

    rows = []
    for x in listings:
        status = x.get("status", "UNKNOWN")
        article_no = x["articleNo"]
        if article_no in labels:
            label, size, tint = labels[article_no]
            group_cell = f'<span class="grp g{tint}">동일 추정 {esc(label)} · {size}건</span>'
            row_class = f' class="grouped g{tint}"'
        elif article_no in lookalike_ids:
            group_cell = '<span class="grp maybe">조건 유사</span>'
            row_class = ""
        else:
            group_cell = ""
            row_class = ""
        rows.append(f"""
        <tr{row_class}>
          <td>{group_cell}</td>
          <td><span class="badge {status.lower()}">{esc(status)}</span></td>
          <td>{money(x.get('price'))}</td>
          <td>{esc(x.get('buildingName'))}</td>
          <td>{esc(x.get('floor'))}</td>
          <td>{esc(x.get('areaExclusiveSqm'))}㎡</td>
          <td>{esc(x.get('featureDesc'))}</td>
          <td>{esc(when(x.get('firstSeenAt')))}</td>
          <td>{esc(when(x.get('lastSeenAt')))}</td>
          <td>{esc(x.get('missCount', 0))}</td>
          <td><a href="{esc(x.get('url', '#'))}" target="_blank" rel="noopener">매물 보기</a></td>
        </tr>""")

    dup_items = []
    for group in clusters:
        label = labels[group[0]["articleNo"]][0]
        members = " / ".join(
            f"{esc(m.get('floor'))} {esc(m.get('status'))} ({esc(m['articleNo'])})"
            for m in sorted(group, key=sort_key)
        )
        dup_items.append(f"<li><b>{esc(label)}</b> · {esc(group[0].get('buildingName'))}동 — {members}</li>")
    dup_note = f"<ul class=\"dups\">{''.join(dup_items)}</ul>" if dup_items else ""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>대방현대1차 매물 추적</title>
<style>{CSS}</style></head><body><main>
<h1>대방현대1차 매물 추적</h1><div class="sub">마지막 생성: {now} · 네이버 부동산 노출 상태 기반</div>
<div class="cards">
<div class="card"><div>현재 노출</div><div class="num">{len(live_listings)}</div><div class="hint">등록된 매물 건수</div></div>
<div class="card"><div>실매물 추정</div><div class="num">{live_homes}</div><div class="hint">중복 묶은 뒤 집 수</div></div>
<div class="card"><div>재등록</div><div class="num">{counts['RELISTED']}</div></div>
<div class="card"><div>삭제 후보</div><div class="num">{counts['OFF_MARKET_CANDIDATE']}</div></div>
<div class="card"><div>거래종결 추정</div><div class="num">{counts['OFF_MARKET']}</div></div>
</div>
<div class="table-wrap"><table><thead><tr><th>묶음</th><th>상태</th><th>가격</th><th>동</th><th>층</th><th>전용</th><th>특징</th><th>최초 확인</th><th>마지막 확인</th><th>미노출 횟수</th><th>링크</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<div class="note">
<b>동일 추정</b>은 같은 동·같은 면적에 층이 맞아떨어지고, 특징 문구가 겹치거나 층이 정확히 일치하는 매물을 한 집으로 묶은 것입니다.
중개사 여러 곳이 같은 집을 올리거나, 층 표기가 "15층"에서 "중층"으로 바뀌어 재등록되면 매물번호가 달라져 별건으로 쌓이기 때문입니다.
<b>조건 유사</b>는 조건은 겹치지만 문구가 달라 같은 집이라 단정하기 어려운 2건입니다. 어디까지나 추정이므로 원 매물을 확인하세요.
{dup_note}
<br>OFF_MARKET은 네이버 부동산에서 연속 3회 보이지 않았다는 뜻이며 실제 매매 완료를 확정하지 않습니다. 계약 취소, 중개사 철회, 가격 수정 후 재등록도 포함될 수 있습니다.
</div>
</main></body></html>""", encoding="utf-8")

    print(f"생성 완료: {OUT} (매물 {len(listings)}건, 동일추정 {len(clusters)}묶음, 조건유사 {len(lookalikes)}쌍)")


if __name__ == "__main__":
    main()
