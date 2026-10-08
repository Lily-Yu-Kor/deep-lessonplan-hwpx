#!/usr/bin/env python3
"""2022 개정 교육과정(교육부 고시 제2022-33호) 교과 PDF → data/curriculum/<교과>.json

추출 대상(공통 교육과정 부분만):
  - 영역별 핵심 아이디어
  - 학년(군)별 내용 요소(지식·이해 / 과정·기능 / 가치·태도)  ← 내용 체계 표(pdfplumber 표 인식)
  - 성취기준 문장, 성취기준 해설, 성취기준 적용 시 고려 사항   ← pdftotext -layout 본문
모든 항목에 source(file, pdf_page)를 남기고, 줄바꿈 복원·표 인식이 불확실하면 needs_review=true.

사용: python3 scripts/extract_curriculum.py --pdf-dir /path/to/pdf [--only 영어] [--out data/curriculum]
원문 PDF는 저장소에 포함하지 않는다(교육부·NCIC에서 내려받기).
"""
import argparse, collections, json, os, re, subprocess, sys, datetime

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("pdfplumber 필요: pip install pdfplumber (Debian/Ubuntu: --break-system-packages 또는 venv)")

# (교과 키, 파일, 공통 교육과정 과목 PDF 페이지 범위[시작, '3. 교수·학습 및 평가' 페이지], 별책)
COURSES = [
    ("국어", "[별책5] 국어과 교육과정.pdf", (11, 65), "별책 5"),
    ("도덕", "[별책6] 도덕과 교육과정..pdf", (11, 25), "별책 6"),
    ("사회", "[별책7] 사회과 교육과정.pdf", (12, 74), "별책 7"),
    ("역사", "[별책7] 사회과 교육과정.pdf", (79, 92), "별책 7"),
    ("수학", "[별책8] 수학과 교육과정.pdf", (12, 49), "별책 8"),
    ("과학", "[별책9] 과학과 교육과정.pdf", (11, 67), "별책 9"),
    ("실과(기술·가정)", "[별책10] 실과(기술가정)정보과 교육과정...pdf", (11, 37), "별책 10"),
    ("정보", "[별책10] 실과(기술가정)정보과 교육과정...pdf", (151, 162), "별책 10"),
    ("체육", "[별책11] 체육과 교육과정.pdf", (11, 44), "별책 11"),
    ("음악", "[별책12] 음악과 교육과정.pdf", (12, 29), "별책 12"),
    ("미술", "[별책13] 미술과 교육과정.pdf", (11, 25), "별책 13"),
    ("영어", "[별책14] 영어과 교육과정.pdf", (11, 30), "별책 14"),
    ("바른 생활", "[별책15] 바른 생활, 슬기로운 생활, 즐거운 생활 교육과정.pdf", (11, 17), "별책 15"),
    ("슬기로운 생활", "[별책15] 바른 생활, 슬기로운 생활, 즐거운 생활 교육과정.pdf", (27, 33), "별책 15"),
    ("즐거운 생활", "[별책15] 바른 생활, 슬기로운 생활, 즐거운 생활 교육과정.pdf", (44, 50), "별책 15"),
]
OUTNAME = {"실과(기술·가정)": "실과_기술가정", "바른 생활": "바른생활", "슬기로운 생활": "슬기로운생활",
           "즐거운 생활": "즐거운생활"}

DOT = "\u22c5"                       # HWP 가운뎃점(⋅)
BULLETS = "•\uf09fŸ∙●◦"
CODE = r"\d{1,2}[가-힣]{1,4}(?:\([가-힣]{1,4}\))?\d{2}\s*[-–−‐]\s*\d{2}"
CODE_RE = re.compile(r"\[(" + CODE + r")\]")
CATS = {"지식·이해": "지식·이해", "과정·기능": "과정·기능", "가치·태도": "가치·태도"}
PRIV = re.compile(r"[\ue000-\uf8ff\ufffd]")
MATHGAP = re.compile(r"\(\s*\)|‘\s*’|,\s+,|\s{2,}|이차함수\s+에서|가능성을\s+0")


def norm_code(c):
    return re.sub(r"\s*[-–−‐]\s*", "-", c)


def norm_text(s):
    s = s.replace(DOT, "·").replace("ㆍ", "·").replace("∼", "~")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def norm_label(s):
    return re.sub(r"[\s\[\]]", "", (s or "").replace(DOT, "·").replace("ㆍ", "·"))


def band_of_code(code):
    n = re.match(r"(\d+)", code).group(1)
    return {"2": "초1~2", "4": "초3~4", "6": "초5~6", "9": "중1~3"}.get(n, n)


# ───────────────────────── 줄바꿈 복원 ─────────────────────────
class Joiner:
    """한글 PDF는 글자 단위 줄나눔이라 줄 끝 공백 여부를 알 수 없다.
    교과 PDF 전체에서 '줄 안쪽'에 나온 어절만 모아(줄 끝 조각 제외) 붙여 쓰기/띄어 쓰기를 판정한다."""
    STRIP = "()[]{}<>‘’“”'\",.·:;!?~-–"
    SUFFIX = {"하기", "하는", "하고", "하며", "하여", "한다", "하다", "하게", "함", "할", "해", "했다", "하면", "하도록",
              "하거나", "하기를", "하기와", "하였다", "되는", "되고", "된다", "되어", "됨", "되도록", "시키기", "시키는", "시킨다",
              "해야", "해서", "한", "하지"}

    def __init__(self, vocab):
        import bisect
        self._bis = bisect
        uni, bi = vocab
        self.exact = uni
        self.U = self._index(uni)
        self.B = self._index(bi)

    @staticmethod
    def _index(counter):
        keys = sorted(counter)
        cum, t = [], 0
        for k in keys:
            t += counter[k]
            cum.append(t)
        return keys, cum

    def _prefix(self, idx, p):
        keys, cum = idx
        i = self._bis.bisect_left(keys, p)
        j = self._bis.bisect_left(keys, p + "\U0010ffff")
        if j <= i:
            return 0
        return cum[j - 1] - (cum[i - 1] if i > 0 else 0)

    def prefix(self, p):
        return self._prefix(self.U, p)

    def join(self, a, b):
        """a(앞줄 끝), b(뒷줄 시작) → (구분자, 근거충분?)
        근거: 문서 안쪽에서 'A B'(띄어 쓴 2어절)와 'AB…'(붙여 쓴 어절)가 각각 몇 번 나오는지."""
        a, b = a.rstrip().replace(DOT, "·"), b.lstrip().replace(DOT, "·")
        if not a or not b:
            return " ", True
        la, fb = a[-1], b[0]
        hangul = lambda ch: "\uac00" <= ch <= "\ud7a3"
        if la in "(/[‘“" or fb in ")],.·/’”:;?!":
            return "", True
        if la == "·":
            return "", True
        if not (hangul(la) or la.isdigit()) or not (hangul(fb) or fb.isdigit()):
            if hangul(la) and fb == "(":
                return "", True
            return " ", True
        A = re.split(r"[)\]·,(‘“]", a.split()[-1].strip(self.STRIP))[-1]
        B = re.split(r"[(\[·,’”]", b.split()[0].strip(self.STRIP))[0]
        if not A or not B:
            return " ", True
        if len(B) == 1 and B in "을를은는의에도와과로" and len(A) >= 2:
            return "", True
        joined = self.prefix(A + B)
        spaced = self._prefix(self.B, A + " " + B)
        if joined or spaced:
            if joined > spaced:
                return "", joined >= 2 * spaced + 1
            if spaced > joined:
                return " ", spaced >= 2 * joined + 1
            return " ", False
        if re.fullmatch(r"(하|되|시키|해|했|할|함|한)[가-힣]{0,3}", B) and B in self.SUFFIX \
                and A[-1] not in "을를이가은는에의로와과게고서며여도면니":
            return "", True
        a_word = self.exact.get(A, 0) or self.prefix(A)
        if len(B) == 1:
            b_start = self.exact.get(B, 0)
        else:
            b_start = max(self.prefix(B[:k]) for k in range(max(2, len(B) - 2), len(B) + 1))
        if a_word and b_start:
            return " ", True
        if not a_word and not b_start:
            return "", True
        if b_start and len(A) >= 2 and A[-1] in "의을를은는에와과로가이도며고서게적한된할인":
            return " ", False   # 앞말이 조사·어미로 끝나고 뒷말이 단어 시작: 띄어 씀 쪽이 우세
        return "", (len(A) == 1 or len(B) == 1)

    def join_lines(self, lines):
        out, ok = "", True
        for ln in lines:
            if ln.startswith("\n"):
                out = out + ln
                continue
            ln = ln.strip()
            if not ln:
                continue
            if not out:
                out = ln
                continue
            sep, good = self.join(out, ln)
            ok = ok and good
            out = out + sep + ln
        return out, ok


# ───────────────────────── 내용 체계 표 ─────────────────────────
def dotted_edges(page):
    """점선(길이 0 선분 묶음)을 실선 edge로 복원한다."""
    dots = [l for l in page.lines if (l["x1"] - l["x0"]) < 1.2 and (l["bottom"] - l["top"]) < 1.2]
    edges = []
    byy = collections.defaultdict(list)
    byx = collections.defaultdict(list)
    for d in dots:
        byy[round(d["top"])].append(d["x0"])
        byx[round(d["x0"])].append(d["top"])
    def runs(vals, gap=4.5):
        vals = sorted(vals)
        s = p = vals[0]
        for v in vals[1:]:
            if v - p > gap:
                yield s, p
                s = v
            p = v
        yield s, p
    for y, xs in byy.items():
        if len(xs) < 6:
            continue
        for a, b in runs(xs):
            if b - a > 15:
                edges.append(("h", a, b, y))
    for x, ys in byx.items():
        if len(ys) < 6:
            continue
        for a, b in runs(ys):
            if b - a > 8:
                edges.append(("v", x, a, b))
    H = [{"x0": a, "x1": b, "top": y, "bottom": y, "width": b - a, "height": 0, "object_type": "line"}
         for k, a, b, y in edges if k == "h"]
    V = [{"x0": x, "x1": x, "top": a, "bottom": b, "width": 0, "height": b - a, "object_type": "line"}
         for k, x, a, b in edges if k == "v"]
    return H, V


def outer_x(page):
    hl = [l for l in page.lines if abs(l["top"] - l["bottom"]) < 1.5 and 100 < (l["x1"] - l["x0"]) < page.width * 0.93
          and l["x0"] > 5 and l["x1"] < page.width - 5]
    hl += [r for r in page.rects if r["height"] < 1.5 and 100 < r["width"] < page.width * 0.93]
    if not hl:
        return []
    a = collections.Counter(round(l["x0"]) for l in hl).most_common(1)[0][0]
    b = collections.Counter(round(l["x1"]) for l in hl).most_common(1)[0][0]
    return [a, b]


def clip(page, bbox):
    x0, t, x1, b = bbox
    return (max(x0, 0), max(t, 0), min(x1, page.width), min(b, page.height))


def cell_items(page, bbox, joiner):
    """셀 안 텍스트 → [(항목, 근거충분?)], 다단여부.
    한 셀 안에 항목이 여러 단으로 놓인 경우(⋅ 시작 x가 다른 묶음) 줄마다 글머리표·큰 간격에서 끊어 단별로 모은다."""
    x0, top, x1, bottom = bbox
    crop = page.crop((max(x0 - 6, 0), max(top, 0), min(x1, page.width), min(bottom, page.height)))
    allchars = [c for c in crop.chars if (c["x0"] >= x0 - 0.5 or (c["text"] == DOT and c["x1"] <= x0 + 3))
                and c["x0"] < x1 - 1.5]
    chars = [c for c in allchars if c["text"].strip()]
    if not chars:
        return [], False
    spaces = [c for c in allchars if not c["text"].strip()]
    lines = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        for ln in lines:
            if abs(ln[0] - c["top"]) < 2.5:
                ln[1].append(c)
                break
        else:
            lines.append([c["top"], [c]])
    lines.sort(key=lambda l: l[0])
    parsed = []          # 줄마다 [(x, text, is_item_start)]
    starts = []
    for _, cs in lines:
        cs.sort(key=lambda c: c["x0"])
        gaps = [b["x0"] - a["x1"] for a, b in zip(cs, cs[1:])]
        med = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
        segs, cur = [], None
        for i, c in enumerate(cs):
            gap = gaps[i - 1] if i else None
            rel = (gap - med) if gap is not None else 99
            sp = gap is not None and (rel > 1.2 or any(abs(s["top"] - c["top"]) < 2.5 and cs[i - 1]["x1"] - 0.6 <= s["x0"] <= c["x0"] + 0.3 for s in spaces))
            item_start = c["text"] == DOT and (i == 0 or rel > 1.2)
            big = gap is not None and rel > 6
            if cur is None or item_start or big:
                if item_start:
                    starts.append(c["x0"])
                cur = [c["x0"], "", item_start]
                segs.append(cur)
                if item_start:
                    continue
                cur[1] += c["text"]
                continue
            cur[1] += (" " if sp else "") + c["text"]
        parsed.append(segs)
    cols = []
    for x in sorted(starts):
        if not cols or x - cols[-1] > 12:
            cols.append(x)
    multi = len(cols) > 1

    def col_of(x):
        if not multi:
            return 0
        k = 0
        for i, cx in enumerate(cols):
            if x >= cx - 3:
                k = i
        return k
    percol = collections.defaultdict(list)    # col → [(text, is_start)]
    for segs in parsed:
        for x, t, st in segs:
            percol[col_of(x)].append((t, st))
    items = []
    for ci in sorted(percol):
        groups, cur = [], None
        cont_first = False
        for t, st in percol[ci]:
            if st or cur is None:
                if not st and ci == 0 and not groups:
                    cont_first = True
                cur = [t]
                groups.append(cur)
            else:
                cur.append(t)
        for gi, g in enumerate(groups):
            txt, ok = joiner.join_lines(g)
            txt = norm_text(txt)
            if txt and txt not in ("-", "–"):
                if gi == 0 and cont_first:
                    txt = "\x00" + txt
                items.append((txt, ok))
    return items, multi


def cat_of(t):
    k = re.sub(r"[·\s\[\]⋅ㆍ]", "", t or "")
    return {"지식이해": "지식·이해", "과정기능": "과정·기능", "가치태도": "가치·태도"}.get(k)


def make_item(txt, ok, multi, sub, cat, covered, fname, pno, review=None):
    it = {"text": txt}
    if sub and cat in ("지식·이해", "과정·기능"):
        it["sub"] = sub
    if len(covered) > 1:
        it["span"] = covered
    it["source"] = {"file": fname, "pdf_page": pno}
    rr = review or (None if ok else ("셀 안 다단 배치" if multi else "줄바꿈 복원 근거 부족"))
    if PRIV.search(txt):
        rr = "판독 불가 문자 포함"
    elif MATHGAP.search(txt):
        rr = "수식·기호 누락 가능"
    if rr:
        it["needs_review"] = True
        it["review_reason"] = rr
    return it


def add_items(cur, band, cat, its, multi, sub, covered, fname, pno, joiner, review=None):
    slot = cur["content_elements"].setdefault(band, collections.OrderedDict(
        [("지식·이해", []), ("과정·기능", []), ("가치·태도", [])]))
    lst = slot.setdefault(cat or "?", [])
    for txt, ok in its:
        if txt.startswith("\x00"):
            txt = txt[1:]
            if lst:
                sep, good = joiner.join(lst[-1]["text"], txt)
                lst[-1]["text"] = norm_text(lst[-1]["text"] + sep + txt)
                lst[-1]["source"]["pdf_page_end"] = pno
                if not good:
                    lst[-1]["needs_review"] = True
                    lst[-1]["review_reason"] = "쪽 넘김 항목 이어 붙임"
                continue
        lst.append(make_item(txt, ok, multi, sub, cat, covered, fname, pno, review))


def parse_content_system(pdf, p_from, p_to, joiner, fname, course_key, default_band):
    domains = []
    cur = None
    grades = []          # [(x0,x1,band)]
    content_col = None
    last_school_row = []
    cat = sub = None
    pending_heading = None
    integrated = None    # 통합교과 표: 이 과목 열의 (x0,x1)
    ideas_x = None
    for pno in range(p_from, p_to + 1):
        page = pdf.pages[pno - 1]
        # '나. 성취기준'이 시작되는 쪽은 그 위까지만 표로 읽는다(마지막 행이 본문을 삼키지 않도록)
        for ln in page.extract_text_lines():
            if re.match(r"^\s*나\.\s*성취\s*기준", ln["text"]):
                page = page.crop((0, 0, page.width, ln["top"] - 1))
                break
        H, V = dotted_edges(page)
        ts = {"vertical_strategy": "lines", "horizontal_strategy": "lines",
              "explicit_vertical_lines": outer_x(page) + V, "explicit_horizontal_lines": H,
              "snap_tolerance": 3, "join_tolerance": 3, "intersection_tolerance": 4}
        tables = page.find_tables(ts)
        words_lines = []
        for ln in page.extract_text_lines():
            inside = any(t.bbox[1] - 1 <= ln["top"] <= t.bbox[3] + 1 and t.bbox[0] - 1 <= ln["x0"] <= t.bbox[2] for t in tables)
            if not inside:
                words_lines.append((ln["top"], ln["text"]))
        events = [(y, "text", t) for y, t in words_lines]
        for t in tables:
            for r in t.rows:
                events.append((r.bbox[1], "row", r))
        events.sort(key=lambda e: e[0])
        for y, kind, obj in events:
            if kind == "text":
                m = re.search(r"\((\d+)\)\s*([^()]{1,30}(?:\([^)]*\))?)\s*$", obj.strip())
                if m and "핵심" not in obj:
                    pending_heading = (int(m.group(1)), norm_text(m.group(2)), pno)
                continue
            cells = [(c, page.crop(clip(page, c)).extract_text() or "") for c in obj.cells
                     if c is not None and clip(page, c)[3] - clip(page, c)[1] > 1]
            if not cells:
                continue
            cells.sort(key=lambda ct: ct[0][0])
            labs = [norm_label(t).replace("·", "") for _, t in cells]
            mh = re.search(r"\((\d+)\)\s*([^\n()]{1,30}(?:\([^)\n]*\))?)\s*$", cells[0][1].strip())
            if mh and len(cells) == 1:
                pending_heading = (int(mh.group(1)), norm_text(mh.group(2)), pno)
                continue
            # ── 통합교과(바른/슬기로운/즐거운 생활) 공통 표 머리글
            if "영역" in labs[:1] and any("핵심아이디어" in l for l in labs[:3]):
                integrated = "pending"
                ideas_x = next(c for c, t in cells if "핵심아이디어" in norm_label(t).replace("·", ""))
                continue
            if integrated == "pending":
                for c, t in cells:
                    if norm_label(t) == norm_label(course_key):
                        integrated = (c[0], c[2])
                continue
            if isinstance(integrated, tuple):
                c0, t0 = cells[0]
                if c0[2] <= ideas_x[0] + 2 and norm_label(t0):   # 영역 칸이 있는 행 → 새 영역
                    no = len(domains) + 1
                    cur = {"no": no, "name": norm_text(t0.replace("\n", " ")), "name_full": None,
                           "source": {"file": fname, "pdf_page": pno}, "core_ideas": [],
                           "content_elements": collections.OrderedDict()}
                    domains.append(cur)
                    for c, t in cells:
                        if abs(c[0] - ideas_x[0]) < 3:
                            its, _ = cell_items(page, c, joiner)
                            for txt, ok in its:
                                cur["core_ideas"].append(make_item(txt.lstrip("\x00"), ok, False, None, None, [], fname, pno))
                if cur is None:
                    continue
                for c, t in cells:
                    if cat_of(t):
                        cat = cat_of(t)
                for c, t in cells:
                    if min(c[2], integrated[1]) - max(c[0], integrated[0]) > 0.5 * (integrated[1] - integrated[0]):
                        its, multi = cell_items(page, c, joiner)
                        add_items(cur, default_band, cat, its, multi, None, [default_band], fname, pno, joiner)
                continue
            # ── 일반 교과 표
            idx = next((i for i, l in enumerate(labs[:3]) if l.startswith("핵심아이디어")), None)
            if idx is not None:
                if pending_heading is None:
                    pending_heading = (len(domains) + 1, f"[영역 {len(domains)+1}]", pno)
                no, name, hp = pending_heading
                pending_heading = None
                cur = {"no": no, "name": re.sub(r"\s*\(.*\)$", "", name), "name_full": name,
                       "source": {"file": fname, "pdf_page": hp},
                       "core_ideas": [], "content_elements": collections.OrderedDict()}
                domains.append(cur)
                cat = sub = None
                grades, content_col, last_school_row = [], None, []
                for c, t in cells[idx + 1:]:
                    its, _ = cell_items(page, c, joiner)
                    for txt, ok in its:
                        cur["core_ideas"].append(make_item(txt.lstrip("\x00"), ok, False, None, None, [], fname, pno))
                continue
            if cur is None:
                continue
            nodot = not any(DOT in t for _, t in cells)
            if nodot and any(l in ("범주", "구분범주", "범주구분") for l in labs[:3]):
                for c, t in cells:
                    if "내용요소" in norm_label(t) or "학년군" in norm_label(t):
                        content_col = (c[0], c[2])
                    if re.search(r"초등학교|중학교", t):
                        last_school_row = [(cc[0], cc[2], tt) for cc, tt in cells]
                continue
            GR = r"(\d)\s*[~∼\-–]\s*(\d)\s*학년|(\d)\s*학년"
            if nodot and any(re.search(GR, t) for _, t in cells):
                grades = []
                for c, t in cells:
                    m = re.search(GR, t)
                    cx = (c[0] + c[2]) / 2
                    if not m:
                        if norm_label(t) == "중학교":
                            grades.append((c[0], c[2], "중1~3"))
                        continue
                    lab = f"{m.group(1)}~{m.group(2)}" if m.group(1) else m.group(3)
                    school = None
                    for sx0, sx1, st in last_school_row:
                        if sx0 - 1 <= cx <= sx1 + 1:
                            school = "초" if "초등" in st else "중" if "중학" in st else None
                    if school is None:
                        school = "중" if ("중" in t or lab == "1~3") else "초"
                    grades.append((c[0], c[2], school + lab))
                continue
            if nodot and any(re.search(r"초등학교|중학교", t) for _, t in cells):
                last_school_row = [(c[0], c[2], t) for c, t in cells]
                grades = [(c[0], c[2], "중1~3" if "중학" in t else default_band) for c, t in cells
                          if re.search(r"초등학교|중학교", t)]
                continue
            if not grades:
                if content_col:
                    grades = [(content_col[0], content_col[1], default_band)]
                else:
                    continue
            gx0 = min(g[0] for g in grades)
            labels = [(c, t) for c, t in cells if c[2] <= gx0 + 2]
            datas = [(c, t) for c, t in cells if c[2] > gx0 + 2]
            for c, t in labels:
                if cat_of(t):
                    cat, sub = cat_of(t), None
                elif norm_label(t).strip("·"):
                    sub = norm_text(t.replace("\n", " "))
            for c, t in datas:
                covered = [g[2] for g in grades if min(c[2], g[1]) - max(c[0], g[0]) > 0.5 * min(g[1] - g[0], c[2] - c[0])]
                its, multi = cell_items(page, c, joiner)
                if not its:
                    continue
                review = None
                if not covered:
                    review, covered = "학년군 열을 판정하지 못함", ["?"]
                if cat is None:
                    review = "범주(지식·이해/과정·기능/가치·태도) 판정 실패"
                for band in covered:
                    add_items(cur, band, cat, its, multi, sub, covered, fname, pno, joiner, review)
    for d in domains:
        d["content_elements"] = {b: dict(v) for b, v in d["content_elements"].items()}
    return domains


# ───────────────────────── 성취기준 본문 ─────────────────────────
def layout_pages(path, a, b):
    out = subprocess.run(["pdftotext", "-layout", "-f", str(a), "-l", str(b), path, "-"],
                         capture_output=True, text=True, check=True).stdout
    return out.split("\f")[: b - a + 1]


def header_lines(path, npages):
    """여러 쪽의 첫/끝 줄에 반복되는 머리말·꼬리말."""
    out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    cnt = collections.Counter()
    for pg in out.split("\f"):
        ls = [l.strip() for l in pg.splitlines() if l.strip()]
        for l in ls[:2] + ls[-2:]:
            if len(l) < 45:
                cnt[re.sub(r"\s+", " ", l)] += 1
    struct = re.compile(r"^([가-하]\.\s|\d+\.\s|\(\d+\)|\(가\)|\(나\)|\[|<|[•\uf09fŸ⋅])")
    return {k for k, v in cnt.items() if not struct.match(k) and not k.endswith(".")
            and ((v >= 3 and "교육과정" in k) or v >= 8)}, out


def parse_standards(path, fname, p_from, p_to, joiner, headers):
    pages = layout_pages(path, p_from, p_to)
    lines = []
    for i, pg in enumerate(pages):
        for l in pg.splitlines():
            s = l.rstrip()
            st = re.sub(r"\s+", " ", s.strip())
            if not st or st in headers or re.fullmatch(r"\d{1,3}", st):
                lines.append((p_from + i, 0, ""))
                continue
            lines.append((p_from + i, len(s) - len(s.lstrip()), s.strip()))
    started = False
    blocks, block = [], None
    heading = None
    mode = None
    item = None      # 현재 이어 붙이는 항목: dict(kind, parts, page, indent, target)
    subtopic = None
    std_indent = 0
    section = None

    def flush():
        nonlocal item
        if not item:
            return
        txt, ok = joiner.join_lines(item["parts"])
        mathgap = bool(MATHGAP.search(txt))
        rawtxt = txt
        txt = norm_text(txt)
        rec = {"text": txt}
        src = {"file": fname, "pdf_page": item["page"]}
        if item["last_page"] != item["page"]:
            src["pdf_page_end"] = item["last_page"]
        if item["kind"] == "std":
            rec = {"code": item["code"], "text": txt, "grade_band": band_of_code(item["code"])}
            if item.get("subtopic"):
                rec["subtopic"] = item["subtopic"]
            rec["source"] = src
            reasons = []
            if not ok:
                reasons.append("줄바꿈 복원 근거 부족")
            if not re.search(r"(다|한다|있다)\.$", txt):
                reasons.append("문장 끝 확인 필요")
            if mathgap:
                reasons.append("표·다단 배치 섞임(원문 확인)" if re.search(r"\s{3,}", rawtxt) else "수식·기호 누락 가능")
            if reasons:
                rec["needs_review"] = True; rec["review_reason"] = ", ".join(reasons)
            block["standards"].append(rec)
        else:
            codes = []
            m = re.match(r"^((?:\s*\[" + CODE + r"\]\s*(?:[,·와과및]|[~∼])?\s*)+)", txt)
            if m:
                lead = m.group(1)
                cs = [norm_code(c) for c in re.findall(CODE, lead)]
                if re.search(r"\]\s*[~∼]\s*\[", lead) and len(cs) == 2:
                    a, b = cs
                    pa, pb = a.rsplit("-", 1), b.rsplit("-", 1)
                    if pa[0] == pb[0]:
                        cs = [f"{pa[0]}-{n:02d}" for n in range(int(pa[1]), int(pb[1]) + 1)]
                codes = cs
            rec["source"] = src
            if codes:
                rec["codes"] = codes
            if not ok:
                rec["needs_review"] = True; rec["review_reason"] = "줄바꿈 복원 근거 부족"
            if PRIV.search(txt):
                rec["needs_review"] = True; rec["review_reason"] = "판독 불가 문자 포함"
            elif mathgap:
                rec["needs_review"] = True
                rec["review_reason"] = "표·다단 배치 섞임(원문 확인)" if re.search(r"\s{3,}", rawtxt) else "수식·기호 누락 가능"
            item["target"].append(rec)
        item = None

    for pno, ind, t in lines:
        if not started:
            if re.match(r"^나\.\s*성취\s*기준", t):
                started = True
            continue
        if re.match(r"^3\.\s*교수", t):
            break
        if not t:
            continue
        mg = re.fullmatch(r"\[((?:초등학교|중학교)[^\]]*)\]", t)
        if mg:
            flush(); heading = norm_text(mg.group(1)); block = None; mode = None; section = None
            continue
        md = re.fullmatch(r"\((\d+)\)\s*(.{1,40})", t)
        last_no = block["domain_no"] if block else 0
        if md and not re.search(r"(다|요)\.$", t) and int(md.group(1)) in (1, last_no + 1):
            flush()
            block = {"grade_heading": heading, "section": section, "domain_no": int(md.group(1)),
                     "domain": norm_text(md.group(2)),
                     "standards": [], "explanations": [], "considerations": [], "extras": {},
                     "source": {"file": fname, "pdf_page": pno}}
            if not section:
                block.pop("section")
            blocks.append(block); mode = "std"; subtopic = None
            continue
        msec = re.fullmatch(r"<\s*([^<>]{1,20}영역)\s*>", t)
        if msec:
            flush(); section = norm_text(msec.group(1)); block = None; mode = None
            continue
        if block is None:
            continue
        if re.match(r"^\(가\)\s*성취\s*기준\s*해설", t):
            flush(); mode = "exp"; continue
        if re.match(r"^\(나\)\s*성취\s*기준\s*적용\s*시\s*고려\s*사항", t):
            flush(); mode = "con"; continue
        mx = re.fullmatch(r"<\s*([^<>]{1,20})\s*>", t)
        if mx:
            flush(); mode = "extra:" + norm_text(mx.group(1)); block["extras"].setdefault(norm_text(mx.group(1)), [])
            continue
        ms = re.match(r"^\[(" + CODE + r")\]\s*(.*)$", t)
        if ms and mode == "std":
            flush()
            item = {"kind": "std", "code": norm_code(ms.group(1)), "parts": [ms.group(2)], "page": pno,
                    "last_page": pno, "indent": ind, "subtopic": subtopic}
            std_indent = ind
            continue
        if t[0] in BULLETS or (ms and mode != "std"):
            flush()
            body = t[1:].strip() if t[0] in BULLETS else t
            target = {"exp": block["explanations"], "con": block["considerations"]}.get(mode)
            if target is None and mode and mode.startswith("extra:"):
                target = block["extras"][mode[6:]]
            if target is None:
                target = block["considerations"]
            item = {"kind": mode, "parts": [body], "page": pno, "last_page": pno, "indent": ind, "target": target}
            continue
        if re.match(r"^[-–○◦]\s", t) and item and item["kind"] != "std":
            item["parts"].append("\n" + t)
            item["last_page"] = pno
            continue
        if mode == "std":
            prev_done = bool(item) and re.search(r"다\.\s*$", item["parts"][-1] or "")
            if item and (not prev_done or ind >= std_indent + 5):
                item["parts"].append(t); item["last_page"] = pno
            else:
                flush(); subtopic = norm_text(t)
            continue
        if item:
            item["parts"].append(t); item["last_page"] = pno
        else:
            target = {"exp": block["explanations"], "con": block["considerations"]}.get(mode, block["considerations"])
            item = {"kind": mode, "parts": [t], "page": pno, "last_page": pno, "indent": ind, "target": target}
    flush()
    # 해설을 성취기준에 연결
    bycode = {}
    for b in blocks:
        for s in b["standards"]:
            bycode[s["code"]] = s
    for b in blocks:
        for e in b["explanations"]:
            for c in e.get("codes", []):
                if c in bycode:
                    bycode[c].setdefault("explanation_refs", []).append(len(b["explanations"]) and b["explanations"].index(e))
    return blocks


def fix_parts_newlines(s):
    return s


GLOBAL_VOCAB = None


def squash(s):
    s = s.replace(DOT, "·").replace("ㆍ", "·").replace("∼", "~").replace("–", "-")
    return re.sub(r"[\s·•\uf09fŸ]", "", s)


_PT = {}


def raw_pages(path, a, b):
    """원문 쪽 텍스트(공백·가운뎃점 제거, 머리말·꼬리말 제외) — 글자 대조용"""
    k = (path, a, b)
    if k not in _PT:
        r = subprocess.run(["pdftotext", "-raw", "-f", str(a), "-l", str(b), path, "-"], capture_output=True, text=True)
        t = "\n".join(l for l in r.stdout.splitlines()
                      if not re.fullmatch(r"\s*\d{1,3}\s*", l) and not ("교육과정" in l and len(l.strip()) < 40)
                      and l.strip() not in ("바른 생활", "슬기로운 생활", "즐거운 생활"))
        _PT[k] = squash(t)
    return _PT[k]


def verify_against_source(path, data):
    """모든 항목의 글자(공백 제외)가 기록된 쪽 원문에 있는지 확인. 없으면 needs_review."""
    def chk(it, extra=0, table=False):
        p0 = it["source"]["pdf_page"]
        p1 = it["source"].get("pdf_page_end", p0)
        if squash(it["text"]) in raw_pages(path, p0, p1 + extra):
            return
        if table and "pdf_page_end" in it["source"]:
            return   # 쪽을 넘긴 표 칸은 원문 텍스트 순서가 섞여 대조 불가(이미지 대조로 확인)
        if not it.get("needs_review"):
            it["needs_review"] = True
            it["review_reason"] = "원문 글자 대조 불일치(표·기호 섞임 가능)"
    for dm in data["domains"]:
        for c in dm["core_ideas"]:
            chk(c)
        for cats in dm["content_elements"].values():
            for its in cats.values():
                for it in its:
                    chk(it, 1, True)
    for b in data["standard_blocks"]:
        for key in ("standards", "explanations", "considerations"):
            for it in b[key]:
                chk(it)
        for v in b.get("extras", {}).values():
            for it in v:
                chk(it)


def global_vocab(pdfdir):
    global GLOBAL_VOCAB
    if GLOBAL_VOCAB is None:
        uni, bi = collections.Counter(), collections.Counter()
        GLOBAL_VOCAB = (uni, bi)
        for f in sorted({c[1] for c in COURSES}):
            out = subprocess.run(["pdftotext", "-layout", os.path.join(pdfdir, f), "-"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                # 표 다단 사이 큰 공백으로 구간을 나누고, 각 구간의 첫/끝 어절(줄바꿈 조각일 수 있음)은 제외
                for seg in re.split(r"\s{3,}", line.strip()):
                    toks = [t.strip(Joiner.STRIP) for t in norm_text(seg.replace(DOT, " ")).split()]
                    uni.update(t for t in toks[1:-1] if t)
                    for i in range(1, len(toks) - 1):
                        if toks[i] and toks[i + 1]:
                            bi[toks[i] + " " + toks[i + 1]] += 1
    return GLOBAL_VOCAB


def build(course, pdfdir, outdir):
    key, fname, (a, b), book = course
    path = os.path.join(pdfdir, fname)
    headers, fulltext = header_lines(path, None)
    joiner = Joiner(global_vocab(pdfdir))
    pdf = pdfplumber.open(path)
    pages = fulltext.split("\f")
    cs_from = cs_to = None
    for p in range(a, b + 1):
        t = pages[p - 1]
        if cs_from is None and re.search(r"가\.\s*내용\s*체계", t):
            cs_from = p
        if cs_from is not None and re.search(r"(^|\n)\s*나\.\s*성취\s*기준", t):
            cs_to = p
            break
    default_band = {"역사": "중1~3", "정보": "중1~3", "바른 생활": "초1~2", "슬기로운 생활": "초1~2",
                    "즐거운 생활": "초1~2"}.get(key, "?")
    domains = parse_content_system(pdf, cs_from, cs_to, joiner, fname, key, default_band)
    blocks_tmp = None
    blocks = parse_standards(path, fname, cs_to, b, joiner, headers)
    # 성취기준별 해설 텍스트를 바로 붙여 둔다
    for bl in blocks:
        for s in bl["standards"]:
            s.pop("explanation_refs", None)
    for bl in blocks:
        for e in bl["explanations"]:
            for c in e.get("codes", []):
                for bl2 in blocks:
                    for s in bl2["standards"]:
                        if s["code"] == c:
                            s.setdefault("explanation", []).append({k: v for k, v in e.items() if k != "codes"})
    # 영역명 보정: 표 왼쪽 열의 줄바꿈(예: '살아 갈까')은 본문 제목 '(1) 우리는 누구로 살아갈까'를 우선한다
    sq = lambda t: re.sub(r"\s+", "", t or "")
    for dm in domains:
        for bl in blocks:
            if bl.get("domain_no") == dm["no"] and sq(bl.get("domain")) == sq(dm["name"]) and bl["domain"] != dm["name"]:
                dm["name"] = bl["domain"]
        if re.fullmatch(r"\[영역 \d+\]", dm["name"]) and len(domains) == 1:
            dm["name"] = key  # 역사처럼 영역 구분 없이 표 하나인 경우
            dm["name_note"] = "내용 체계 표에 영역 구분이 없어 과목명을 영역명으로 사용"
    # 성취기준 묶음 제목이 내용 체계 영역인지(국어·수학 등), 학년군별 단원인지(과학·사회·역사 등) 표시
    dnames = {sq(dm["name"]) for dm in domains}
    for bl in blocks:
        bl["heading_kind"] = "영역" if sq(bl.get("domain")) in dnames else "단원"
    data = {
        "subject": key,
        "curriculum": "2022 개정 교육과정 (교육부 고시 제2022-33호)",
        "book": f"[{book}] " + re.sub(r"^\[별책\d+\]\s*|\.+pdf$|\.pdf$", "", fname).strip(),
        "source_file": fname,
        "pdf_pages": {"course": [a, b], "content_system": [cs_from, cs_to], "standards": [cs_to, b]},
        "extracted_at": datetime.date.today().isoformat(),
        "extraction": "pdfplumber(내용 체계 표) + pdftotext -layout(성취기준·해설·고려 사항), 줄바꿈은 문서 내 어절 빈도로 복원",
        "notice": "교육부 공공저작물(교육과정 고시문)에서 기계 추출. needs_review=true 항목은 원문 대조 필요. 수업 설계 시 교사 검토 필수.",
        "domains": domains,
        "standard_blocks": blocks,
    }
    verify_against_source(path, data)
    if key == "영어":
        data["annex2_communicative_functions"] = parse_english_annex2(pdfdir, fname)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, OUTNAME.get(key, key) + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return out, data


def stats(data):
    n = collections.Counter()
    rv = collections.Counter()
    for d in data["domains"]:
        for ci in d["core_ideas"]:
            n["핵심아이디어"] += 1; rv["핵심아이디어"] += bool(ci.get("needs_review"))
        for band, cats in d["content_elements"].items():
            for cat, its in cats.items():
                for it in its:
                    n["내용요소"] += 1; rv["내용요소"] += bool(it.get("needs_review"))
    for b in data["standard_blocks"]:
        for s in b["standards"]:
            n["성취기준"] += 1; rv["성취기준"] += bool(s.get("needs_review"))
        for e in b["explanations"]:
            n["해설"] += 1; rv["해설"] += bool(e.get("needs_review"))
        for e in b["considerations"]:
            n["고려사항"] += 1; rv["고려사항"] += bool(e.get("needs_review"))
    return n, rv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=os.environ.get("CURRICULUM_PDF_DIR", "pdf"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "curriculum"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    for c in COURSES:
        if a.only and c[0] not in a.only:
            continue
        out, data = build(c, a.pdf_dir, a.out)
        n, rv = stats(data)
        print(f"{c[0]:10s} " + " ".join(f"{k}={n[k]}(검토{rv[k]})" for k in ["핵심아이디어", "내용요소", "성취기준", "해설", "고려사항"]) + f" → {os.path.relpath(out)}")



# ───────────────────────── 영어 [별표 2] 의사소통 기능 예시문 ─────────────────────────
def parse_english_annex2(pdfdir, fname="[별책14] 영어과 교육과정.pdf"):
    """[별표 2] '나. 의사소통 기능 예시문'을 기능 번호별 예시문 목록으로 만든다(2단 편집을 단별로 읽음)."""
    path = os.path.join(pdfdir, fname)
    pdf = pdfplumber.open(path)
    start = end = None
    for i, p in enumerate(pdf.pages):
        t = p.extract_text() or ""
        if start is None and "나. 의사소통 기능 예시문" in t:
            start = i
        if start is not None and re.search(r"\[별표\s*3\]", t):
            end = i
            break
    out, cur, section = [], None, None
    for i in range(start, end + 1):
        p = pdf.pages[i]
        W = p.width
        for a, b in ((0, W / 2), (W / 2, W)):
            t = p.crop((a, 40, b, p.height - 40)).extract_text() or ""
            for ln in t.splitlines():
                s = ln.strip()
                if not s or s.startswith("[별표") or s in ("영어과 교육과정",) or re.fullmatch(r"\d{1,3}", s):
                    continue
                if "의사소통 기능 예시문" in s:
                    continue
                m = re.match(r"^([ⅠⅡⅢⅣ])\.\s*(.+)$", s)
                if m:
                    section = f"{m.group(1)}. {norm_text(m.group(2))}"
                    continue
                m = re.match(r"^(\d+(?:\.\d+){0,2})\.\s*([^A-Za-z].*)$", s)
                if m and re.search(r"[가-힣]", m.group(2)):
                    cur = {"section": section, "no": m.group(1), "name": norm_text(m.group(2)), "examples": [],
                           "source": {"file": fname, "pdf_page": i + 1}}
                    out.append(cur)
                    continue
                if cur is None:
                    continue
                ex = cur["examples"]
                if ex and (s[0].islower() or not re.search(r"[.?!)]\s*$|\.\.\.\s*$", ex[-1])):
                    ex[-1] = ex[-1] + " " + s
                else:
                    ex.append(s)
                if i + 1 != cur["source"]["pdf_page"]:
                    cur["source"]["pdf_page_end"] = i + 1
    # 상위 범주(예: 1.2 진술하기와 보고하기)만 있고 예시가 없는 항목은 남겨 둔다
    return {"title": "[별표 2] 의사소통 기능과 예시문 - 나. 의사소통 기능 예시문",
            "note": "예시문은 의사소통 기능의 이해를 돕기 위한 예이며, 학교급별 언어 형식을 고려해 사용(원문 일러두기)",
            "pdf_pages": [start + 1, end], "functions": out}


if __name__ == "__main__":
    main()
