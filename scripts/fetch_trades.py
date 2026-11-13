#!/usr/bin/env python
"""국토교통부 아파트 매매 실거래가를 받아 data/trades.json에 쌓는다.

네이버에서 긁지 않고 공공데이터포털 공개 API를 쓴다. 실거래 원본이 국토부이고,
API 키만 있으면 차단 걱정 없이 정식으로 받을 수 있기 때문이다.

  https://www.data.go.kr/data/15126468/openapi.do  (국토교통부_아파트 매매 실거래가 상세 자료)

환경변수:
  MOLIT_API_KEY    공공데이터포털 인증키. 없으면 아무것도 하지 않고 정상 종료한다.
  TRADES_MONTHS    받아올 개월 수(기본 8). 신고 지연 30일 + 철회 판정 6개월을 덮는다.
  TRADES_FILE      출력 경로(기본 data/trades.json)
  APT_TARGETS      수집 대상 단지 JSON(기본 src/main/resources/targets/dongjak-500.json)

계약 신고 기한이 30일이라 최근 두 달치는 나중에 더 늘어난다. 그래서 매번 전체
구간을 다시 받아 같은 거래는 덮어쓴다(해제 신고가 뒤늦게 붙는 경우도 반영된다).
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

TARGETS = Path(os.getenv("APT_TARGETS") or "src/main/resources/targets/dongjak-500.json")
OUT = Path(os.getenv("TRADES_FILE") or "data/trades.json")
MONTHS = int(os.getenv("TRADES_MONTHS") or 8)

# 상세 자료(Dev)가 활용신청 상태에 따라 막히는 경우가 있어, 기본 API로 한 번 더 시도한다.
ENDPOINTS = [
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
]


def months_back(n):
    """오늘부터 거슬러 n개월치 YYYYMM 목록(과거 → 현재)."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def lawd_codes():
    """대상 단지가 속한 시군구 코드(법정동코드 앞 5자리)."""
    spec = json.loads(TARGETS.read_text(encoding="utf-8"))
    codes = {}
    for region in spec.get("regions", []):
        cortar = str(region.get("cortarNo") or "")
        if len(cortar) >= 5:
            codes.setdefault(cortar[:5], region.get("name") or "")
    return codes


def build_url(endpoint, key, lawd_cd, ymd, page):
    params = {
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": ymd,
        "numOfRows": "1000",
        "pageNo": str(page),
    }
    query = urllib.parse.urlencode(params)
    # 포털이 인증키를 Encoding/Decoding 두 벌로 준다. %가 있으면 이미 인코딩된 키로 보고
    # 그대로 붙인다(한 번 더 인코딩하면 서명이 깨진다).
    service_key = key if "%" in key else urllib.parse.quote(key, safe="")
    return f"{endpoint}?serviceKey={service_key}&{query}"


def get(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "apt-price-tracker"})
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):  # 키 문제는 재시도해도 같다
                return e.read().decode("utf-8", "replace")
            if attempt == 2:
                raise
        except Exception:
            if attempt == 2:
                raise
        time.sleep(1.5 * (attempt + 1))
    return ""


def text_of(node, tag):
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def to_int(raw):
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return int(digits) if digits else None


def parse(xml_text):
    """(거래 목록, 전체 건수, 오류 메시지)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return [], 0, f"XML 파싱 실패: {e}"

    code = text_of(root, ".//resultCode") or text_of(root, ".//returnReasonCode")
    msg = text_of(root, ".//resultMsg") or text_of(root, ".//returnAuthMsg")
    if code and code not in ("00", "000"):
        return [], 0, f"{code} {msg}"

    rows = []
    for item in root.findall(".//item"):
        price = to_int(text_of(item, "dealAmount"))
        year, month, day = (text_of(item, "dealYear"), text_of(item, "dealMonth"), text_of(item, "dealDay"))
        if not (price and year and month and day):
            continue
        area = text_of(item, "excluUseAr")
        rows.append({
            "apt": text_of(item, "aptNm"),
            "umd": text_of(item, "umdNm"),
            "dong": text_of(item, "aptDong"),
            "jibun": text_of(item, "jibun"),
            "area": round(float(area), 2) if area else None,
            "floor": to_int(text_of(item, "floor")),
            "price": price,
            "date": f"{int(year):04d}-{int(month):02d}-{int(day):02d}",
            "buildYear": to_int(text_of(item, "buildYear")),
            # 해제(계약 취소)된 거래는 매칭에서 빼야 한다. 뒤늦게 신고되는 경우가 있다.
            "cancelled": text_of(item, "cdealType").upper() == "O",
            "cancelDate": text_of(item, "cdealDay") or None,
        })
    return rows, to_int(text_of(root, ".//totalCount")) or len(rows), ""


def trade_id(lawd_cd, row):
    return "|".join([
        lawd_cd, row["date"], row["apt"], str(row["area"]), str(row["floor"]), str(row["price"]),
    ])


def main():
    key = (os.getenv("MOLIT_API_KEY") or "").strip()
    if not key:
        print("MOLIT_API_KEY가 없어 실거래 수집을 건너뜁니다.")
        print("  공공데이터포털에서 '아파트 매매 실거래가 상세 자료' 활용신청 후 키를 넣으세요.")
        return 0

    if not TARGETS.exists():
        print(f"대상 단지 파일이 없습니다: {TARGETS}", file=sys.stderr)
        return 1

    codes = lawd_codes()
    if not codes:
        print("대상 파일에서 시군구 코드를 찾지 못했습니다.", file=sys.stderr)
        return 1

    existing = {}
    if OUT.exists():
        for row in json.loads(OUT.read_text(encoding="utf-8")):
            existing[row["id"]] = row

    endpoint = None
    added = updated = 0
    failures = []

    for lawd_cd in sorted(codes):
        for ymd in months_back(MONTHS):
            page, total, seen = 1, None, 0
            while True:
                text = err = None
                # 첫 호출에서 쓸 수 있는 엔드포인트를 정하고, 이후로는 그것만 쓴다.
                for candidate in ([endpoint] if endpoint else ENDPOINTS):
                    text = get(build_url(candidate, key, lawd_cd, ymd, page))
                    rows, total_count, err = parse(text)
                    if not err:
                        endpoint = candidate
                        break
                if err:
                    failures.append(f"{lawd_cd} {ymd} p{page}: {err}")
                    break

                for row in rows:
                    row_id = trade_id(lawd_cd, row)
                    record = dict(row, id=row_id, lawdCd=lawd_cd)
                    if row_id not in existing:
                        added += 1
                    elif existing[row_id] != record:
                        updated += 1
                    existing[row_id] = record

                seen += len(rows)
                total = total_count if total is None else total
                if seen >= (total or 0) or not rows:
                    break
                page += 1
                time.sleep(0.3 + random.random() * 0.3)

            time.sleep(0.3 + random.random() * 0.3)

    if failures:
        for line in failures[:5]:
            print(f"  조회 실패 {line}", file=sys.stderr)
        if not existing:
            print(f"실거래를 한 건도 받지 못했습니다({len(failures)}건 실패).", file=sys.stderr)
            return 1

    rows = sorted(existing.values(), key=lambda r: (r["date"], r["apt"], r["price"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    live = sum(1 for r in rows if not r["cancelled"])
    print(f"실거래 {len(rows)}건 저장 (유효 {live} · 해제 {len(rows) - live}) → {OUT}")
    print(f"  신규 {added} · 갱신 {updated} · 구간 {months_back(MONTHS)[0]}~{months_back(MONTHS)[-1]}"
          + (f" · 실패 {len(failures)}" if failures else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
