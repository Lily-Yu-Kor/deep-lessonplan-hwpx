#!/usr/bin/env python3
"""2022 개정 교육과정 로컬 데이터(data/curriculum/*.json) 조회 도구.

교과·학년(군)·영역·성취기준 코드로 핵심 아이디어, 내용 요소(지식·이해/과정·기능/가치·태도),
성취기준 문장, 성취기준 해설, 성취기준 적용 시 고려 사항을 원문 쪽수와 함께 돌려준다.

예)
  python3 lookup.py --code 6영02-05                       # 코드만으로 교과·학년군·영역 자동 판정
  python3 lookup.py --subject 영어 --grade 초5 --domain 표현
  python3 lookup.py --subject 수학 --grade 중2 --search 일차함수
  python3 lookup.py --subject 영어 --annex 길               # 영어 [별표 2] 의사소통 기능·예시문 검색
  python3 lookup.py --code 6영02-05 --render > ce.json      # render.py 입력용 조각(coreIdeas/contentElements/standards/sources)
  python3 lookup.py --list                                 # 교과·영역 목록

needs_review=true 항목은 기계 추출에서 원문 대조가 필요한 것으로 표시된 항목이다(출력에 ⚠ 표시).
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "curriculum")
CATS = [("지식·이해", "knowledge"), ("과정·기능", "skills"), ("가치·태도", "values")]

ALIASES = {
    "국어": "국어", "도덕": "도덕", "사회": "사회", "역사": "역사", "수학": "수학", "과학": "과학",
    "실과": "실과(기술·가정)", "기술가정": "실과(기술·가정)", "기술·가정": "실과(기술·가정)", "기가": "실과(기술·가정)",
    "실과(기술·가정)": "실과(기술·가정)", "실과_기술가정": "실과(기술·가정)",
    "정보": "정보", "체육": "체육", "음악": "음악", "미술": "미술", "영어": "영어",
    "바른생활": "바른 생활", "바른 생활": "바른 생활", "슬기로운생활": "슬기로운 생활", "슬기로운 생활": "슬기로운 생활",
    "즐거운생활": "즐거운 생활", "즐거운 생활": "즐거운 생활",
}
BANDS = ["초1~2", "초3~4", "초5~6", "중1~3"]


def sq(t):
    return re.sub(r"\s+", "", t or "")


_cache = {}


def load_all():
    if not _cache:
        for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
            d = json.load(open(f, encoding="utf-8"))
            _cache[d["subject"]] = d
    return _cache


def norm_subject(s):
    if not s:
        return None
    k = sq(s)
    for a, v in ALIASES.items():
        if sq(a) == k:
            return v
    for name in load_all():
        if k in sq(name):
            return name
    raise SystemExit(f"알 수 없는 교과: {s} (가능: {', '.join(load_all())})")


def norm_grade(g):
    """'초5', '5학년', '초등학교 5학년', '초5~6', '5~6학년군', '중2', '중학교' → 학년군 키"""
    if not g:
        return None
    t = sq(g)
    if t in BANDS:
        return t
    mid = t.startswith("중") or "중학" in t
    if t.startswith("고") or "고등" in t:
        raise SystemExit("고등학교 선택 과목은 로컬 데이터에 없음 → [확인 필요] 처리 또는 원문 확인")
    if mid:
        return "중1~3"
    m = re.search(r"(\d)", t)
    if not m:
        raise SystemExit(f"학년을 해석할 수 없음: {g}")
    n = int(m.group(1))
    return {1: "초1~2", 2: "초1~2", 3: "초3~4", 4: "초3~4", 5: "초5~6", 6: "초5~6"}.get(n) or SystemExit(f"학년 범위 밖: {g}")


def band_of_code(code):
    m = re.match(r"(\d{1,2})", code)
    return {"2": "초1~2", "4": "초3~4", "6": "초5~6", "9": "중1~3"}.get(m.group(1)) if m else None


def norm_code(c):
    c = re.sub(r"[\[\]\s]", "", c or "")
    return re.sub(r"[–−‐]", "-", c)


def find_code(code):
    code = norm_code(code)
    for subj, d in load_all().items():
        for bl in d["standard_blocks"]:
            for s in bl["standards"]:
                if s["code"] == code:
                    return subj, bl, s
    return None, None, None


def bigrams(t):
    t = sq(re.sub(r"[^\w가-힣]", "", t))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def domain_by_name(d, name):
    if not name:
        return None
    for dm in d["domains"]:
        if sq(dm["name"]) == sq(name) or sq(name) == str(dm["no"]):
            return dm
    for dm in d["domains"]:
        if sq(name) in sq(dm["name"]) or sq(dm["name"]) in sq(name):
            return dm
    return None


def infer_domain(d, band, block):
    """과학·사회처럼 성취기준이 '단원'으로 묶인 교과: 단원의 각 성취기준 문장이 어느 영역의 내용 요소(지식·이해)
    항목을 가장 많이 덮는지로 영역을 추정한다(항목별 최대 포함률의 평균)."""
    stds = [bigrams(block.get("domain", "") + " " + s["text"]) for s in block["standards"]]
    ranked = []
    for dm in d["domains"]:
        cats = dm["content_elements"].get(band) or {}
        items = [bigrams(it["text"]) for c in ("지식·이해", "과정·기능") for it in cats.get(c, [])]
        items = [b for b in items if len(b) >= 2]
        if not items:
            continue
        sc = sum(max(len(q & b) / len(b) for b in items) for q in stds) / len(stds)
        ranked.append((round(sc, 2), dm))
    ranked.sort(key=lambda x: -x[0])
    return ranked


def item_out(it):
    o = {"text": it["text"], "source": it["source"]}
    for k in ("sub", "span", "needs_review", "review_reason"):
        if it.get(k):
            o[k] = it[k]
    return o


def lookup(subject=None, grade=None, domain=None, code=None):
    res = {"query": {"subject": subject, "grade": grade, "domain": domain, "code": code}, "notes": []}
    block = std = None
    if code:
        subj, block, std = find_code(code)
        if not std:
            res["error"] = f"로컬 데이터에 성취기준 {norm_code(code)} 없음 → cu2022로 확인하거나 [확인 필요]"
            return res
        if subject and norm_subject(subject) != subj:
            res["notes"].append(f"코드 {std['code']}는 {subj} 성취기준이다(요청 교과 {subject}).")
        subject = subj
        band = std.get("grade_band") or band_of_code(std["code"])
        if grade and norm_grade(grade) != band:
            res["notes"].append(f"코드 학년군 {band}를 우선함(요청 {grade}).")
    else:
        subject = norm_subject(subject)
        band = norm_grade(grade)
    if not subject:
        raise SystemExit("--subject 또는 --code 필요")
    d = load_all()[subject]
    res.update({"subject": subject, "grade_band": band, "curriculum": d["curriculum"], "book": d["book"],
                "source_file": d["source_file"]})
    if band is None and d["domains"]:
        bands = sorted({b for dm in d["domains"] for b in dm["content_elements"]}, key=lambda x: BANDS.index(x) if x in BANDS else 9)
        if len(bands) == 1:
            band = res["grade_band"] = bands[0]
    dm = domain_by_name(d, domain)
    if domain and not dm:
        res["notes"].append(f"영역 '{domain}'을 찾지 못함. 가능한 영역: {', '.join(x['name'] for x in d['domains'])}")
    if block is not None and dm is None:
        if block.get("heading_kind") == "영역":
            dm = domain_by_name(d, block["domain"])
        else:
            ranked = infer_domain(d, band, block)
            res["unit"] = block["domain"]
            if ranked:
                dm = ranked[0][1]
                res["domain_inferred"] = {
                    "domain": dm["name"], "score": ranked[0][0],
                    "candidates": [{"domain": x.name if hasattr(x, "name") else x["name"], "score": sc} for sc, x in ranked[:3]],
                    "note": "이 교과는 성취기준이 영역이 아닌 학년군별 단원으로 묶여 있어, 내용 요소와 글자 겹침으로 영역을 추정함. "
                            "후보를 보고 맞는 영역을 --domain 으로 지정할 것"}
    if dm is None and len(d["domains"]) == 1:
        dm = d["domains"][0]
    doms = [dm] if dm else d["domains"]
    res["domains"] = []
    for x in doms:
        ce = x["content_elements"].get(band) if band else None
        o = {"no": x["no"], "name": x["name"], "source": x.get("source"),
             "core_ideas": [item_out(c) for c in x["core_ideas"]]}
        if band:
            o["content_elements"] = {c: [item_out(i) for i in (ce or {}).get(c, [])] for c, _ in CATS}
            if not ce:
                o["notes"] = [f"{band} 내용 요소 없음(이 영역은 해당 학년군 칸이 비어 있거나 '-')"]
        else:
            o["content_elements_by_band"] = {b: {c: [item_out(i) for i in v.get(c, [])] for c, _ in CATS}
                                             for b, v in x["content_elements"].items()}
        res["domains"].append(o)
    # 성취기준 묶음
    blocks = []
    for bl in d["standard_blocks"]:
        if block is not None and bl is not block:
            continue
        stds = [s for s in bl["standards"] if not band or (s.get("grade_band") or band_of_code(s["code"])) == band]
        if not stds:
            continue
        if block is None and dm is not None and bl.get("heading_kind") == "영역" and sq(bl["domain"]) != sq(dm["name"]):
            continue
        blocks.append({
            "grade_heading": bl.get("grade_heading"), "section": bl.get("section"),
            "heading": bl["domain"], "heading_kind": bl.get("heading_kind"),
            "standards": [{k: s[k] for k in ("code", "text", "grade_band", "subtopic", "source", "explanation",
                                              "needs_review", "review_reason") if s.get(k)} for s in stds],
            "explanations": bl["explanations"], "considerations": bl["considerations"],
            "extras": bl.get("extras") or {}, "source": bl.get("source")})
    res["standard_blocks"] = blocks
    if std:
        res["standard"] = {k: std[k] for k in ("code", "text", "grade_band", "source", "explanation",
                                                "needs_review", "review_reason") if std.get(k)}
    return res


def search(subject, keyword, grade=None):
    band = norm_grade(grade) if grade else None
    subs = [norm_subject(subject)] if subject else list(load_all())
    k = sq(keyword)
    hits = []
    for sj in subs:
        d = load_all()[sj]
        for dm in d["domains"]:
            for ci in dm["core_ideas"]:
                if k in sq(ci["text"]):
                    hits.append({"subject": sj, "kind": "핵심 아이디어", "domain": dm["name"], **item_out(ci)})
            for b, cats in dm["content_elements"].items():
                if band and b != band:
                    continue
                for c, its in cats.items():
                    for it in its:
                        if k in sq(it["text"]):
                            hits.append({"subject": sj, "kind": f"내용 요소/{c}", "domain": dm["name"], "band": b, **item_out(it)})
        for bl in d["standard_blocks"]:
            for s in bl["standards"]:
                if band and (s.get("grade_band") or band_of_code(s["code"])) != band:
                    continue
                if k in sq(s["text"]) or k == sq(s["code"]):
                    hits.append({"subject": sj, "kind": "성취기준", "code": s["code"], "text": s["text"],
                                 "heading": bl["domain"], "source": s["source"]})
            for kind, arr in (("성취기준 해설", bl["explanations"]), ("적용 시 고려 사항", bl["considerations"])):
                for e in arr:
                    if k in sq(e["text"]) and (not band or any(band_of_code(c) == band for c in e.get("codes", [])) or not e.get("codes")):
                        hits.append({"subject": sj, "kind": kind, "heading": bl["domain"], "text": e["text"], "source": e["source"]})
    return hits


def annex(keyword=None):
    a = load_all()["영어"].get("annex2_communicative_functions") or {}
    fs = a.get("functions", [])
    if keyword:
        k = sq(keyword).lower()
        fs = [f for f in fs if k in sq(f["name"]).lower() or any(k in sq(e).lower() for e in f.get("examples", []))]
    return {"title": a.get("title"), "note": a.get("note"), "functions": fs}


def to_render(res, pick=None):
    """render.py 입력 조각. 모든 후보를 넣으므로 차시에 맞는 항목만 남기고 지울 것."""
    out = {"meta": {"subject": res.get("subject"), "domain": None}, "coreIdeas": [], "standards": [],
           "contentElements": {e: [] for _, e in CATS}, "sources": {}}
    pages = set()
    for dm in res.get("domains", [])[:1]:
        out["meta"]["domain"] = dm["name"]
        out["coreIdeas"] = [c["text"] for c in dm["core_ideas"]]
        pages |= {c["source"]["pdf_page"] for c in dm["core_ideas"]}
        for c, e in CATS:
            for it in dm.get("content_elements", {}).get(c, []):
                out["contentElements"][e].append(it["text"])
                pages.add(it["source"]["pdf_page"])
    if res.get("standard"):
        out["standards"] = [{"code": res["standard"]["code"], "text": res["standard"]["text"]}]
    std_pages = sorted({s["source"]["pdf_page"] for b in res.get("standard_blocks", []) for s in b["standards"]})
    src = res.get("source_file", "")
    out["sources"]["coreIdeas_contentElements"] = (
        f"교육부 고시 제2022-33호 {res.get('book')} 내용 체계({out['meta']['domain']} 영역, {res.get('grade_band')}) — "
        f"원문 파일 '{src}' PDF {', '.join(map(str, sorted(pages)))}쪽 (lookup.py 로컬 데이터)")
    if res.get("standard"):
        out["sources"]["standards"] = (f"{res['book']} 성취기준 [{res['standard']['code']}] 원문 PDF "
                                       f"{res['standard']['source']['pdf_page']}쪽(로컬 데이터), cu2022와 대조 권장")
    elif std_pages:
        out["sources"]["standards"] = f"{res.get('book')} 성취기준 원문 PDF {', '.join(map(str, std_pages))}쪽"
    if res.get("domain_inferred"):
        out["sources"]["domain_note"] = res["domain_inferred"]["note"]
    return out


def verify_input(path):
    """지도안 입력 JSON의 coreIdeas·contentElements 문구가 로컬 데이터 원문과 글자 그대로 일치하는지 검사.
    sub(하위 줄)는 교사가 차시에 맞춰 덧붙인 설명으로 보고 검사하지 않는다."""
    data = json.load(open(path, encoding="utf-8"))
    docs = [data] + list(data.get("lessons", []))
    subj = norm_subject(data.get("meta", {}).get("subject"))
    d = load_all()[subj]
    meta = data.get("meta", {})
    want_dom = [sq(x) for x in re.split(r"[,·/]", meta.get("domain") or "") if x.strip()]
    try:
        want_band = norm_grade(meta.get("grade") or meta.get("target"))
    except SystemExit:
        want_band = None
    pool = {"coreIdeas": {}, "knowledge": {}, "skills": {}, "values": {}}
    for dm in d["domains"]:
        for c in dm["core_ideas"]:
            pool["coreIdeas"].setdefault(sq(c["text"]), []).append((dm["name"], None, c))
        for b, cats in dm["content_elements"].items():
            for c, e in CATS:
                for it in cats.get(c, []):
                    pool[e].setdefault(sq(it["text"]), []).append((dm["name"], b, it))

    def best(hits):
        def rank(h):
            return (not want_dom or sq(h[0]) in want_dom) * 2 + (h[1] is None or not want_band or h[1] == want_band)
        return max(hits, key=rank)
    rep = []
    for doc in docs:
        fields = [("coreIdeas", x) for x in doc.get("coreIdeas") or []]
        for _, e in CATS:
            fields += [(e, x) for x in (doc.get("contentElements") or {}).get(e) or []]
        for key, x in fields:
            t = x["text"] if isinstance(x, dict) else x
            hits = pool[key].get(sq(t))
            if hits:
                dmn, b, it = best(hits)
                warn = []
                if want_dom and sq(dmn) not in want_dom:
                    warn.append(f"지정 영역({meta.get('domain')})이 아닌 '{dmn}' 영역 문구")
                if b and want_band and b != want_band:
                    warn.append(f"{want_band}가 아닌 {b} 칸 문구")
                rep.append({"field": key, "text": t, "ok": True, "domain": dmn, "band": b, "warn": warn,
                            "pdf_page": it["source"]["pdf_page"], "needs_review": bool(it.get("needs_review"))})
            else:
                rep.append({"field": key, "text": t, "ok": "[확인 필요]" in t, "note": "로컬 데이터에 없는 문구" if "[확인 필요]" not in t else "확인 필요 표시"})
    return subj, rep


def flag(it):
    return " ⚠" + (f"({it.get('review_reason')})" if it.get("review_reason") else "") if it.get("needs_review") else ""


def pg(src):
    if not src:
        return ""
    e = f"~{src['pdf_page_end']}" if src.get("pdf_page_end") else ""
    return f" [p{src['pdf_page']}{e}]"


def print_text(res):
    if res.get("error"):
        print(res["error"]); return
    print(f"# {res['subject']} {res.get('grade_band') or '(학년군 전체)'} — {res['book']}")
    print(f"  원문 파일: {res['source_file']}  (쪽수는 PDF 쪽 번호)")
    for n in res.get("notes", []):
        print("  ※", n)
    if res.get("unit"):
        print(f"  단원: {res['unit']}")
    if res.get("domain_inferred"):
        di = res["domain_inferred"]
        cands = ", ".join(f"{c['domain']}({c['score']})" for c in di.get("candidates", []))
        print(f"  ※ 영역 추정(교사 확인): {di['domain']} | 후보 {cands}\n    {di['note']}")
    if res.get("standard"):
        s = res["standard"]
        print(f"\n## 성취기준\n[{s['code']}] {s['text']}{pg(s['source'])}{flag(s)}")
    for dm in res["domains"]:
        print(f"\n## 영역 ({dm['no']}) {dm['name']}{pg(dm.get('source'))}")
        print("### 핵심 아이디어")
        for c in dm["core_ideas"]:
            print(f"- {c['text']}{pg(c['source'])}{flag(c)}")
        ces = {res.get("grade_band"): dm["content_elements"]} if "content_elements" in dm else dm["content_elements_by_band"]
        for b, ce in ces.items():
            print(f"### 내용 요소 {b}")
            for n in dm.get("notes", []):
                print("  ※", n)
            for c, _ in CATS:
                print(f"- {c}")
                for it in ce.get(c, []):
                    sub = f"({it['sub']}) " if isinstance(it.get("sub"), str) else ""
                    print(f"    · {sub}{it['text']}{pg(it['source'])}{flag(it)}")
    for bl in res["standard_blocks"]:
        print(f"\n## 성취기준 묶음: {bl.get('grade_heading') or ''} {('<' + bl['section'] + '> ') if bl.get('section') else ''}"
              f"{bl['heading']} ({bl.get('heading_kind')})")
        for s in bl["standards"]:
            sub = f" <{s['subtopic']}>" if s.get("subtopic") else ""
            print(f"  [{s['code']}] {s['text']}{sub}{pg(s['source'])}{flag(s)}")
        if bl["explanations"]:
            print("  (가) 성취기준 해설")
            for e in bl["explanations"]:
                print(f"   • {e['text']}{pg(e['source'])}{flag(e)}")
        if bl["considerations"]:
            print("  (나) 성취기준 적용 시 고려 사항")
            for e in bl["considerations"]:
                print(f"   • {e['text']}{pg(e['source'])}{flag(e)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", "-s")
    ap.add_argument("--grade", "-g", help="초5, 5학년, 초5~6, 중2 등")
    ap.add_argument("--domain", "-d")
    ap.add_argument("--code", "-c", help="성취기준 코드(예: 6영02-05)")
    ap.add_argument("--search", help="키워드 검색(핵심 아이디어·내용 요소·성취기준·해설·고려 사항)")
    ap.add_argument("--annex", nargs="?", const="", help="영어 [별표 2] 의사소통 기능·예시문 검색(키워드 생략 시 전체)")
    ap.add_argument("--render", action="store_true", help="render.py 입력용 JSON 조각 출력")
    ap.add_argument("--json", action="store_true", help="전체 결과 JSON 출력")
    ap.add_argument("--list", action="store_true", help="교과·영역·학년군 목록")
    ap.add_argument("--verify", metavar="INPUT_JSON", help="지도안 JSON의 핵심 아이디어·내용 요소가 원문 문구 그대로인지 검사")
    a = ap.parse_args()
    if a.verify:
        subj, rep = verify_input(a.verify)
        bad = [r for r in rep if not r["ok"]]
        for r in rep:
            mark = "OK " if r["ok"] and not r.get("note") else ("?? " if r.get("note") == "확인 필요 표시" else "NG ")
            where = f"{r.get('domain')} {r.get('band') or ''} p{r.get('pdf_page')}" if r.get("domain") else r.get("note", "")
            w = "".join(f" ⚠{x}" for x in r.get("warn") or [])
            print(f"{mark}{r['field']:<10} {r['text']}  — {where}{' ⚠원문 대조 필요' if r.get('needs_review') else ''}{w}")
        print(f"{subj}: {len(rep) - len(bad)}/{len(rep)} 일치")
        sys.exit(1 if bad else 0)
    if a.list:
        for sj, d in load_all().items():
            if a.subject and sj != norm_subject(a.subject):
                continue
            bands = sorted({b for dm in d["domains"] for b in dm["content_elements"]}, key=lambda x: BANDS.index(x) if x in BANDS else 9)
            print(f"{sj}: 학년군 {', '.join(bands)} | 영역 " + ", ".join(f"({dm['no']}){dm['name']}" for dm in d["domains"]))
        return
    if a.annex is not None:
        r = annex(a.annex)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1)); return
        print(f"# {r['title']}\n  ※ {r['note']}")
        for f in r["functions"]:
            print(f"- {f['section']} {f['no']} {f['name']}{pg(f['source'])}")
            for e in f.get("examples", []):
                print(f"    {e}")
        return
    if a.search:
        hits = search(a.subject, a.search, a.grade)
        if a.json:
            print(json.dumps(hits, ensure_ascii=False, indent=1)); return
        for h in hits:
            lab = h.get("code") or h.get("band") or h.get("heading") or ""
            print(f"[{h['subject']}] {h['kind']} {h.get('domain', '')} {lab}: {h['text']}{pg(h['source'])}{flag(h)}")
        print(f"({len(hits)}건)")
        return
    if not (a.subject or a.code):
        ap.error("--subject 또는 --code 가 필요하다")
    res = lookup(a.subject, a.grade, a.domain, a.code)
    if a.render:
        print(json.dumps(to_render(res), ensure_ascii=False, indent=1))
    elif a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print_text(res)
    if res.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
