#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview.py — HWPX를 '한글처럼' PDF로 렌더해 쪽 넘김을 확인한다.
  LibreOffice + H2Orestart(.oxt)로 hwpx를 연 뒤, H2Orestart가 무시하는 한글 표 속성을
  hwpx 원본에서 읽어 LibreOffice 표에 다시 적용한 후 PDF/PNG로 내보낸다.

  H2Orestart 0.7.x 한계(소스 확인) → 보정 내용
   - pageBreak(CELL/TABLE/NONE) 무시, 표 Split=false 고정      → Split / 행 IsSplitAllowed 적용
   - repeatHeader 무시                                       → RepeatHeadline + HeaderRowCount(머리행=header="1")
   - 행 높이를 저장값으로 고정(IsAutoHeight=false)             → 자동 높이(저장값은 최소 높이) — 한글 동작과 같게
   - 셀 안쪽 여백 0 고정                                      → hwpx cellMargin 적용
   - 탭 채움 문자를 항상 '-'로 설정                            → 채움 없음
   - 한/영 사이 자동 간격(LibreOffice 기본)                     → 끔(hwpx autoSpacing=0)

사용:  python3 preview.py file.hwpx [-o outdir] [--dpi 70] [--raw]
       --raw : 보정 없이 H2Orestart 결과 그대로(비교용)
필요:  soffice, H2Orestart 확장(unopkg add H2Orestart.oxt), python3-uno, pdftoppm
"""
import argparse, os, subprocess, sys, time, zipfile, shutil, tempfile
from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HERE = os.path.dirname(os.path.abspath(__file__))


def hwpx_tables(path):
    with zipfile.ZipFile(path) as z:
        secs = sorted(n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml"))
        out = []
        for s in secs:
            root = etree.fromstring(z.read(s))
            for tbl in root.iter(f"{{{HP}}}tbl"):
                # 중첩 표 제외(최상위만)
                if any(a.tag == f"{{{HP}}}tbl" for a in tbl.iterancestors()):
                    continue
                rows = tbl.findall(f"{{{HP}}}tr")
                hdr = 0
                for tr in rows:
                    tcs = tr.findall(f"{{{HP}}}tc")
                    if tcs and all(tc.get("header") == "1" for tc in tcs):
                        hdr += 1
                    else:
                        break
                cm = tbl.find(f".//{{{HP}}}cellMargin")
                out.append(dict(pageBreak=tbl.get("pageBreak", "CELL"), repeat=tbl.get("repeatHeader") == "1",
                                header_rows=hdr, nrows=len(rows),
                                margin={k: int(cm.get(k)) for k in ("left", "right", "top", "bottom")} if cm is not None else None))
        return out


def hangul_line_spacing(p):
    """한글의 '글자에 따라 %' 줄 간격(줄 높이 = 글자 크기 × %)을 LibreOffice 고정 줄 간격으로 흉내.
    LibreOffice 비례 간격은 글꼴 자체 줄 높이(대략 1.15~1.45em)에 %를 곱해 한글보다 줄이 높아진다."""
    ls = p.ParaLineSpacing
    if ls.Mode != 0:      # PROP 만 변환
        return
    pct = ls.Height
    size_pt = p.CharHeight
    ls.Mode = 3           # FIX
    ls.Height = int(round(size_pt * pct / 100 * 2540 / 72))
    p.ParaLineSpacing = ls


def hwp2mm100(v):
    return int(round(v * 2540 / 7200))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hwpx")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--dpi", type=int, default=70)
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--font-lines", action="store_true",
                    help="줄 높이를 글꼴 기준(LibreOffice 기본)으로 둠 — 더 보수적인(긴) 미리보기")
    a = ap.parse_args()
    src = os.path.abspath(a.hwpx)
    outdir = os.path.abspath(a.outdir or os.path.dirname(src))
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0] + ("_raw" if a.raw else "")
    pdf = os.path.join(outdir, base + ".pdf")

    import uno
    from com.sun.star.beans import PropertyValue

    def pv(n, v):
        p = PropertyValue(); p.Name = n; p.Value = v; return p

    env = dict(os.environ)
    env["FONTCONFIG_FILE"] = os.path.join(HERE, "fonts-render.conf")
    pipe = f"dlp{os.getpid()}"
    prof = tempfile.mkdtemp(prefix="dlp-lo-")
    # 확장은 사용자 프로필에 설치되어 있으므로 기본 프로필을 복사해 사용
    user_prof = os.path.expanduser("~/.config/libreoffice/4")
    if os.path.isdir(user_prof):
        shutil.copytree(user_prof, os.path.join(prof, "4"), dirs_exist_ok=True)
        prof_url = "file://" + os.path.join(prof, "4")
    else:
        prof_url = "file://" + prof
    proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--nologo", "--norestore",
                             f"-env:UserInstallation={prof_url}", f"--accept=pipe,name={pipe};urp;"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        ctx = None
        for _ in range(60):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.5)
        if ctx is None:
            sys.exit("LibreOffice 연결 실패")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(src), "_blank", 0, (pv("Hidden", True),))
        if doc is None:
            sys.exit("hwpx 열기 실패(H2Orestart 설치 확인)")
        meta = hwpx_tables(src)
        # 최상위 표를 문서 순서대로
        lo_tables = []
        en = doc.getText().createEnumeration()
        while en.hasMoreElements():
            el = en.nextElement()
            if el.supportsService("com.sun.star.text.TextTable"):
                lo_tables.append(el)
        report = []
        if not a.raw:
            for i, t in enumerate(lo_tables):
                m = meta[i] if i < len(meta) else None
                if m is None:
                    continue
                t.Split = m["pageBreak"] != "NONE"
                rows = t.getRows()
                for r in range(rows.getCount()):
                    row = rows.getByIndex(r)
                    row.IsAutoHeight = True
                    row.IsSplitAllowed = m["pageBreak"] == "CELL"
                if m["repeat"] and m["header_rows"] > 0:
                    t.RepeatHeadline = True
                    t.HeaderRowCount = m["header_rows"]
                for name in t.getCellNames():
                    c = t.getCellByName(name)
                    if m["margin"]:
                        c.LeftBorderDistance = hwp2mm100(m["margin"]["left"])
                        c.RightBorderDistance = hwp2mm100(m["margin"]["right"])
                        c.TopBorderDistance = hwp2mm100(m["margin"]["top"])
                        c.BottomBorderDistance = hwp2mm100(m["margin"]["bottom"])
                    pe = c.getText().createEnumeration()
                    while pe.hasMoreElements():
                        p = pe.nextElement()
                        if not p.supportsService("com.sun.star.text.Paragraph"):
                            continue
                        try:
                            if not a.font_lines:
                                hangul_line_spacing(p)
                            p.ParaIsCharacterDistance = False
                            tabs = list(p.ParaTabStops)
                            if tabs:
                                for ts in tabs:
                                    ts.FillChar = " "
                                p.ParaTabStops = tuple(tabs)
                        except Exception:
                            pass
                report.append(f"표{i+1}: rows={m['nrows']} pageBreak={m['pageBreak']} "
                              f"repeatHeader={'Y' if m['repeat'] else 'N'} headerRows={m['header_rows']}")
        doc.storeToURL(uno.systemPathToFileUrl(pdf), (pv("FilterName", "writer_pdf_Export"),))
        doc.close(True)
    finally:
        try:
            desktop.terminate()
        except Exception:
            pass
        time.sleep(1)
        proc.kill()
        shutil.rmtree(prof, ignore_errors=True)
    prefix = os.path.join(outdir, base + "-p")
    subprocess.run(["pdftoppm", "-r", str(a.dpi), "-png", pdf, prefix], check=True)
    pages = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    n = next((l.split()[-1] for l in pages.splitlines() if l.startswith("Pages")), "?")
    for r in report:
        print(r)
    print(f"PDF: {pdf}  pages={n}")
    print("PNG:", " ".join(sorted(f for f in os.listdir(outdir) if f.startswith(base + "-p"))))


if __name__ == "__main__":
    main()
