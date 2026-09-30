#!/usr/bin/env python3
"""수집한 매물 JSON으로 동작구 다중 단지 대시보드(docs/index.html)를 만든다.

  python -X utf8 scripts/build_daebang_report.py                     # data/apt-listings.json
  python -X utf8 scripts/build_daebang_report.py data/apt-listings-s1.json
  python -X utf8 scripts/build_daebang_report.py --out docs/preview.html

환경변수 APT_LISTINGS / APT_TARGETS / APT_REPORT_OUT 로도 경로를 바꿀 수 있다.
"""
import argparse
import html
import json
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DATA = Path("data/apt-listings.json")
TARGETS = Path("src/main/resources/targets/dongjak-500.json")
OUT = Path("docs/index.html")

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


def complex_no(x):
    return str(x.get("hscpNo") or "")


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
    """동일 매물로 추정되는 묶음들. 2건 이상 묶인 것만 반환.

    전수 비교는 O(n²)라 단지가 44개로 늘면 감당이 안 된다. same_home은 단지·동·면적이
    모두 같아야 참이므로, 그 값으로 버킷을 나눠 버킷 안에서만 비교한다(결과는 동일).
    """
    parent = {x["articleNo"]: x["articleNo"] for x in listings}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    buckets = defaultdict(list)
    for x in listings:
        buckets[(complex_no(x),) + identity(x)].append(x)

    for bucket in buckets.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if same_home(a, b):
                    ra, rb = find(a["articleNo"]), find(b["articleNo"])
                    if ra != rb:
                        parent[ra] = rb

    members = defaultdict(list)
    for x in listings:
        members[find(x["articleNo"])].append(x)
    return [group for group in members.values() if len(group) > 1], {x["articleNo"]: find(x["articleNo"]) for x in listings}


def find_lookalikes(listings, clustered):
    """묶을 근거는 부족하지만 조건이 겹쳐 눈으로 확인할 만한 2건 짝."""
    buckets = defaultdict(list)
    for x in listings:
        if x["articleNo"] in clustered or len(bigrams(x.get("featureDesc"))) < 4:
            continue
        band = band_of(*parse_floor(x.get("floor")))
        if band is None:
            continue
        buckets[(complex_no(x),) + identity(x) + (band, x.get("price"))].append(x)
    # 조건이 겹치는 매물이 3건 이상이면 비슷한 세대가 많을 뿐이므로 제외한다.
    return [group for group in buckets.values() if len(group) == 2]


STATUS_ORDER = {"ACTIVE": 0, "RELISTED": 1, "OFF_MARKET_CANDIDATE": 2, "OFF_MARKET": 3}
STATUS_LABEL = {
    "ACTIVE": "판매중",
    "RELISTED": "재등록",
    "OFF_MARKET_CANDIDATE": "삭제 후보",
    "OFF_MARKET": "거래종결 추정",
}
LIVE_STATUS = ("ACTIVE", "RELISTED")


def sort_key(x):
    return (STATUS_ORDER.get(x.get("status"), 9), x.get("price", 0), x.get("articleNo", ""))


def floor_number(raw):
    """정렬용 층 값. 저/중/고는 해당 구간의 중앙값으로 환산한다."""
    floor, total = parse_floor(raw)
    if floor is None:
        return None
    if isinstance(floor, int):
        return floor
    low, high = band_span(floor, total)
    return round((low + high) / 2)


PRICE_EDGES = [(50000, "5억 미만"), (100000, "5~10억"), (150000, "10~15억"), (200000, "15~20억"),
               (250000, "20~25억"), (300000, "25~30억"), (None, "30억 이상")]


def price_band(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "-"
    for edge, label in PRICE_EDGES:
        if edge is None or v < edge:
            return label
    return "-"


def pyeong_band(value):
    try:
        p = int(value)
    except (TypeError, ValueError):
        return "-"
    if p >= 50:
        return "50평 이상"
    return f"{p // 10 * 10}평대"


def dong_label(region_name):
    """'서울동작_흑석동' -> '흑석동'"""
    text = str(region_name or "")
    return text.split("_", 1)[1] if "_" in text else text


def load_targets(path):
    """단지번호 -> {name, dong, households, year}. 매물 데이터에는 세대수·준공연도가 없다."""
    if not path.exists():
        return {}, []
    doc = json.loads(path.read_text(encoding="utf-8"))
    meta, dongs = {}, []
    for region in doc.get("regions", []):
        dong = dong_label(region.get("name"))
        if dong not in dongs:
            dongs.append(dong)
        for c in region.get("complexes", []):
            meta[str(c.get("no"))] = {
                "name": c.get("name") or "",
                "dong": dong,
                "households": c.get("households"),
                "year": c.get("approvedYear"),
            }
    return meta, dongs


# 동일 추정 묶음 색: (밝은 배경, 글자, 행 배경) × (라이트, 다크)
TINTS = [
    (("#ede7f6", "#4527a0", "#fbfaff"), ("#2c2444", "#c9b8f0", "#1d1a27")),
    (("#e0f2f1", "#00695c", "#f7fcfb"), ("#143330", "#87d9cc", "#161f1e")),
    (("#fff8e1", "#8a5200", "#fffdf5"), ("#3a2f13", "#e8c76b", "#201d15")),
    (("#fce4ec", "#ad1457", "#fdf6f9"), ("#3a2029", "#f3a8c6", "#221a1d")),
    (("#e1f5fe", "#01579b", "#f6fcff"), ("#122c3d", "#8ec9ef", "#161d23")),
    (("#f1f8e9", "#33691e", "#f9fcf5"), ("#22301a", "#b6d99a", "#1a1e17")),
]


def tint_vars(dark):
    """묶음 색도 변수로 빼야 라이트/다크 블록에서 같은 규칙을 재사용할 수 있다."""
    out = []
    for i, pair in enumerate(TINTS):
        bg, fg, row = pair[1 if dark else 0]
        out.append(f"--g{i}-bg:{bg};--g{i}-fg:{fg};--g{i}-row:{row};")
    return "".join(out)


DARK_VARS = """
  --bg:#12141a; --panel:#1a1d25; --panel-2:#20242e; --card:#1a1d25; --text:#e6e8ee; --muted:#98a0b0;
  --line:#2c313c; --line-2:#3a4150; --accent:#7aa7ff; --accent-soft:#1e2a44; --chip:#232834; --chip-on:#2b3a5c;
  --shadow:0 1px 3px rgba(0,0,0,.5); --ok-bg:#16301f; --ok-fg:#7fd6a0; --info-bg:#152a3f; --info-fg:#8cc4f0;
  --warn-bg:#3a2c12; --warn-fg:#e5bd6a; --bad-bg:#3a1c1e; --bad-fg:#f0a0a4; --th:#20242e;
""" + tint_vars(True)


def build_css():
    tints = "".join(
        f".g{i}{{background:var(--g{i}-bg);color:var(--g{i}-fg)}}"
        f"tr.grouped.g{i} td{{background:var(--g{i}-row)}}"
        f"tr.grouped.g{i} td:first-child{{border-left-color:var(--g{i}-fg)}}"
        for i in range(len(TINTS)))
    return ("""
*{box-sizing:border-box}
:root{
  --bg:#f2f4f8; --panel:#ffffff; --panel-2:#f8fafc; --card:#ffffff; --text:#16181d; --muted:#5f6775;
  --line:#e3e8ef; --line-2:#cdd5e0; --accent:#1f5fd0; --accent-soft:#e8f0ff; --chip:#eef1f6; --chip-on:#1f5fd0;
  --shadow:0 1px 3px rgba(16,24,40,.08); --ok-bg:#e6f6ec; --ok-fg:#1d7a44; --info-bg:#e6f0fb; --info-fg:#17558f;
  --warn-bg:#fdf0da; --warn-fg:#96590a; --bad-bg:#fdeaea; --bad-fg:#a92c2c; --th:#f6f8fb;
  __LIGHT_TINTS__
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){__DARK__}}
:root[data-theme="dark"]{__DARK__}
body{margin:0;background:var(--bg);color:var(--text);font-size:15px;line-height:1.45;
  font-family:'Pretendard','Apple SD Gothic Neo',-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif;
  -webkit-text-size-adjust:100%}
main{max-width:1560px;margin:0 auto;padding:22px 20px 60px}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
h1{margin:0;font-size:26px;letter-spacing:-.02em} h2{margin:0;font-size:17px;letter-spacing:-.01em}
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.sub{color:var(--muted);font-size:13px;margin:4px 0 18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);margin-bottom:16px}
.panel>.panel-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;
  padding:13px 16px;border-bottom:1px solid var(--line)}
.panel>.panel-body{padding:14px 16px}
.panel.closed>.panel-body{display:none} .panel.closed>.panel-head{border-bottom:0}
.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 14px;box-shadow:var(--shadow)}
.stat .k{color:var(--muted);font-size:12px} .stat .v{font-size:25px;font-weight:800;letter-spacing:-.02em;margin-top:2px}
.stat .h{color:var(--muted);font-size:11px;margin-top:1px}
.lbl{font-size:12px;font-weight:700;color:var(--muted);margin:0 0 7px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid var(--line-2);background:var(--chip);color:var(--text);border-radius:999px;
  padding:5px 11px;font-size:13px;cursor:pointer;font-family:inherit;line-height:1.3}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--chip-on);border-color:var(--chip-on);color:#fff;font-weight:700}
.chip .c{opacity:.7;font-size:11px;margin-left:4px}
.chip.zero{opacity:.45}
.row{display:grid;grid-template-columns:repeat(5,minmax(0,1fr)) 1.4fr auto;gap:8px;margin-top:14px}
select,input[type=search],input[type=text]{width:100%;padding:8px 10px;border:1px solid var(--line-2);border-radius:9px;
  background:var(--panel-2);color:var(--text);font-size:13px;font-family:inherit}
select:focus,input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
button.btn{padding:8px 14px;border:1px solid var(--line-2);border-radius:9px;background:var(--panel-2);color:var(--text);
  font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;white-space:nowrap}
button.btn:hover{border-color:var(--accent)}
.active-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px;padding:9px 11px;
  border:1px dashed var(--line-2);border-radius:11px;background:var(--panel-2)}
.active-bar[hidden]{display:none}
.active-bar .t{font-size:12px;font-weight:700;color:var(--muted)}
.af{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line-2);background:var(--panel);
  border-radius:999px;padding:3px 9px;font-size:12px;cursor:pointer;color:var(--text);font-family:inherit}
.af .x{opacity:.6;font-weight:700}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:middle}
th{background:var(--th);font-size:12px;color:var(--muted);font-weight:700;position:sticky;top:0;z-index:1}
th.s{cursor:pointer;user-select:none} th.s:hover{color:var(--accent)} th .ind{opacity:.45;margin-left:3px}
th.s.on{color:var(--accent)} th.s.on .ind{opacity:1}
tbody tr:hover td{background:var(--panel-2)}
td.num{text-align:right;font-variant-numeric:tabular-nums} th.num{text-align:right}
td.feat{white-space:normal;min-width:260px;max-width:460px;color:var(--muted);font-size:12.5px}
td.price{font-weight:700;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11.5px;font-weight:700}
.st-active{background:var(--ok-bg);color:var(--ok-fg)} .st-relisted{background:var(--info-bg);color:var(--info-fg)}
.st-off_market_candidate{background:var(--warn-bg);color:var(--warn-fg)} .st-off_market{background:var(--bad-bg);color:var(--bad-fg)}
.grp{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}
.grp.maybe{background:var(--chip);color:var(--muted)}
tr.grouped td:first-child{border-left:3px solid transparent}
__TINTS__
.cx{margin-bottom:12px}
.cx-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 15px;cursor:pointer}
.cx-head:hover{background:var(--panel-2)}
.cx-head .nm{font-size:16px;font-weight:800;letter-spacing:-.01em}
.cx-head .meta{color:var(--muted);font-size:12px}
.cx-head .caret{color:var(--muted);font-size:12px;width:12px}
.cx-head .right{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.tag{display:inline-block;padding:3px 9px;border-radius:999px;font-size:11.5px;font-weight:700;background:var(--chip);color:var(--muted)}
.tag.acc{background:var(--accent-soft);color:var(--accent)}
.cx.closed .cx-body{display:none}
.cx-foot{padding:10px 15px;border-top:1px solid var(--line)}
details.dups{padding:10px 15px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted)}
details.dups summary{cursor:pointer;font-weight:700}
details.dups ul{margin:8px 0 0;padding-left:18px} details.dups li{margin:3px 0}
.empty{padding:38px 16px;text-align:center;color:var(--muted)}
.note{color:var(--muted);font-size:12.5px;line-height:1.7;padding:14px 16px}
.note b{color:var(--text)}
@media (max-width:1200px){.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.row{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:720px){
  main{padding:14px 12px 44px} h1{font-size:21px} .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .stat .v{font-size:21px} .row{grid-template-columns:1fr}
  th{display:none}
  table,tbody,tr,td{display:block;width:100%}
  tbody tr{border-bottom:1px solid var(--line-2);padding:8px 4px}
  td{border:0;padding:3px 11px;white-space:normal;text-align:left!important}
  td:before{content:attr(data-l);display:inline-block;min-width:74px;color:var(--muted);font-size:11.5px;font-weight:700}
  td:empty{display:none} td.feat{max-width:none}
  td.c-first,td.c-miss{display:none}
  tr.grouped td:first-child{border-left:0}
}
""".replace("__LIGHT_TINTS__", tint_vars(False))
            .replace("__DARK__", DARK_VARS)
            .replace("__TINTS__", tints))


def main():
    started = time.perf_counter()
    ap = argparse.ArgumentParser(description="동작구 아파트 매물 대시보드 생성")
    ap.add_argument("data", nargs="?", default=os.getenv("APT_LISTINGS") or str(DATA), help="매물 JSON 경로")
    ap.add_argument("--targets", default=os.getenv("APT_TARGETS") or str(TARGETS), help="수집 대상 단지 JSON 경로")
    ap.add_argument("--out", default=os.getenv("APT_REPORT_OUT") or str(OUT), help="출력 HTML 경로")
    args = ap.parse_args()

    data_path, targets_path, out_path = Path(args.data), Path(args.targets), Path(args.out)
    listings = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else []
    meta, dong_order = load_targets(targets_path)

    # 단지 정보: 대상 목록이 기준이고, 목록에 없는 단지가 수집되면 매물 데이터로 채운다.
    by_complex = defaultdict(list)
    for x in listings:
        by_complex[complex_no(x)].append(x)
    for no, group in by_complex.items():
        if no not in meta:
            meta[no] = {
                "name": group[0].get("title") or f"단지 {no}",
                "dong": dong_label(group[0].get("regionName")),
                "households": None,
                "year": None,
            }
            if meta[no]["dong"] and meta[no]["dong"] not in dong_order:
                dong_order.append(meta[no]["dong"])

    # 동일 추정 묶음은 단지 안에서만 성립하므로 단지별로 계산하고 라벨도 단지별로 붙인다.
    labels, homes, cluster_total, lookalike_total = {}, {}, 0, 0
    dup_groups = defaultdict(list)
    lookalike_ids = set()
    for no, group in by_complex.items():
        clusters, roots = build_clusters(group)
        homes.update(roots)
        clusters.sort(key=lambda g: min(sort_key(x) for x in g))
        for idx, members in enumerate(clusters):
            label = chr(ord("A") + idx) if idx < 26 else f"#{idx + 1}"
            for x in members:
                labels[x["articleNo"]] = (label, len(members), idx % len(TINTS))
            dup_groups[no].append((label, members))
        cluster_total += len(clusters)
        for pair in find_lookalikes(group, labels):
            lookalike_total += 1
            lookalike_ids.update(x["articleNo"] for x in pair)

    rows = []
    for x in listings:
        no = complex_no(x)
        article = x["articleNo"]
        info = meta.get(no, {})
        grp = labels.get(article)
        rows.append({
            "n": article,
            "c": no,
            "cn": info.get("name") or "",
            "d": info.get("dong") or dong_label(x.get("regionName")),
            "b": x.get("buildingName") or "",
            "s": x.get("status") or "UNKNOWN",
            "p": x.get("price"),
            "pb": price_band(x.get("price")),
            "y": x.get("pyeong"),
            "yb": pyeong_band(x.get("pyeong")),
            "ae": x.get("areaExclusiveSqm"),
            "as": x.get("areaSupplySqm"),
            "fl": x.get("floor") or "",
            "fn": floor_number(x.get("floor")),
            "fd": x.get("featureDesc") or "",
            "u": x.get("url") or "",
            "fs": when(x.get("firstSeenAt")),
            "ls": when(x.get("lastSeenAt")),
            "mc": x.get("missCount") or 0,
            "h": homes.get(article, article),
            "g": list(grp) if grp else 0,
            "lk": 1 if article in lookalike_ids else 0,
        })
    rows.sort(key=lambda r: (r["c"], STATUS_ORDER.get(r["s"], 9), r["p"] or 0, r["n"]))

    complexes = []
    for no, info in meta.items():
        complexes.append({
            "no": no,
            "name": info.get("name") or f"단지 {no}",
            "dong": info.get("dong") or "",
            "hh": info.get("households"),
            "yr": info.get("year"),
        })
    complexes.sort(key=lambda c: (dong_order.index(c["dong"]) if c["dong"] in dong_order else 99,
                                  -(c["hh"] or 0), c["name"]))

    dups = {}
    for no, groups in dup_groups.items():
        dups[no] = [
            {"l": label, "b": members[0].get("buildingName") or "",
             "m": [f"{m.get('floor')} {STATUS_LABEL.get(m.get('status'), m.get('status'))} ({m['articleNo']})"
                   for m in sorted(members, key=sort_key)]}
            for label, members in groups
        ]

    present_price = [b for _, b in PRICE_EDGES if any(r["pb"] == b for r in rows)]
    present_pyeong = sorted({r["yb"] for r in rows if r["yb"] != "-"},
                            key=lambda s: 999 if s.startswith("50평 이상") else int(re.sub(r"\D", "", s) or 0))
    present_status = [s for s in STATUS_ORDER if any(r["s"] == s for r in rows)]

    counts = Counter(r["s"] for r in rows)
    live_rows = [r for r in rows if r["s"] in LIVE_STATUS]
    meta_out = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": data_path.name,
        "total": len(rows),
        "live": len(live_rows),
        "homes": len({r["h"] for r in live_rows}),
        "complexCount": len([c for c in complexes if any(r["c"] == c["no"] for r in rows)]),
        "targetCount": len(complexes),
        "clusters": cluster_total,
        "lookalikes": lookalike_total,
        "dongs": dong_order,
        "priceBands": present_price,
        "pyeongBands": present_pyeong,
        "statuses": present_status,
        "statusLabel": STATUS_LABEL,
    }

    def embed(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    page = (PAGE
            .replace("__CSS__", build_css())
            .replace("__META__", embed(meta_out))
            .replace("__ROWS__", embed(rows))
            .replace("__COMPLEXES__", embed(complexes))
            .replace("__DUPS__", embed(dups))
            .replace("__GENERATED__", esc(meta_out["generatedAt"]))
            .replace("__SOURCE__", esc(meta_out["source"])))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")

    elapsed = time.perf_counter() - started
    print(f"생성 완료: {out_path} ({elapsed:.2f}초, {len(page) / 1024:.0f}KB)")
    print(f"  매물 {len(rows)}건 · 단지 {meta_out['complexCount']}/{meta_out['targetCount']}개 · "
          f"실매물 추정 {meta_out['homes']}건 · 동일추정 {cluster_total}묶음 · 조건유사 {lookalike_total}쌍")


PAGE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>동작구 아파트 매물 추적</title>
<meta name="description" content="서울 동작구 500세대 이상 아파트 단지 매물 추적 대시보드">
<style>__CSS__</style>
</head><body><main>

<div class="head">
  <div>
    <h1>동작구 아파트 매물 추적</h1>
    <div class="sub">마지막 생성: __GENERATED__ · 네이버 부동산 노출 상태 기반 · 원본 __SOURCE__</div>
  </div>
  <button class="btn" id="theme" type="button">테마: 자동</button>
</div>

<div class="stats">
  <div class="stat"><div class="k">현재 노출</div><div class="v" id="sLive">0</div><div class="h">판매중 + 재등록</div></div>
  <div class="stat"><div class="k">실매물 추정</div><div class="v" id="sHomes">0</div><div class="h">중복 묶은 뒤 집 수</div></div>
  <div class="stat"><div class="k">단지</div><div class="v" id="sCx">0</div><div class="h">매물이 있는 단지</div></div>
  <div class="stat"><div class="k">재등록</div><div class="v" id="sRe">0</div><div class="h">RELISTED</div></div>
  <div class="stat"><div class="k">삭제 후보</div><div class="v" id="sCand">0</div><div class="h">연속 미노출</div></div>
  <div class="stat"><div class="k">거래종결 추정</div><div class="v" id="sOff">0</div><div class="h">3회 연속 미노출</div></div>
</div>

<section class="panel">
  <div class="panel-head"><h2>단지 선택</h2><span class="tag" id="selCount">전체</span></div>
  <div class="panel-body">
    <div class="lbl">동 (복수 선택)</div>
    <div class="chips" id="dongChips"></div>
    <div class="lbl" style="margin-top:14px">단지 (복수 선택)</div>
    <div class="chips" id="cxChips"></div>
    <div class="row">
      <select id="fPrice"></select>
      <select id="fPyeong"></select>
      <select id="fStatus"></select>
      <select id="fGroup">
        <option value="">묶음(전체)</option>
        <option value="dup">동일 추정만</option>
        <option value="look">조건 유사만</option>
        <option value="solo">묶이지 않은 것만</option>
      </select>
      <select id="fSort">
        <option value="p:asc">가격 낮은순</option>
        <option value="p:desc">가격 높은순</option>
        <option value="y:asc">평형 작은순</option>
        <option value="y:desc">평형 큰순</option>
        <option value="fn:asc">층 낮은순</option>
        <option value="fn:desc">층 높은순</option>
        <option value="s:asc">상태순</option>
        <option value="ls:desc">최근 확인순</option>
      </select>
      <input type="search" id="fQ" placeholder="단지·동·특징 검색">
      <button class="btn" id="reset" type="button">초기화</button>
    </div>
    <div class="active-bar" id="activeBar" hidden></div>
  </div>
</section>

<section class="panel" id="summaryPanel">
  <div class="panel-head" id="summaryHead" style="cursor:pointer"><h2>단지별 요약</h2>
    <span class="tag">행을 누르면 그 단지만 보기</span></div>
  <div class="panel-body" style="padding:0">
    <div class="table-wrap"><table id="summary"></table></div>
  </div>
</section>

<div id="sections"></div>

<section class="panel"><div class="note">
<b>동일 추정</b>은 같은 단지·같은 동·같은 면적에 층이 맞아떨어지고, 특징 문구가 겹치거나 층이 정확히 일치하는 매물을 한 집으로 묶은 것입니다.
중개사 여러 곳이 같은 집을 올리거나, 층 표기가 "15층"에서 "중층"으로 바뀌어 재등록되면 매물번호가 달라져 별건으로 쌓이기 때문입니다.
<b>조건 유사</b>는 조건은 겹치지만 문구가 달라 같은 집이라 단정하기 어려운 2건입니다. 어디까지나 추정이므로 원 매물을 확인하세요.
<b>실매물 추정</b>은 현재 노출 매물을 동일 추정으로 묶은 뒤 남는 집 수입니다.
<br>OFF_MARKET은 네이버 부동산에서 연속 3회 보이지 않았다는 뜻이며 실제 매매 완료를 확정하지 않습니다. 계약 취소, 중개사 철회, 가격 수정 후 재등록도 포함될 수 있습니다.
</div></section>

<script>
const META=__META__, RAW=__ROWS__, COMPLEXES=__COMPLEXES__, DUPS=__DUPS__;
const CX=new Map(COMPLEXES.map(c=>[c.no,c]));
const SECTION_STEP=8, ROW_STEP=30;

const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const money=v=>{const n=Number(v);if(!Number.isFinite(n))return '-';return n>=10000?(n/10000).toFixed(2)+'억':n.toLocaleString()+'만';};
const stLabel=s=>META.statusLabel[s]||s;

const dongSel=new Set(), cxSel=new Set();
let sortState={key:'p',dir:'asc'}, sumSort={key:'cnt',dir:'desc'};
let visibleSections=SECTION_STEP;
const rowLimit=new Map(), closed=new Set();

/* ---------- 테마 ---------- */
const THEMES=['auto','light','dark'], THEME_LABEL={auto:'자동',light:'라이트',dark:'다크'};
let theme='auto';
try{const t=localStorage.getItem('apt-theme'); if(THEMES.includes(t)) theme=t;}catch(e){}
function applyTheme(){
  if(theme==='auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme',theme);
  $('theme').textContent='테마: '+THEME_LABEL[theme];
}
$('theme').addEventListener('click',()=>{
  theme=THEMES[(THEMES.indexOf(theme)+1)%THEMES.length];
  try{localStorage.setItem('apt-theme',theme);}catch(e){}
  applyTheme();
});
applyTheme();

/* ---------- 필터 ---------- */
function fillSelect(el,label,values){
  el.innerHTML='<option value="">'+esc(label)+'(전체)</option>'+values.map(v=>'<option>'+esc(v)+'</option>').join('');
}
fillSelect($('fPrice'),'가격대',META.priceBands);
fillSelect($('fPyeong'),'평형',META.pyeongBands);
$('fStatus').innerHTML='<option value="">상태(전체)</option>'+META.statuses.map(s=>'<option value="'+esc(s)+'">'+esc(stLabel(s))+'</option>').join('');

function passOther(x){
  const p=$('fPrice').value, y=$('fPyeong').value, s=$('fStatus').value, g=$('fGroup').value, q=$('fQ').value.trim().toLowerCase();
  if(p && x.pb!==p) return false;
  if(y && x.yb!==y) return false;
  if(s && x.s!==s) return false;
  if(g==='dup' && !x.g) return false;
  if(g==='look' && !x.lk) return false;
  if(g==='solo' && (x.g||x.lk)) return false;
  if(q && !((x.cn+' '+x.d+' '+x.b+'동 '+x.fd+' '+x.fl).toLowerCase().includes(q))) return false;
  return true;
}
/* 동·단지 칩의 건수는 나머지 필터 기준으로 센다(선택해도 다른 칩 숫자가 0이 되지 않게). */
function baseRows(){ return RAW.filter(passOther); }
function afterDong(rows){ return dongSel.size ? rows.filter(x=>dongSel.has(x.d)) : rows; }
function finalRows(rows){ return cxSel.size ? rows.filter(x=>cxSel.has(x.c)) : rows; }

/* ---------- 정렬 ---------- */
const TEXT_KEYS=new Set(['b','fl','fd','s','cn','d','fs','ls','name','dong']);
function cmpKey(a,b,k){
  if(k==='s') return (['ACTIVE','RELISTED','OFF_MARKET_CANDIDATE','OFF_MARKET'].indexOf(a.s)+9)%13-(['ACTIVE','RELISTED','OFF_MARKET_CANDIDATE','OFF_MARKET'].indexOf(b.s)+9)%13;
  if(TEXT_KEYS.has(k)) return String(a[k]??'').localeCompare(String(b[k]??''),'ko');
  const x=Number(a[k]), y=Number(b[k]);
  if(!Number.isFinite(x)&&!Number.isFinite(y)) return 0;
  if(!Number.isFinite(x)) return 1;
  if(!Number.isFinite(y)) return -1;
  return x-y;
}
/* 묶인 매물끼리 표에서 붙어 나오도록 묶음 대표값으로 먼저 정렬한다. */
function sortRows(rows){
  const dir=sortState.dir==='asc'?1:-1;
  const cmp=(a,b)=>dir*cmpKey(a,b,sortState.key);
  const rep=new Map();
  for(const r of rows){const cur=rep.get(r.h); if(cur===undefined||cmp(r,cur)<0) rep.set(r.h,r);}
  return rows.slice().sort((a,b)=>{
    const c=cmp(rep.get(a.h),rep.get(b.h));
    if(c!==0) return c;
    if(a.h!==b.h) return a.h<b.h?-1:1;
    return cmp(a,b) || (a.n<b.n?-1:1);
  });
}
function ind(key,state){ return state.key!==key?'↕':(state.dir==='asc'?'↑':'↓'); }
/* 모바일에서는 표 머리글이 숨겨지므로 정렬 셀렉트가 유일한 정렬 수단이다. */
function syncSort(){
  const el=$('fSort'), v=sortState.key+':'+sortState.dir;
  el.value=v;
  if(el.value!==v){
    let opt=el.querySelector('option[data-custom]');
    if(!opt){ opt=document.createElement('option'); opt.dataset.custom='1'; el.appendChild(opt); }
    opt.value=v; opt.textContent='표 머리글 정렬';
    el.value=v;
  }
}

/* ---------- 칩 ---------- */
function renderDongChips(rows){
  const cnt=new Map();
  for(const x of rows) cnt.set(x.d,(cnt.get(x.d)||0)+1);
  const all=dongSel.size===0?'on':'';
  const html=['<button type="button" class="chip '+all+'" data-dong="">전체<span class="c">'+rows.length+'</span></button>']
    .concat(META.dongs.map(d=>{
      const n=cnt.get(d)||0;
      return '<button type="button" class="chip '+(dongSel.has(d)?'on':'')+(n?'':' zero')+'" data-dong="'+esc(d)+'">'+esc(d)+'<span class="c">'+n+'</span></button>';
    }));
  $('dongChips').innerHTML=html.join('');
}
function renderCxChips(rows){
  const cnt=new Map();
  for(const x of rows) cnt.set(x.c,(cnt.get(x.c)||0)+1);
  const pool=COMPLEXES.filter(c=>dongSel.size===0||dongSel.has(c.dong));
  for(const no of [...cxSel]) if(!pool.some(c=>c.no===no)) cxSel.delete(no);
  const all=cxSel.size===0?'on':'';
  const html=['<button type="button" class="chip '+all+'" data-cx="">전체<span class="c">'+pool.length+'</span></button>']
    .concat(pool.map(c=>{
      const n=cnt.get(c.no)||0;
      return '<button type="button" class="chip '+(cxSel.has(c.no)?'on':'')+(n?'':' zero')+'" data-cx="'+esc(c.no)+'">'+esc(c.name)+'<span class="c">'+n+'</span></button>';
    }));
  $('cxChips').innerHTML=html.join('');
  $('selCount').textContent=cxSel.size?cxSel.size+'개 단지 선택':(dongSel.size?dongSel.size+'개 동':'전체');
}
function renderActiveBar(){
  const chips=[];
  const add=(label,type,value)=>chips.push('<button type="button" class="af" data-clear="'+type+'" data-value="'+esc(value||'')+'"><span>'+esc(label)+'</span><span class="x">×</span></button>');
  dongSel.forEach(d=>add('동: '+d,'dong',d));
  cxSel.forEach(c=>add('단지: '+(CX.get(c)?CX.get(c).name:c),'cx',c));
  if($('fPrice').value) add('가격대: '+$('fPrice').value,'fPrice');
  if($('fPyeong').value) add('평형: '+$('fPyeong').value,'fPyeong');
  if($('fStatus').value) add('상태: '+stLabel($('fStatus').value),'fStatus');
  if($('fGroup').value) add('묶음: '+$('fGroup').selectedOptions[0].text,'fGroup');
  if($('fQ').value.trim()) add('검색: '+$('fQ').value.trim(),'fQ');
  const bar=$('activeBar');
  if(!chips.length){bar.hidden=true;bar.innerHTML='';return;}
  bar.hidden=false;
  bar.innerHTML='<span class="t">활성 필터</span>'+chips.join('')+'<button type="button" class="af" data-clear="all"><span>전체 해제</span></button>';
}

/* ---------- 단지별 요약 ---------- */
function summarize(rows){
  const by=new Map();
  for(const x of rows){
    let s=by.get(x.c);
    if(!s){const c=CX.get(x.c)||{name:x.cn,dong:x.d,hh:null,yr:null};
      s={no:x.c,name:c.name,dong:c.dong,hh:c.hh,yr:c.yr,cnt:0,live:0,homes:new Set(),min:null,max:null,py:new Set(),dup:0};
      by.set(x.c,s);}
    s.cnt++;
    if(x.s==='ACTIVE'||x.s==='RELISTED'){s.live++;s.homes.add(x.h);}
    if(x.g) s.dup++;
    const p=Number(x.p);
    if(Number.isFinite(p)){ s.min=s.min===null?p:Math.min(s.min,p); s.max=s.max===null?p:Math.max(s.max,p); }
    if(x.y) s.py.add(Number(x.y));
  }
  return [...by.values()].map(s=>({...s,homes:s.homes.size,
    pyMin:s.py.size?Math.min(...s.py):null, pyMax:s.py.size?Math.max(...s.py):null}));
}
function renderSummary(stats){
  const dir=sumSort.dir==='asc'?1:-1;
  const key=sumSort.key;
  const sorted=stats.slice().sort((a,b)=>{
    const c=(key==='name'||key==='dong')
      ? String(a[key]??'').localeCompare(String(b[key]??''),'ko')
      : (Number(a[key]??-1)-Number(b[key]??-1));
    return c!==0?dir*c:String(a.name).localeCompare(String(b.name),'ko');
  });
  const th=(l,k,cls)=>'<th class="s '+(cls||'')+(sumSort.key===k?' on':'')+'" data-sum="'+k+'">'+l+'<span class="ind">'+ind(k,sumSort)+'</span></th>';
  const head='<thead><tr>'+th('단지','name')+th('동','dong')+th('세대수','hh','num')+th('준공','yr','num')
    +th('매물','cnt','num')+th('실매물 추정','homes','num')+th('동일 추정','dup','num')
    +th('최저가','min','num')+th('최고가','max','num')+th('평형','pyMin','num')+'</tr></thead>';
  const body=sorted.map(s=>'<tr data-pick="'+esc(s.no)+'" style="cursor:pointer">'
    +'<td data-l="단지"><b>'+esc(s.name)+'</b></td>'
    +'<td data-l="동">'+esc(s.dong)+'</td>'
    +'<td data-l="세대수" class="num">'+(s.hh?s.hh.toLocaleString():'-')+'</td>'
    +'<td data-l="준공" class="num">'+esc(s.yr||'-')+'</td>'
    +'<td data-l="매물" class="num">'+s.cnt+'</td>'
    +'<td data-l="실매물 추정" class="num">'+s.homes+'</td>'
    +'<td data-l="동일 추정" class="num">'+(s.dup||'')+'</td>'
    +'<td data-l="최저가" class="num">'+money(s.min)+'</td>'
    +'<td data-l="최고가" class="num">'+money(s.max)+'</td>'
    +'<td data-l="평형" class="num">'+(s.pyMin?(s.pyMin===s.pyMax?s.pyMin+'평':s.pyMin+'~'+s.pyMax+'평'):'-')+'</td></tr>').join('');
  $('summary').innerHTML=head+'<tbody>'+(body||'<tr><td colspan="10" class="empty">조건에 맞는 단지가 없습니다.</td></tr>')+'</tbody>';
}

/* ---------- 매물 표 ---------- */
function rowHtml(x){
  let cell='', cls='';
  if(x.g){ cell='<span class="grp g'+x.g[2]+'">동일 추정 '+esc(x.g[0])+' · '+x.g[1]+'건</span>'; cls=' class="grouped g'+x.g[2]+'"'; }
  else if(x.lk){ cell='<span class="grp maybe">조건 유사</span>'; }
  return '<tr'+cls+'>'
    +'<td data-l="묶음">'+cell+'</td>'
    +'<td data-l="상태"><span class="badge st-'+esc(x.s.toLowerCase())+'">'+esc(stLabel(x.s))+'</span></td>'
    +'<td data-l="가격" class="price num">'+money(x.p)+'</td>'
    +'<td data-l="평형" class="num">'+(x.y?x.y+'평':'-')+'</td>'
    +'<td data-l="전용" class="num">'+(x.ae??'-')+'㎡</td>'
    +'<td data-l="동">'+esc(x.b)+'</td>'
    +'<td data-l="층">'+esc(x.fl)+'</td>'
    +'<td data-l="특징" class="feat">'+esc(x.fd)+'</td>'
    +'<td data-l="최초 확인" class="c-first">'+esc(x.fs)+'</td>'
    +'<td data-l="마지막 확인">'+esc(x.ls)+'</td>'
    +'<td data-l="미노출" class="num c-miss">'+x.mc+'</td>'
    +'<td data-l="링크"><a href="'+esc(x.u||'#')+'" target="_blank" rel="noopener">매물 보기</a></td></tr>';
}
function headHtml(){
  const th=(l,k,cls)=>k?'<th class="s '+(cls||'')+(sortState.key===k?' on':'')+'" data-sort="'+k+'">'+l+'<span class="ind">'+ind(k,sortState)+'</span></th>'
                      :'<th class="'+(cls||'')+'">'+l+'</th>';
  return '<thead><tr>'+th('묶음','')+th('상태','s')+th('가격','p','num')+th('평형','y','num')+th('전용','ae','num')
    +th('동','b')+th('층','fn')+th('특징','fd')+th('최초 확인','fs')+th('마지막 확인','ls')+th('미노출','mc','num')+th('링크','')+'</tr></thead>';
}
function dupsHtml(no){
  const gs=DUPS[no];
  if(!gs||!gs.length) return '';
  return '<details class="dups"><summary>동일 추정 '+gs.length+'묶음 상세</summary><ul>'
    +gs.map(g=>'<li><b>'+esc(g.l)+'</b> · '+esc(g.b)+'동 — '+esc(g.m.join(' / '))+'</li>').join('')+'</ul></details>';
}
function renderSections(rows,stats){
  const order=stats.slice().sort((a,b)=>b.cnt-a.cnt||String(a.name).localeCompare(String(b.name),'ko'));
  const by=new Map();
  for(const x of rows){ if(!by.has(x.c)) by.set(x.c,[]); by.get(x.c).push(x); }
  if(!order.length){ $('sections').innerHTML='<section class="panel"><div class="empty">조건에 맞는 매물이 없습니다.</div></section>'; return; }
  const shown=order.slice(0,visibleSections);
  const hidden=order.length-shown.length;
  $('sections').innerHTML=shown.map(s=>{
    const items=sortRows(by.get(s.no)||[]);
    const isClosed=closed.has(s.no);
    const limit=rowLimit.get(s.no)||ROW_STEP;
    const vis=items.slice(0,limit);
    const rest=items.length-vis.length;
    const tags='<span class="tag acc">'+items.length+'건</span>'
      +'<span class="tag">실매물 추정 '+s.homes+'</span>'
      +(s.dup?'<span class="tag">동일 추정 '+s.dup+'건</span>':'')
      +'<span class="tag">'+money(s.min)+' ~ '+money(s.max)+'</span>';
    const head='<div class="cx-head" data-toggle="'+esc(s.no)+'"><span class="caret">'+(isClosed?'▸':'▾')+'</span>'
      +'<span class="nm">'+esc(s.name)+'</span>'
      +'<span class="meta">'+esc(s.dong)+(s.hh?' · '+s.hh.toLocaleString()+'세대':'')+(s.yr?' · '+esc(s.yr)+'년':'')+'</span>'
      +'<span class="right">'+tags+'</span></div>';
    const body=isClosed?'':'<div class="cx-body"><div class="table-wrap"><table>'+headHtml()+'<tbody>'
      +vis.map(rowHtml).join('')+'</tbody></table></div>'
      +(rest>0?'<div class="cx-foot"><button class="btn" type="button" data-more="'+esc(s.no)+'">+ '+Math.min(ROW_STEP,rest)+'건 더보기 (남은 '+rest+'건)</button></div>':'')
      +dupsHtml(s.no)+'</div>';
    return '<section class="panel cx'+(isClosed?' closed':'')+'">'+head+body+'</section>';
  }).join('')
  +(hidden>0?'<section class="panel"><div class="cx-foot" style="border:0"><button class="btn" type="button" id="moreSections">+ 단지 '+Math.min(SECTION_STEP,hidden)+'개 더보기 (남은 '+hidden+'개)</button></div></section>':'');
}

/* ---------- 렌더 ---------- */
function render(){
  const base=baseRows();
  renderDongChips(base);
  const afterD=afterDong(base);
  renderCxChips(afterD);
  const rows=finalRows(afterD);
  renderActiveBar();

  const live=rows.filter(x=>x.s==='ACTIVE'||x.s==='RELISTED');
  $('sLive').textContent=live.length;
  $('sHomes').textContent=new Set(live.map(x=>x.h)).size;
  $('sCx').textContent=new Set(rows.map(x=>x.c)).size;
  $('sRe').textContent=rows.filter(x=>x.s==='RELISTED').length;
  $('sCand').textContent=rows.filter(x=>x.s==='OFF_MARKET_CANDIDATE').length;
  $('sOff').textContent=rows.filter(x=>x.s==='OFF_MARKET').length;

  syncSort();
  const stats=summarize(rows);
  renderSummary(stats);
  renderSections(rows,stats);
}
function reset(view){ if(view){visibleSections=SECTION_STEP; rowLimit.clear();} render(); }

/* ---------- 이벤트 ---------- */
$('dongChips').addEventListener('click',e=>{
  const b=e.target.closest('[data-dong]'); if(!b) return;
  const d=b.dataset.dong;
  if(!d){dongSel.clear();cxSel.clear();}
  else{ dongSel.has(d)?dongSel.delete(d):dongSel.add(d); }
  reset(true);
});
$('cxChips').addEventListener('click',e=>{
  const b=e.target.closest('[data-cx]'); if(!b) return;
  const c=b.dataset.cx;
  if(!c) cxSel.clear();
  else cxSel.has(c)?cxSel.delete(c):cxSel.add(c);
  reset(true);
});
$('summary').addEventListener('click',e=>{
  const th=e.target.closest('[data-sum]');
  if(th){ const k=th.dataset.sum;
    sumSort=sumSort.key===k?{key:k,dir:sumSort.dir==='asc'?'desc':'asc'}:{key:k,dir:(k==='name'||k==='dong')?'asc':'desc'};
    render(); return; }
  const tr=e.target.closest('[data-pick]');
  if(tr){ const no=tr.dataset.pick;
    cxSel.clear(); cxSel.add(no);
    const c=CX.get(no); if(c&&c.dong&&dongSel.size&&!dongSel.has(c.dong)) dongSel.add(c.dong);
    reset(true);
    document.getElementById('sections').scrollIntoView({behavior:'smooth',block:'start'});
  }
});
$('sections').addEventListener('click',e=>{
  const th=e.target.closest('[data-sort]');
  if(th){ const k=th.dataset.sort;
    sortState=sortState.key===k?{key:k,dir:sortState.dir==='asc'?'desc':'asc'}:{key:k,dir:'asc'};
    render(); return; }
  const more=e.target.closest('[data-more]');
  if(more){ const no=more.dataset.more; rowLimit.set(no,(rowLimit.get(no)||ROW_STEP)+ROW_STEP); render(); return; }
  if(e.target.closest('#moreSections')){ visibleSections+=SECTION_STEP; render(); return; }
  const head=e.target.closest('[data-toggle]');
  if(head){ const no=head.dataset.toggle; closed.has(no)?closed.delete(no):closed.add(no); render(); }
});
$('activeBar').addEventListener('click',e=>{
  const b=e.target.closest('[data-clear]'); if(!b) return;
  const t=b.dataset.clear, v=b.dataset.value;
  if(t==='all'){ $('reset').click(); return; }
  if(t==='dong'){ dongSel.delete(v); }
  else if(t==='cx'){ cxSel.delete(v); }
  else { $(t).value=''; }
  reset(true);
});
$('summaryHead').addEventListener('click',()=>$('summaryPanel').classList.toggle('closed'));
$('reset').addEventListener('click',()=>{
  dongSel.clear(); cxSel.clear(); closed.clear();
  for(const id of ['fPrice','fPyeong','fStatus','fGroup','fQ']) $(id).value='';
  sortState={key:'p',dir:'asc'};
  reset(true);
});
for(const id of ['fPrice','fPyeong','fStatus','fGroup']) $(id).addEventListener('change',()=>reset(true));
$('fSort').addEventListener('change',()=>{
  const [k,d]=$('fSort').value.split(':');
  sortState={key:k,dir:d};
  render();
});
let qTimer; $('fQ').addEventListener('input',()=>{clearTimeout(qTimer);qTimer=setTimeout(()=>reset(true),180);});

render();
</script>
</main></body></html>"""


if __name__ == "__main__":
    main()
