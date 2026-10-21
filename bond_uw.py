#!/usr/bin/env python3
"""채권 인수기관 조회 (OpenDART 사용, 표준 라이브러리만 필요)

사용법
  python bond_uw.py 비씨카드 189-1 20260420     회사명 회차 발행일(YYYYMMDD)
  python bond_uw.py 비씨카드 189-1              발행일 생략 시 최근 2년 안에서 검색합니다 (느립니다)
  python bond_uw.py KR6063621G20                종목코드(ISIN)만 입력: 회사명/회차/발행일을 자동으로 찾습니다
                                                (공공데이터포털 인증키가 필요합니다)
  python bond_uw.py                             대화형: 계속 입력하며 조회합니다

API 키는 환경변수 DART_API_KEY 또는 ~/.dart_bond_lookup/key.txt 에서 읽습니다.
둘 다 없으면 처음 한 번 물어본 뒤 key.txt 에 저장합니다. (키는 사람마다 각자 발급받아 사용합니다)
"""
import io
import json
import os
import math
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

API = "https://opendart.fss.or.kr/api"
HOME = Path.home() / ".dart_bond_lookup"
NO_SERIES = "(회차 표기 없음)"


# ---------------------------------------------------------------- 기본 도구
def load_key():
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        return key
    f = HOME / "key.txt"
    if f.exists():
        key = f.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = input("OpenDART API 키를 입력해 주세요 (한 번만 묻고 저장합니다): ").strip()
    if not key:
        sys.exit("API 키가 필요합니다.")
    HOME.mkdir(parents=True, exist_ok=True)
    f.write_text(key, encoding="utf-8")
    return key


def http_get(path, params, timeout=60):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def api_json(path, params):
    return json.loads(http_get(path, params).decode("utf-8"))


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w, right=False):
    gap = max(0, w - width(s))
    return (" " * gap + s) if right else (s + " " * gap)


def to_num(s):
    t = re.sub(r"[^0-9.\-]", "", s or "")
    if t in ("", "-", "."):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return int(v) if v == int(v) else v


def norm_series(s):
    return re.sub(r"[‐-―－]", "-", s).replace(" ", "").strip()


def norm_date(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) != 8:
        sys.exit("발행일은 YYYYMMDD 형식으로 입력해 주세요. 예: 20260420")
    return d


# ---------------------------------------------------------------- 회사 코드
def load_corps(key):
    cache = HOME / "corps.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 30 * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    print("회사 코드 목록을 처음 한 번 내려받는 중... (수십 초 걸릴 수 있습니다)")
    raw = http_get("corpCode.xml", {"crtfc_key": key}, timeout=300)
    if not raw.startswith(b"PK"):
        raise RuntimeError("회사 코드 목록 오류: " + raw[:200].decode("utf-8", "ignore"))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    corps = []
    for e in ET.fromstring(xml).iter("list"):
        corps.append([
            (e.findtext("corp_code") or "").strip(),
            (e.findtext("corp_name") or "").strip(),
            (e.findtext("modify_date") or "").strip(),
        ])
    HOME.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(corps, ensure_ascii=False), encoding="utf-8")
    return corps


def _akey(s):
    return re.sub(r"\s+", "", s or "").lower()


def load_aliases():
    """~/.dart_bond_lookup/aliases.json : {"입력 이름": "DART 등록 이름"}"""
    try:
        d = json.loads((HOME / "aliases.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_aliases(d):
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "aliases.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_alias(name):
    k = _akey(name)
    for src, dst in load_aliases().items():
        if _akey(src) == k:
            return dst
    return name


# 종목명에는 한글 발음으로, DART 에는 영문으로 등록된 경우(또는 그 반대)를 자동으로 시도한다.
NAME_VARIANTS = [
    ("씨제이", "CJ"), ("에스케이", "SK"), ("엘지", "LG"), ("케이비", "KB"), ("엔에이치", "NH"),
    ("지에스", "GS"), ("케이티", "KT"), ("에이치디", "HD"), ("디비", "DB"), ("엠비케이", "MBK"),
]


def name_variants(name):
    out = []
    for ko, en in NAME_VARIANTS:
        if ko in name:
            out.append(name.replace(ko, en))
        if en in name:
            out.append(name.replace(en, ko))
    return [v for i, v in enumerate(out) if v != name and v not in out[:i]]


def find_corp(corps, name, log=print):
    name = apply_alias(name)
    n = _akey(name)
    for cand in [n] + [_akey(v) for v in name_variants(name)]:
        exact = [c for c in corps if _akey(c[1]) == cand]
        if exact:
            return sorted(exact, key=lambda c: c[2], reverse=True)
    part = [c for c in corps if n in _akey(c[1])]
    if len(part) == 1:
        return part
    if part:
        log(f"'{name}' 이름이 정확히 일치하는 회사가 없습니다. 비슷한 후보:")
        for c in part[:15]:
            log("  - " + c[1])
        log("DART에 등록된 정식 이름으로 다시 입력해 주세요. (자주 쓰는 변환은 [별칭 관리]에 등록해 두시면 됩니다)")
    else:
        log(f"'{name}' 와 일치하는 회사를 찾지 못했습니다. 정식 이름을 확인해 주세요. "
            "(자주 쓰는 변환은 [별칭 관리]에 등록해 두시면 됩니다)")
    return []


# ---------------------------------------------------------------- 공시 검색
def filing_windows(issue_date):
    """(시작, 끝) 날짜 구간을 차례로 만든다."""
    if issue_date:
        d = datetime.strptime(issue_date, "%Y%m%d").date()
        yield d - timedelta(days=1), d + timedelta(days=7)
        return
    end = date.today()
    for _ in range(8):  # 90일 x 8 = 약 2년
        start = end - timedelta(days=89)
        yield start, end
        end = start - timedelta(days=1)


def list_filings(key, corp_code, start, end):
    found = {}
    page = 1
    while True:
        j = api_json("list.json", {
            "crtfc_key": key, "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
            "pblntf_ty": "C", "page_no": page, "page_count": 100,
        })
        st = j.get("status")
        if st == "013":
            break
        if st != "000":
            raise RuntimeError(f"OpenDART 오류 {st}: {j.get('message')}")
        for it in j.get("list", []):
            if "증권발행실적보고서" in it["report_nm"]:
                found[it["rcept_no"]] = it
        if page >= int(j.get("total_page", 1)):
            break
        page += 1
    return sorted(found.values(), key=lambda x: x["rcept_no"], reverse=True)


# ---------------------------------------------------------------- 본문 파싱
class Flatten(HTMLParser):
    """DART 본문 XML을 문단("p")과 표의 행("row") 순서열로 펼친다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.buf = []
        self.rows = []
        self.cells = []

    def _flush(self):
        t = clean("".join(self.buf))
        if t:
            self.items.append(("p", t))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("p", "br", "table", "title"):
            if not self.rows:
                self._flush()
        elif t == "tr":
            self.rows.append([])
            self.cells.append(None)
        elif t in ("td", "th", "te", "tu") and self.rows:
            self.cells[-1] = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("td", "th", "te", "tu") and self.rows and self.cells[-1] is not None:
            self.rows[-1].append(clean("".join(self.cells[-1])))
            self.cells[-1] = None
        elif t == "tr" and self.rows:
            row = self.rows.pop()
            self.cells.pop()
            self.items.append(("row", row))
        elif t in ("p", "table") and not self.rows:
            self._flush()

    def handle_data(self, data):
        if not self.rows:
            self.buf.append(data)
        elif self.cells[-1] is not None:
            self.cells[-1].append(data)

    def close(self):
        super().close()
        self._flush()


def clean(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def decode(b):
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "ignore")


def fetch_items(key, rcept_no):
    raw = http_get("document.xml", {"crtfc_key": key, "rcept_no": rcept_no}, timeout=120)
    if not raw.startswith(b"PK"):
        head = raw[:400].decode("utf-8", "ignore")
        m = re.search(r"<status>(\d+)</status>\s*<message>([^<]*)", head)
        if m:
            raise RuntimeError(f"공시 원문을 받을 수 없습니다 ({m.group(1)} {m.group(2).strip()})")
        raise RuntimeError("본문을 받지 못했습니다: " + head[:200])
    items = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if name.lower().endswith((".xml", ".htm", ".html")):
                p = Flatten()
                p.feed(decode(z.read(name)))
                p.close()
                items.extend(p.items)
    return items


SERIES_RE = re.compile(r"회\s*차\s*[:：]\s*([0-9A-Za-z][0-9A-Za-z‐-―－\-]*)")


def extract(items):
    """{회차: {"rows": [(기관, 수량, 금액, 비율)], "total": (수량, 금액, 비율)}} 를 돌려준다."""
    out = {}
    cur = None
    idx = None
    for kind, val in items:
        text = val if kind == "p" else " ".join(val)
        m = SERIES_RE.search(text)
        if m:
            cur = norm_series(m.group(1))
            idx = None
            continue
        if kind != "row":
            continue
        heads = [h.replace(" ", "") for h in val]
        if "인수기관" in heads and "인수수량" in heads:
            idx = {
                "name": heads.index("인수기관"),
                "qty": heads.index("인수수량"),
                "amt": heads.index("인수금액") if "인수금액" in heads else None,
                "pct": next((i for i, h in enumerate(heads) if h.startswith("비율")), None),
            }
            out.setdefault(cur or NO_SERIES, {"rows": [], "total": None})
            continue
        if idx is None:
            continue
        key = cur or NO_SERIES
        need = max(i for i in idx.values() if i is not None)
        if len(val) <= need:
            continue
        first = val[idx["name"]].replace(" ", "")
        rec = (
            val[idx["name"]],
            to_num(val[idx["qty"]]),
            to_num(val[idx["amt"]]) if idx["amt"] is not None else None,
            to_num(val[idx["pct"]]) if idx["pct"] is not None else None,
        )
        if first == "계":
            out[key]["total"] = rec[1:]
            idx = None
        else:
            out[key]["rows"].append(rec)
    return out


# ---------------------------------------------------------------- 출력
def eok(n):
    if n is None:
        return ""
    v = n / 1e8
    return (f"{v:,.2f}".rstrip("0").rstrip(".")) + "억"


def show(corp_name, series, filing, data):
    print()
    print(f"{corp_name} {series} 인수기관   (접수 {filing['rcept_dt']})")
    rows = list(data["rows"])
    total = data["total"]
    head = ["인수기관", "인수수량", "인수금액", "", "비율(%)"]
    body = [[n, f"{q:,}" if q is not None else "", f"{a:,}" if a is not None else "",
             eok(a), f"{p:.2f}" if p is not None else ""] for n, q, a, p in rows]
    if total:
        q, a, p = total
        body.append(["계", f"{q:,}" if q is not None else "", f"{a:,}" if a is not None else "",
                     eok(a), f"{p:.2f}" if p is not None else ""])
    cols = list(zip(head, *body)) if body else [(h,) for h in head]
    ws = [max(width(c) for c in col) for col in cols]
    right = [False, True, True, True, True]
    print("  " + "  ".join(pad(h, ws[i], right[i]) for i, h in enumerate(head)))
    print("  " + "  ".join("-" * ws[i] for i in range(len(head))))
    for r in body:
        print("  " + "  ".join(pad(c, ws[i], right[i]) for i, c in enumerate(r)))
    print(f"\n출처: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filing['rcept_no']}")


# ---------------------------------------------------------------- 종목코드(ISIN) -> 회사명/회차/발행일
# 공공데이터포털 "금융위원회_채권기본정보"(원천: 한국예탁결제원) 사용. data.go.kr 인증키가 따로 필요하다.
ISIN_RE = re.compile(r"^KR[0-9A-Z]{10}$")
_V2 = "https://apis.data.go.kr/1160100/GetBondIssuInfoService_V2"
DATAGO_ENDPOINTS = [  # 서비스 주소는 확인됨, 오퍼레이션 이름은 후보를 차례로 시도
    f"{_V2}/getBondBasiInfo_V2",
    f"{_V2}/getBondBasiInfo",
    f"{_V2}/getBondIssuInfo_V2",
    f"{_V2}/getBondIssuInfo",
]


def load_datago_key(ask=input):
    key = os.environ.get("DATAGO_API_KEY", "").strip()
    f = HOME / "datago_key.txt"
    if not key and f.exists():
        key = f.read_text(encoding="utf-8").strip()
    if not key:
        key = (ask("공공데이터포털(data.go.kr) 인증키를 입력해 주세요 (한 번만 묻고 저장합니다): ") or "").strip()
        if key:
            HOME.mkdir(parents=True, exist_ok=True)
            f.write_text(key, encoding="utf-8")
    return urllib.parse.unquote(key)  # Encoding 키를 붙여넣어도 동작하게


def pick(item, names):
    for n in names:
        v = item.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def parse_bond_name(name):
    """'한국서부발전76-1' -> ('한국서부발전', '76-1')"""
    name = re.sub(r"\s+", "", name)
    last = None
    for last in re.finditer(r"\d+(?:-\d+)?", name):
        pass
    if not last:
        return name, ""
    return re.sub(r"제$", "", name[: last.start()]), last.group(0)


def clean_company(s):
    return re.sub(r"\(주\)|㈜|주식회사", "", s).strip()


def datago_urls():
    """~/.dart_bond_lookup/datago_url.txt (또는 환경변수 DATAGO_URL)에 요청주소를 넣으면 그걸 먼저 쓴다."""
    urls = []
    custom = os.environ.get("DATAGO_URL", "").strip()
    f = HOME / "datago_url.txt"
    if not custom and f.exists():
        custom = f.read_text(encoding="utf-8").strip()
    if custom:
        urls.append(custom.split("?")[0])
    return urls + [u for u in DATAGO_ENDPOINTS if u not in urls]


def remember_url(url):
    """한 번 성공한 주소를 저장해서 다음부터 실패하는 후보를 건너뛴다."""
    f = HOME / "datago_url.txt"
    try:
        if not f.exists() or f.read_text(encoding="utf-8").strip().split("?")[0] != url:
            HOME.mkdir(parents=True, exist_ok=True)
            f.write_text(url, encoding="utf-8")
    except OSError:
        pass


def isin_items(datago_key, isin):
    return datago_items(datago_key, {"isinCd": isin}, rows=1)


def datago_items(datago_key, extra, rows=1, page=1):
    """채권기본정보 API 호출 결과(items)만 돌려준다. 전체 건수는 datago_items.total 에 넣어 둔다."""
    items, total = datago_page(datago_key, extra, rows, page)
    datago_items.total = total
    return items


def datago_page(datago_key, extra, rows=1, page=1):
    """채권기본정보 API 한 페이지 호출. (items, totalCount) 를 돌려준다. 스레드에서 써도 안전하다."""
    errors = []
    for url in datago_urls():
        last_err = None
        q = urllib.parse.urlencode({
            "serviceKey": datago_key, "pageNo": page, "numOfRows": rows,
            "resultType": "json", **extra,
        })
        try:
            req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            errors.append(f"{url}\n    -> HTTP {e.code} {body}".rstrip())
            continue
        try:
            resp = json.loads(raw).get("response") or {}
        except ValueError:
            errors.append(f"{url}\n    -> {raw[:200]}")  # 키 오류 등은 XML 로 돌아온다
            continue
        head = resp.get("header") or {}
        code = str(head.get("resultCode", ""))
        if code not in ("00", "0", "000"):
            errors.append(f"{url}\n    -> {code} {head.get('resultMsg', '')}")
            continue
        items = (resp.get("body") or {}).get("items") or {}
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        remember_url(url)
        return items, int((resp.get("body") or {}).get("totalCount") or 0)
    raise RuntimeError("종목코드 조회 실패. 시도한 주소와 응답:\n  " + "\n  ".join(errors))


CACHE_LOCK = threading.Lock()


def isin_cache():
    try:
        return json.loads((HOME / "isin_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_isin_cache(cache):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / "isin_cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def resolve_isin(datago_key, isin):
    with CACHE_LOCK:
        hit = isin_cache().get(isin)
    if hit:  # 이미 찾아본 종목코드는 바로 반환
        return hit
    items = isin_items(datago_key, isin)
    if not items:
        raise RuntimeError("해당 종목코드 정보가 없습니다. 코드를 확인해 주세요. (만기가 지난 채권은 조회되지 않을 수 있습니다)")
    it = items[0]
    name = pick(it, ["isinCdNm", "bondNm", "itmsNm"])
    api_issuer = clean_company(pick(it, ["bondIsurNm", "isurNm"]))
    issued = re.sub(r"\D", "", pick(it, ["bondIssuDt", "issuDt"]))
    issuer, series = parse_bond_name(name)
    if not (name and series and len(issued) == 8):
        raise RuntimeError("응답에서 종목명/발행일을 읽지 못했습니다. 받은 내용을 그대로 전달해 주세요: "
                           + json.dumps(it, ensure_ascii=False)[:700])
    names = []
    for n in (issuer, api_issuer):
        if n and n not in names:
            names.append(n)
    info = {"name": name, "issuers": names, "series": series, "date": issued}
    with CACHE_LOCK:
        cache = isin_cache()
        cache[isin] = info
        save_isin_cache(cache)
    return info


def lookup_isin(key, corps, datago_key, isin):
    info = resolve_isin(datago_key, isin)
    print(f"{isin} -> {info['issuers'][0]} {info['series']} {info['date']}  (종목명: {info['name']})")
    for nm in info["issuers"]:
        if lookup(key, corps, nm, info["series"], info["date"]):
            return True
    return False


# ---------------------------------------------------------------- 일괄 조회용 (스레드 안전)
def _doc_path(rcept_no):
    return HOME / "docs" / f"{re.sub(r'[^0-9A-Za-z]', '', rcept_no)}.json"


def load_doc_disk(rcept_no):
    try:
        return json.loads(_doc_path(rcept_no).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_doc_disk(rcept_no, data):
    if not data:  # 표를 못 읽은 결과는 저장하지 않는다 (다시 시도할 수 있게)
        return
    try:
        path = _doc_path(rcept_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


class DartCache:
    """같은 공시(한 공시에 여러 회차가 들어 있음)를 여러 종목이 한 번만 내려받도록 공유하는 캐시."""

    LIST_TTL = 600  # 공시 목록은 새 공시가 올라올 수 있어서 10분만 재사용한다

    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}
        self._docs = {}
        self._lists = {}

    def _lock(self, k):
        with self._guard:
            return self._locks.setdefault(k, threading.Lock())

    def doc(self, key, rcept_no):
        with self._lock(("doc", rcept_no)):
            if rcept_no not in self._docs:
                d = load_doc_disk(rcept_no)  # 한 번 읽은 공시는 내용이 바뀌지 않으니 PC에 저장해 둔다
                if d is None:
                    d = extract(fetch_items(key, rcept_no))
                    save_doc_disk(rcept_no, d)
                self._docs[rcept_no] = d
            return self._docs[rcept_no]

    def filings(self, key, corp_code, start, end):
        k = ("list", corp_code, start, end)
        with self._lock(k):
            hit = self._lists.get(k)
            if hit is None or time.time() - hit[0] > self.LIST_TTL:
                hit = (time.time(), list_filings(key, corp_code, start, end))
                self._lists[k] = hit
            return hit[1]

    def day_filings(self, key, start, end):
        """기간 안에 접수된 모든 회사의 증권발행실적보고서 (회사 코드 목록이 필요 없다)"""
        k = ("all", start, end)
        with self._lock(k):
            hit = self._lists.get(k)
            if hit is None or time.time() - hit[0] > self.LIST_TTL:
                hit = (time.time(), list_all_filings(key, start, end))
                self._lists[k] = hit
            return hit[1]


def list_all_filings(key, start, end, workers=4):
    """start~end 에 접수된 증권발행실적보고서를 회사 구분 없이 모두 가져온다. (여러 쪽은 동시에 받는다)"""
    def get(pg):
        j = api_json("list.json", {
            "crtfc_key": key, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
            "pblntf_ty": "C", "page_no": pg, "page_count": 100,
        })
        st = j.get("status")
        if st == "013":
            return {"list": [], "total_page": 1}
        if st != "000":
            raise RuntimeError(f"OpenDART 오류 {st}: {j.get('message')}")
        return j

    first = get(1)
    items = list(first.get("list", []))
    pages = int(first.get("total_page", 1) or 1)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for j in ex.map(get, range(2, pages + 1)):
                items += j.get("list", [])
    return [i for i in items if "증권발행실적보고서" in i.get("report_nm", "")]


def match_corp_filings(name, filings):
    """공시 목록에서 회사명이 맞는 것만 고른다. (별칭, 씨제이/CJ 같은 표기 차이도 반영)"""
    name = apply_alias(name)
    keys = [_akey(clean_company(name))] + [_akey(clean_company(v)) for v in name_variants(name)]
    keys = [k for k in keys if k]

    def ck(f):
        return _akey(clean_company(f.get("corp_name", "")))

    exact = [f for f in filings if ck(f) in keys]
    if exact:
        return exact
    return [f for f in filings if any(k in ck(f) for k in keys)]


def _read_docs(cache, key, filings, log):
    """여러 공시를 동시에 읽는다. [(공시, 표 데이터 또는 None)]"""
    def one(f):
        try:
            return f, cache.doc(key, f["rcept_no"])
        except Exception as e:
            log(f"공시 {f['rcept_no']} 읽기 실패: {e}")
            return f, None

    if len(filings) <= 1:
        return [one(f) for f in filings]
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, filings))


def search_bond(key, corps, name, series, issue_date, cache=None, log=print):
    """(회사명, 회차, 공시, 표 데이터, 본 회차 집합). 못 찾으면 공시가 None.
    발행일이 있으면 회사 코드 목록 없이 그 기간의 공시에서 바로 찾는다. (회사 코드 목록 내려받기 생략)"""
    cache = cache or DartCache()
    series = norm_series(series)
    seen = set()
    if issue_date:
        d = datetime.strptime(issue_date, "%Y%m%d").date()
        done, any_match, pool = set(), False, {}
        for start, end in ((d, d + timedelta(days=2)), (d - timedelta(days=1), d + timedelta(days=7))):
            all_f = cache.day_filings(key, start, end)
            for f in all_f:
                pool[f["rcept_no"]] = f
            mine = [f for f in match_corp_filings(name, all_f) if f["rcept_no"] not in done]
            mine.sort(key=lambda f: f["rcept_no"], reverse=True)
            any_match = any_match or bool(mine)
            done.update(f["rcept_no"] for f in mine)
            for f, data in _read_docs(cache, key, mine, log):
                if not data:
                    continue
                seen.update(data.keys())
                if series in data:
                    return f.get("corp_name", name), series, f, data[series], seen
                if list(data.keys()) == [NO_SERIES]:
                    log("회차 표기가 없는 공시라 있는 표를 그대로 사용합니다")
                    return f.get("corp_name", name), series, f, data[NO_SERIES], seen
        if not any_match:
            n2 = _akey(clean_company(apply_alias(name)))[:2]
            near = sorted({f["corp_name"] for f in pool.values() if n2 and n2 in _akey(f.get("corp_name", ""))})
            log(f"'{name}' 회사의 증권발행실적보고서를 {d:%Y-%m-%d} 전후 기간에서 찾지 못했습니다. "
                "DART에 등록된 회사명과 발행일을 확인해 주세요. (자주 쓰는 변환은 [별칭 관리]에 등록하시면 됩니다)")
            if near:
                log("같은 기간의 비슷한 회사명: " + ", ".join(near[:10]))
        return None, series, None, None, seen
    if corps is None:
        corps = load_corps(key)
    for corp_code, corp_name, _ in find_corp(corps, name, log):
        for start, end in filing_windows(issue_date):
            mine = sorted(cache.filings(key, corp_code, start, end), key=lambda f: f["rcept_no"], reverse=True)
            for f, data in _read_docs(cache, key, mine, log):
                if not data:
                    continue
                seen.update(data.keys())
                if series in data:
                    return corp_name, series, f, data[series], seen
                if list(data.keys()) == [NO_SERIES]:
                    log("회차 표기가 없는 공시라 있는 표를 그대로 사용합니다")
                    return corp_name, series, f, data[NO_SERIES], seen
    return None, series, None, None, seen


# ---------------------------------------------------------------- 국고채 검색 (회차 -> 종목코드/종목명/발행일/만기일)
KTB_SERIES_RE = re.compile(r"(?<!\d)(\d{2})\s*-\s*(\d{1,2})(?!\d)")


def parse_ktb_series(text):
    """지저분한 입력에서 국고채 회차(예: 25-4)만 뽑는다. 순서는 유지하고 중복은 뺀다."""
    out = []
    for m in KTB_SERIES_RE.finditer(text or ""):
        sr = f"{m.group(1)}-{int(m.group(2))}"
        if sr not in out:
            out.append(sr)
    return out


def _slash(d):
    d = re.sub(r"\D", "", d or "")
    return f"{d[:4]}/{d[4:6]}/{d[6:8]}" if len(d) == 8 else ""


def _maturity_from_name(name, issued):
    """종목명 '국고02250-2806(25-4)' 의 2806 -> 2028/06, 일자는 발행일의 일자를 쓴다."""
    m = re.search(r"-(\d{2})(\d{2})(?:\(|$)", name)
    if not m or len(issued) != 8:
        return ""
    return f"20{m.group(1)}/{m.group(2)}/{issued[6:8]}"


def _ktb_row(it):
    name = pick(it, ["isinCdNm", "bondNm", "itmsNm"])
    issued = re.sub(r"\D", "", pick(it, ["bondIssuDt", "issuDt"]))
    exp = re.sub(r"\D", "", pick(it, ["bondExprDt", "exprDt", "mtrtDt", "bondMtrtDt"]))
    return {
        "isin": pick(it, ["isinCd"]),
        "name": name,
        "issued": _slash(issued),
        "maturity": _slash(exp) or _maturity_from_name(name, issued),
    }


def _ktb_cache_file():
    return HOME / "ktb_cache.json"


def ktb_kind(name):
    """종목명으로 국고채 종류를 나눈다: 일반 / 물가채 / 원금분리 / 이표분리"""
    if "물가" in name:
        return "물가채"
    if "이표" in name:
        return "이표분리"
    if re.search(r"원금|분리|스트립|strip", name, re.I):
        return "원금분리"
    return "일반"


def _is_gov(name):
    return "국고" in name or "국채" in name


def _fetch_terms(datago_key, terms, log):
    """공공데이터포털에서 종목명에 term 이 들어간 채권을 받아 {term: [행...]} 로 돌려준다."""
    out = {}
    how = None
    for term in terms:
        rows = None
        for param in ([how] if how else ["isinCdNm", "likeIsinCdNm", "likeItmsNm", "bondNm"]):
            try:
                items = datago_items(datago_key, {param: term}, rows=BOND_PAGE)
            except RuntimeError as e:
                log(f"({param} 시도 실패: {str(e).splitlines()[0]})")
                continue
            # 변수가 먹힌다고 확인된 뒤에는 결과가 0건이어도 "해당 종목 없음"으로 본다
            if all(term in pick(i, ["isinCdNm", "bondNm", "itmsNm"]) for i in items) and (items or how):
                how = param
                rows = list(items)
                total, page = datago_items.total, 1
                while page * BOND_PAGE < total and page < 20:
                    page += 1
                    rows += datago_items(datago_key, {param: term}, rows=BOND_PAGE, page=page)
                break
        if rows is None:
            break
        out[term] = [_ktb_row(i) for i in rows if _is_gov(pick(i, ["isinCdNm", "bondNm", "itmsNm"]))]
    if len(out) == len(terms):
        return out
    # 종목명 검색 변수가 적용되지 않으면 전체 목록에서 국고/국채만 추린다 (느림)
    log("종목명 검색 변수가 적용되지 않아 전체 목록에서 국고채를 추리는 중입니다. (시간이 걸립니다)")
    rows, page = [], 1
    while page <= 60:
        items = datago_items(datago_key, {}, rows=1000, page=page)
        rows += [_ktb_row(i) for i in items if _is_gov(pick(i, ["isinCdNm", "bondNm", "itmsNm"]))]
        if not items or page * 1000 >= datago_items.total:
            break
        page += 1
    return {t: rows for t in terms}


def ktb_rows_from_db():
    """오늘 받아 둔 채권 목록(일반 채권 검색용)에서 국고채만 꺼낸다."""
    if not bond_db_fresh():
        return []
    try:
        c = _bond_db()
        raws = c.execute("SELECT raw FROM bonds WHERE nkey LIKE '%국고%' OR nkey LIKE '%국채%'").fetchall()
        c.close()
    except sqlite3.Error:
        return []
    return [_ktb_row(json.loads(r[0])) for r in raws]


def load_ktb_list(datago_key, terms=("국고",), log=print, refresh=False):
    """국고채 목록을 받아 오늘자 캐시로 저장한다. 같은 날 다시 검색하면 캐시를 쓴다."""
    if not refresh:
        local = ktb_rows_from_db()
        if local:
            return local
    f = _ktb_cache_file()
    today = date.today().isoformat()
    cache = {"date": today, "terms": {}}
    if f.exists() and not refresh:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            if c.get("date") == today and isinstance(c.get("terms"), dict):
                cache = c
        except (OSError, ValueError):
            pass
    need = [t for t in terms if t not in cache["terms"]]
    if need:
        cache["terms"].update(_fetch_terms(datago_key, need, log))
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    seen, rows = set(), []
    for t in terms:
        for r in cache["terms"].get(t, []):
            if r["isin"] and r["isin"] not in seen:
                seen.add(r["isin"])
                rows.append(r)
    if not rows:
        raise RuntimeError("국고채 목록을 받지 못했습니다. 인증키와 활용신청 승인 상태를 확인해 주세요.")
    return rows


def search_ktb(datago_key, text, include_inflation=False, include_strips=False, log=print):
    """입력 문자열에서 회차를 뽑아 국고채 종목을 찾는다. (결과 행 목록, 못 찾은 회차 목록)"""
    wanted = parse_ktb_series(text)
    if not wanted:
        return [], []
    terms = ["국고"] + (["원금", "분리"] if include_strips else [])
    rows = load_ktb_list(datago_key, terms, log)
    allowed = {"일반"}
    if include_inflation:
        allowed.add("물가채")
    if include_strips:
        allowed.add("원금분리")
    found, missing = [], []
    for sr in wanted:
        pat = re.compile(r"\(" + re.escape(sr) + r"\)")
        hits = [r for r in rows if pat.search(r["name"]) and ktb_kind(r["name"]) in allowed]
        hits.sort(key=lambda r: (ktb_kind(r["name"]) != "일반", r["name"]))
        if hits:
            found.extend(dict(h, series=sr, kind=ktb_kind(h["name"])) for h in hits)
        else:
            missing.append(sr)
    return found, missing


# ---------------------------------------------------------------- 일반 채권 검색 (종목명/코드 일부로 검색)
BOND_PAGE = 5000  # 실측: 5,000건씩 받을 때 건당 속도가 가장 빠름 (1,000건 대비 약 1.6배)
# 화면 열 이름, 응답 필드 후보(앞에서부터 시도), 후보가 없을 때 필드 이름에서 찾을 패턴
BOND_COLS = [
    # (화면 열 이름, 응답 필드 후보, 예비 패턴) - 필드 이름은 실제 응답(speed_report)으로 확인한 값
    ("종목명", ["isinCdNm"], None),
    ("표준코드", ["isinCd"], None),
    ("채권종류", ["scrsItmsKcdNm"], None),
    ("채권구분", ["pclrBondKcdNm"], None),
    ("통화", ["bondIssuCurCdNm", "bondIssuCurCd"], None),
    ("발행금액", ["bondIssuAmt"], None),
    ("발행일", ["bondIssuDt"], None),
    ("만기일", ["bondExprDt"], None),
    ("지급방법", ["bondIntTcdNm"], None),
    ("지급주기", ["intPayCyclCtt"], None),
    ("표면금리", ["bondSrfcInrt"], None),
    ("민평금리", [], None),    # 공공데이터포털 응답에 없음
    ("기준금리", [], None),    # 공공데이터포털 응답에 없음
    ("신용등급", [], None),    # 신용평가사 4곳(KIS/KBP/NICE/FN) 값을 합쳐서 채움
    ("공사모", ["bondOffrMcdNm"], None),
    ("순위구분", ["bondRnknDcdNm"], None),
    ("보증유형", ["grnDcdNm"], None),
]
BOND_DATE_COLS = {"발행일", "만기일"}
RATING_KEYS = [("KIS", "kisScrsItmsKcdNm"), ("KBP", "kbpScrsItmsKcdNm"),
               ("NICE", "niceScrsItmsKcdNm"), ("FN", "fnScrsItmsKcdNm")]
_COL_OVERRIDE = None


def _col_override():
    """~/.dart_bond_lookup/columns.json 으로 열마다 쓸 응답 필드를 바꿀 수 있다.
    예) {"채권구분": ["bondIntTcdNm"], "지급방법": ["pamtRdptMcdNm"]}"""
    global _COL_OVERRIDE
    if _COL_OVERRIDE is None:
        try:
            d = json.loads((HOME / "columns.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        _COL_OVERRIDE = ({k: (v if isinstance(v, list) else [v]) for k, v in d.items()}
                         if isinstance(d, dict) else {})
    return _COL_OVERRIDE


def _ratings(it):
    vals = [(n, str(it.get(k) or "").strip()) for n, k in RATING_KEYS]
    vals = [(n, v) for n, v in vals if v]
    if not vals:
        return ""
    if len({v for _, v in vals}) == 1:
        return vals[0][1]
    return " / ".join(f"{n} {v}" for n, v in vals)


def _fmt_bond_value(col, v):
    if not v:
        return ""
    if col in BOND_DATE_COLS:
        return _slash(v) or v
    if col == "발행금액":
        n = to_num(v)
        return f"{n:,}" if isinstance(n, int) else (f"{n:,.2f}" if n is not None else v)
    return v


def map_bond(it):
    """원본 응답 한 건을 화면 열 이름 -> 값 으로 바꾼다."""
    ov = _col_override()
    row = {}
    for col, cands, _ in BOND_COLS:
        v = pick(it, ov.get(col, cands)) if (col in ov or cands) else ""
        if not v and col == "신용등급" and col not in ov:
            v = _ratings(it)
        row[col] = _fmt_bond_value(col, v)
    return row


def _bond_db():
    HOME.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(HOME / "bonds.sqlite"), timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS bonds(isin TEXT PRIMARY KEY, nkey TEXT, raw TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    return c


def bond_db_info():
    """(받은 날짜, 건수) 또는 None"""
    try:
        c = _bond_db()
        meta = dict(c.execute("SELECT k, v FROM meta").fetchall())
        n = c.execute("SELECT COUNT(*) FROM bonds").fetchone()[0]
        c.close()
    except sqlite3.Error:
        return None
    return (meta.get("date"), n) if n and meta.get("date") else None


BOND_REFRESH_LOCK = threading.Lock()


def bond_db_fresh():
    i = bond_db_info()
    return bool(i and i[0] == date.today().isoformat())


def ensure_bond_db(datago_key, log=print, progress=None, force=False):
    """오늘 목록이 없으면 받는다. 이미 다른 곳에서 받는 중이면 끝날 때까지 기다렸다가 그 결과를 쓴다."""
    if not force and bond_db_fresh():
        return False
    with BOND_REFRESH_LOCK:
        if not force and bond_db_fresh():
            return False
        log("채권 목록을 내려받는 중입니다. (하루 첫 검색은 시간이 걸립니다)")
        refresh_bond_db(datago_key, log, progress)
        return True


def stored_datago_key():
    """저장된 공공데이터포털 키만 읽는다 (묻지 않음)."""
    key = os.environ.get("DATAGO_API_KEY", "").strip()
    f = HOME / "datago_key.txt"
    if not key and f.exists():
        try:
            key = f.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    return urllib.parse.unquote(key)


def refresh_bond_db(datago_key, log=print, progress=None, workers=4):
    """공공데이터포털의 채권기본정보를 전부 받아 PC에 저장한다. (하루에 한 번이면 충분)"""
    first, total = datago_page(datago_key, {}, rows=BOND_PAGE, page=1)
    if not total:
        raise RuntimeError("채권 목록이 비어 있습니다. 인증키와 활용신청 승인 상태를 확인해 주세요.")
    pages = min(max(1, math.ceil(total / BOND_PAGE)), 400)
    got = {1: first}
    if progress:
        progress(1, pages)

    def one(pg):
        last = None
        for _ in range(3):
            try:
                return pg, datago_page(datago_key, {}, rows=BOND_PAGE, page=pg)[0]
            except Exception as e:  # 일시 오류는 다시 시도
                last = e
                time.sleep(1)
        raise RuntimeError(f"{pg}쪽을 받지 못했습니다: {last}")

    if pages > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for pg, items in ex.map(one, range(2, pages + 1)):
                got[pg] = items
                if progress:
                    progress(len(got), pages)
    c = _bond_db()
    c.execute("DELETE FROM bonds")
    n = 0
    for pg in sorted(got):
        batch = []
        for it in got[pg]:
            isin = pick(it, ["isinCd"]).upper()
            if not isin:
                continue
            name = pick(it, ["isinCdNm", "bondNm", "itmsNm"]) + " " + pick(it, ["bondIsurNm"])
            slim = {k: v for k, v in it.items() if v not in ("", None)}
            batch.append((isin, re.sub(r"\s+", "", name).lower(),
                          json.dumps(slim, ensure_ascii=False, separators=(",", ":"))))
        c.executemany("INSERT OR REPLACE INTO bonds VALUES (?,?,?)", batch)
        n += len(batch)
    c.execute("INSERT OR REPLACE INTO meta VALUES ('date', ?)", (date.today().isoformat(),))
    c.commit()
    c.close()
    return n


def split_terms_raw(query):
    """쉼표/줄바꿈/세미콜론으로 나누고, 안쪽 공백은 한 칸으로 줄여 남긴다."""
    out = []
    for t in re.split(r"[,\n;]+", query or ""):
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def split_terms(query):
    """DB 검색용: 공백을 모두 뺀 검색어"""
    out = []
    for t in split_terms_raw(query):
        t = t.replace(" ", "")
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


CODE_PARTIAL_RE = re.compile(r"^KR[0-9A-Z]{1,10}$")  # 12자리가 안 되는 코드 일부 (예: KR1035)
_MODE_LOCK = threading.Lock()


def _mode_sample(text):
    """이름 가운데의 세 글자: 맨 앞이 아닌 부분을 골라, '앞부분 일치'와 '포함 일치'를 구분한다."""
    m = re.search(r"[가-힣A-Za-z]{3}", (text or "")[1:])
    return m.group(0) if m else ""


def _probe_mode(key, param, field, text):
    sub = _mode_sample(text)
    if not sub:
        return "none"
    try:
        items, _ = datago_page(key, {param: sub}, 100, 1)
    except Exception:
        return "none"
    vals = [str(i.get(field, "")) for i in items]
    if vals and all(sub in v for v in vals) and any(not v.startswith(sub) for v in vals):
        return "contains"
    return "none"


def search_modes(key, log=print):
    """서버가 이름의 일부로 검색해 주는지(contains) 한 번 확인해 저장한다. (호출 3번, 이후에는 저장값 사용)"""
    f = HOME / "search_mode.json"
    with _MODE_LOCK:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            if m.get("name") and m.get("issuer"):
                return m
        except (OSError, ValueError):
            pass
        first, _ = datago_page(key, {}, 200, 1)
        nm = next((pick(i, ["isinCdNm"]) for i in first if _mode_sample(pick(i, ["isinCdNm"]))), "")
        iss = next((pick(i, ["bondIsurNm"]) for i in first if _mode_sample(pick(i, ["bondIsurNm"]))), "")
        m = {"name": _probe_mode(key, "isinCdNm", "isinCdNm", nm),
             "issuer": _probe_mode(key, "bondIsurNm", "bondIsurNm", iss)}
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(m), encoding="utf-8")
        except OSError:
            pass
        return m


def _server_search(key, param, term, limit):
    out, page = [], 1
    while True:
        items, total = datago_page(key, {param: term}, BOND_PAGE, page)
        out += items
        if not items or len(out) >= min(total, limit + 1):
            return out
        page += 1


def _db_search(terms, limit):
    conds, args = [], []
    for t in terms:
        like = "%" + t.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append("(nkey LIKE ? ESCAPE '\\' OR LOWER(isin) LIKE ? ESCAPE '\\')")
        args += [like, like]
    c = _bond_db()
    rows = c.execute(f"SELECT raw FROM bonds WHERE {' OR '.join(conds)} ORDER BY isin LIMIT ?",
                     args + [limit + 1]).fetchall()
    c.close()
    return [json.loads(r[0]) for r in rows]


def search_bonds(datago_key, query, log=print, progress=None, refresh=False, limit=5000):
    """종목명, 발행기관 또는 표준코드의 일부로 채권을 찾는다. 쉼표/줄바꿈으로 여러 개를 넣으면 OR 로 찾는다.
    - 이름: 서버에서 바로 검색 (전체 목록을 받지 않는다)
    - 12자리 코드: 바로 조회 / 코드 일부(KR1035 등): 전체 목록(하루 한 번 저장)에서 검색
    반환: (원본 응답 목록, 잘렸는지)"""
    terms = split_terms_raw(query)
    if refresh:
        ensure_bond_db(datago_key, log, progress, force=True)
    if not terms:
        return [], False
    found, db_terms, modes = {}, [], None

    def add(items):
        for it in items:
            isin = pick(it, ["isinCd"]).upper()
            if isin and isin not in found:
                found[isin] = it

    for t in terms:
        up = t.upper().replace(" ", "")
        if ISIN_RE.match(up):
            add(isin_items(datago_key, up))
        elif CODE_PARTIAL_RE.match(up) and any(ch.isdigit() for ch in up):
            db_terms.append(up)
        else:
            if modes is None:
                modes = search_modes(datago_key, log)
            hits = []
            if modes.get("name") == "contains" or modes.get("issuer") == "contains":
                for variant in dict.fromkeys([t, t.replace(" ", "")]):
                    if modes.get("name") == "contains":
                        hits += _server_search(datago_key, "isinCdNm", variant, limit)
                    if modes.get("issuer") == "contains":
                        hits += _server_search(datago_key, "bondIsurNm", variant, limit)
                    if hits:
                        break
                add(hits)
            else:
                db_terms.append(t.replace(" ", ""))  # 서버 검색이 안 되면 전체 목록에서 찾는다
    if db_terms:
        ensure_bond_db(datago_key, log, progress)
        add(_db_search(db_terms, limit))
    items = list(found.values())
    return items[:limit], len(items) > limit


def dump_raw_sample(items, n=3):
    """응답 원본 필드를 파일로 남긴다. (필드 이름을 맞출 때 쓴다)"""
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        f = HOME / "raw_sample.json"
        f.write_text(json.dumps(items[:n], ensure_ascii=False, indent=1), encoding="utf-8")
        return str(f)
    except OSError:
        return ""


# ---------------------------------------------------------------- 조회
def lookup(key, corps, name, series, issue_date=None):
    corp_name, series, f, data, seen = search_bond(key, corps, name, series, issue_date, DartCache())
    if f is not None:
        show(corp_name, series, f, data)
        return True
    print(f"\n{name} {series} 를 찾지 못했습니다.")
    if seen:
        print("검색한 공시에 있던 회차:", ", ".join(sorted(seen)))
    if issue_date:
        print("발행일이 정확한지 확인해 주세요. (실적보고서는 발행 후 며칠 내 접수됩니다)")
    else:
        print("발행일을 함께 입력하시면 더 정확하고 빠릅니다.")
    return False


def main(argv):
    key = load_key()
    corps = load_corps(key)
    if argv and ISIN_RE.match(argv[0].upper()):
        lookup_isin(key, corps, load_datago_key(), argv[0].upper())
        return
    if len(argv) >= 2:
        date_arg = norm_date(argv[2]) if len(argv) >= 3 else None
        lookup(key, corps, argv[0], argv[1], date_arg)
        return
    print("종목코드  또는  회사명 회차 [발행일]")
    print("  예) KR6063621G20   /   비씨카드 189-1 20260420     (끝내려면 엔터를 누르세요)")
    while True:
        line = input("> ").strip()
        if not line:
            break
        parts = line.split()
        try:
            if ISIN_RE.match(parts[0].upper()):
                lookup_isin(key, corps, load_datago_key(), parts[0].upper())
            elif len(parts) < 2:
                print("회사명과 회차를 함께 입력해 주세요.")
            else:
                lookup(key, corps, parts[0], parts[1], norm_date(parts[2]) if len(parts) >= 3 else None)
        except RuntimeError as e:
            print(e)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        pass
    except urllib.error.URLError as e:
        sys.exit(f"네트워크 오류: {e}")
    except RuntimeError as e:
        sys.exit(str(e))
