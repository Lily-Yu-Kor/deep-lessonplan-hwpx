#!/usr/bin/env python3
"""추출 결과 자동 대조: 각 항목 글자(공백 제외)가 기록된 PDF 쪽 원문에 그대로 있는지 확인하고,
무작위 표본을 뽑아 원문 쪽 이미지(pdftoppm)를 만든다.
사용: python3 scripts/qa_curriculum.py --pdf-dir DIR [--sample 4] [--img-out /tmp/qa]"""
import argparse, glob, json, os, random, re, subprocess, collections

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "curriculum")


def squash(s):
    s = s.replace("\u22c5", "·").replace("ㆍ", "·").replace("∼", "~").replace("–", "-")
    return re.sub(r"[\s·•\uf09fŸ]", "", s)


def page_text(pdf, a, b, cache={}):
    k = (pdf, a, b)
    if k not in cache:
        r = subprocess.run(["pdftotext", "-raw", "-f", str(a), "-l", str(b), pdf, "-"], capture_output=True, text=True)
        # 머리말·꼬리말(쪽 번호, '...교육과정' 머리글)은 항목 사이에 끼어들므로 뺀다
        t = "\n".join(l for l in r.stdout.splitlines()
                      if not re.fullmatch(r"\s*\d{1,3}\s*", l) and not ("교육과정" in l and len(l.strip()) < 40)
                      and l.strip() not in ("바른 생활", "슬기로운 생활", "즐거운 생활"))
        cache[k] = squash(t)
    return cache[k]


def items(d):
    for dm in d["domains"]:
        for c in dm["core_ideas"]:
            yield "핵심아이디어", c
        for band, cats in dm["content_elements"].items():
            for cat, its in cats.items():
                for it in its:
                    yield f"내용요소/{band}/{cat}", it
    for b in d["standard_blocks"]:
        for s in b["standards"]:
            yield "성취기준", s
        for e in b["explanations"]:
            yield "해설", e
        for e in b["considerations"]:
            yield "고려사항", e
        for k, v in b.get("extras", {}).items():
            for e in v:
                yield k, e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=os.environ.get("CURRICULUM_PDF_DIR", "pdf"))
    ap.add_argument("--sample", type=int, default=4)
    ap.add_argument("--img-out", default="/tmp/qa_curriculum")
    ap.add_argument("--seed", type=int, default=20261008)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    os.makedirs(a.img_out, exist_ok=True)
    report = {}
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        pdf = os.path.join(a.pdf_dir, d["source_file"])
        tot = ok = 0
        bad = []
        allit = list(items(d))
        for kind, it in allit:
            src = it["source"]
            p0 = src["pdf_page"]
            p1 = src.get("pdf_page_end", p0)
            txt = squash(it["text"])
            if it.get("code"):
                txt = squash(it["text"])
            tot += 1
            if txt and txt in page_text(pdf, p0, p1 + (1 if kind.startswith("내용요소") else 0)):
                ok += 1
            else:
                bad.append((kind, p0, it["text"][:80]))
        # 표본: 내용 체계 표 한 쪽에서 2개, 성취기준 본문 한 쪽에서 2개(쪽 이미지 2장을 나란히 붙여 대조)
        tab = [x for x in allit if x[0].startswith(("내용요소", "핵심"))]
        pro = [x for x in allit if not x[0].startswith(("내용요소", "핵심"))]
        sample = []
        for pool in (tab, pro):
            if not pool:
                continue
            pg = rnd.choice(sorted({x[1]["source"]["pdf_page"] for x in pool}))
            cand = [x for x in pool if x[1]["source"]["pdf_page"] == pg]
            sample += rnd.sample(cand, min(a.sample // 2, len(cand)))
        smp, pages = [], []
        for kind, it in sample:
            p = it["source"]["pdf_page"]
            png = os.path.join(a.img_out, f"{d['subject'].replace(' ', '')}_p{p}")
            if not glob.glob(png + "-*.png"):
                subprocess.run(["pdftoppm", "-f", str(p), "-l", str(p), "-r", "100", "-png", pdf, png], check=True)
            img = sorted(glob.glob(png + "-*.png"))[0]
            if img not in pages:
                pages.append(img)
            smp.append({"kind": kind, "pdf_page": p, "text": it["text"], "needs_review": bool(it.get("needs_review")),
                        "image": img})
        try:
            from PIL import Image
            ims = [Image.open(x) for x in pages]
            W = sum(i.width for i in ims); H = max(i.height for i in ims)
            canvas = Image.new("RGB", (W, H), "white")
            x = 0
            for im in ims:
                canvas.paste(im, (x, 0)); x += im.width
            mont = os.path.join(a.img_out, f"QA_{d['subject'].replace(' ', '')}.png")
            canvas.save(mont)
        except Exception as e:  # pragma: no cover
            mont = None
        report[d["subject"]] = {"items": tot, "char_match": ok, "mismatch": bad, "sample": smp, "montage": mont}
        print(f"{d['subject']:10s} 항목 {tot:4d}  원문 글자 일치 {ok:4d} ({ok / max(tot, 1):.1%})  불일치 {len(bad)}")
    json.dump(report, open(os.path.join(a.img_out, "qa_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
