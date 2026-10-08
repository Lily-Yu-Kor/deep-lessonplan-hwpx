#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deep-lessonplan / render.py
JSON -> .hwpx 생성기 ('2022 개정 교육과정 기반 수업설계 도움자료'의 깊이있는 수업 양식)

  mode = "lesson" : 깊이있는 수업을 위한 차시별 교수·학습 지도안 (도움자료 40쪽 양식)
  mode = "unit"   : 깊이있는 단원 수업 설계안 + 깊이있는 수업 과정안 (도움자료 38쪽 양식)
                    (선택) "lessons": [...] 를 넣으면 뒤에 차시별 지도안을 새 쪽으로 이어 붙임

사용법:
  python3 render.py input.json -o out.hwpx [--allowed-codes 6영02-05,6영01-04]
                    [--allow-dialog] [--strict] [--check-only]

설계 원칙
  * 교사-학생 문답(대화 스크립트) 금지: '교사:', '학생:', 'T:', 'S:' 형태가 있으면 오류.
  * 성취기준 코드는 반드시 cu2022 검색 결과에서 고른 것만(--allowed-codes 로 검증).
  * 핵심 아이디어·내용 요소는 교육과정 원문에서 확인한 것만. 확인 못 하면 '[확인 필요]'.
    빈 칸은 자동으로 '[확인 필요]'(빨간 글씨)로 채우고 경고를 출력한다.
  * 표 쪽 넘김: 모든 표 pageBreak="CELL"(셀 단위로 나눔), 제목행 repeatHeader="1".
    과정 표는 활동 블록마다 한 행 + 긴 블록은 여러 행으로 자동 분할,
    표 제목행이 쪽 끝에 홀로 남을 것 같으면 그 표 앞에서 쪽을 넘긴다(높이 추정).
"""
import argparse
import json
import math
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _xml_escape

# ----------------------------------------------------------------------------
# 단위/페이지
# ----------------------------------------------------------------------------
HWPUNIT_PER_MM = 7200 / 25.4
def mm(v):
    return int(round(v * HWPUNIT_PER_MM))

PAGE_W, PAGE_H = 59528, 84188          # A4
MARGIN = dict(left=mm(15), right=mm(15), top=mm(10), bottom=mm(10), header=mm(8), footer=mm(8))
BODY_W = PAGE_W - MARGIN["left"] - MARGIN["right"]
BODY_H = PAGE_H - MARGIN["top"] - MARGIN["bottom"] - MARGIN["header"] - MARGIN["footer"]
TABLE_W = BODY_W - 40
CELL_MX, CELL_MY = 400, 220             # 셀 안쪽 여백 (좌우, 상하)

CONFIRM = "[확인 필요]"

THEMES = {
    # 도움자료 38·40쪽 빈 양식(청록)
    "teal":   dict(title="#3DBDB0", head="#7FD3CA", label="#E4F5F2", accent="#16998B",
                   tag="#4EC3B7", line="#8A8A8A", strong="#3DBDB0"),
    # 도움자료 영어 예시(보라)
    "purple": dict(title="#7E84D6", head="#A2A6E3", label="#E8E9F8", accent="#5B61C4",
                   tag="#7E84D6", line="#8A8A8A", strong="#7E84D6"),
    # 흑백 인쇄용
    "mono":   dict(title="#595959", head="#A6A6A6", label="#EDEDED", accent="#262626",
                   tag="#595959", line="#7F7F7F", strong="#404040"),
}

LIFE_CONTEXTS = ["개인과 사회 공동의 행복", "정체성과 자기주도성", "보편적 사회복지", "포용력과 이해력",
                 "공감과 상호 협력", "생태전환과 기후변화", "디지털 전환과 AI", "책임 있는 민주시민"]
METHODS = ["협동학습", "탐구학습", "문제중심학습", "토의·토론학습",
           "프로젝트 학습", "거꾸로 학습", "블렌디드 러닝"]
CODE_RE = re.compile(r"\b\d{1,2}[가-힣]{1,6}\d?\d{2}-\d{2}(?:-\d{2})?\b")
DIALOG_RE = re.compile(r"(^|[\s•·\-–(])(교사|학생|선생님|학생들|T|S|S\d|T\d)\s*[:：]")


def esc(s):
    return _xml_escape(str(s), {'"': "&quot;"})


# ----------------------------------------------------------------------------
# 스타일 레지스트리 (header.xml)
# ----------------------------------------------------------------------------
class Styles:
    def __init__(self, font="맑은 고딕", base_pt=10.0, line_pct=150, theme="teal"):
        self.font = font
        self.base_pt = base_pt
        self.line_pct = line_pct
        self.t = THEMES.get(theme, THEMES["teal"])
        self.char, self.para, self.bf = {}, {}, {}
        self.bf_list, self.char_list, self.para_list = [], [], []
        self.tab_sets = []          # tabPr id = index+1 (0 = 탭 없음)
        # 1: 테두리 없음(기본)
        self.border_fill(None, ("NONE",) * 4)
        self.charpr()                      # id 0 기본
        self.parapr()                      # id 0 기본

    # --- borderFill ---------------------------------------------------------
    def border_fill(self, fill, sides, color=None, widths=None):
        """sides: (left,right,top,bottom) 선 종류 'SOLID'/'NONE'/'DOT'.
        widths: 같은 순서 선 굵기('0.12 mm' 등)."""
        color = color or self.t["line"]
        widths = tuple(widths or ("0.12 mm",) * 4)
        key = (fill, tuple(sides), color, widths)
        if key in self.bf:
            return self.bf[key]
        bid = len(self.bf_list) + 1                       # id == 1-based 위치
        names = ["leftBorder", "rightBorder", "topBorder", "bottomBorder"]
        x = (f'<hh:borderFill id="{bid}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
             '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>')
        for n, s, w in zip(names, sides, widths):
            c = color if not (isinstance(w, str) and w.startswith("!")) else self.t["strong"]
            w2 = w.lstrip("!")
            x += f'<hh:{n} type="{s}" width="{w2}" color="{c}"/>'
        x += '<hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>'
        if fill:
            x += f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#999999" alpha="0"/></hc:fillBrush>'
        x += '</hh:borderFill>'
        self.bf[key] = str(bid)
        self.bf_list.append(x)
        return str(bid)

    # --- charPr -------------------------------------------------------------
    def charpr(self, pt=None, bold=False, color="#000000", shade=None):
        pt = pt or self.base_pt
        key = (pt, bold, color, shade)
        if key in self.char:
            return self.char[key]
        cid = len(self.char_list)
        h = int(round(pt * 100))
        x = (f'<hh:charPr id="{cid}" height="{h}" textColor="{color}" shadeColor="{shade or "none"}" '
             'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">'
             '<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
             '<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
             '<hh:spacing hangul="-3" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
             '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
             '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
             + ('<hh:bold/>' if bold else '') +
             '<hh:underline type="NONE" shape="SOLID" color="#000000"/>'
             '<hh:strikeout shape="NONE" color="#000000"/><hh:outline type="NONE"/>'
             '<hh:shadow type="NONE" color="#B2B2B2" offsetX="10" offsetY="10"/></hh:charPr>')
        self.char[key] = str(cid)
        self.char_list.append(x)
        return str(cid)

    # --- paraPr -------------------------------------------------------------
    def parapr(self, align="JUSTIFY", left=0, indent=0, prev=0, nxt=0, line=None, tab="0", keep_next=0):
        line = line or self.line_pct
        key = (align, left, indent, prev, nxt, line, tab, keep_next)
        if key in self.para:
            return self.para[key]
        pid = len(self.para_list)

        def margin(mult):
            return ('<hh:margin>'
                    f'<hc:intent value="{indent * mult}" unit="HWPUNIT"/><hc:left value="{left * mult}" unit="HWPUNIT"/>'
                    f'<hc:right value="0" unit="HWPUNIT"/><hc:prev value="{prev * mult}" unit="HWPUNIT"/>'
                    f'<hc:next value="{nxt * mult}" unit="HWPUNIT"/></hh:margin>'
                    f'<hh:lineSpacing type="PERCENT" value="{line}" unit="HWPUNIT"/>')
        x = (f'<hh:paraPr id="{pid}" tabPrIDRef="{tab}" condense="0" fontLineHeight="0" snapToGrid="0" '
             'suppressLineNumbers="0" checked="0" textDir="LTR">'
             f'<hh:align horizontal="{align}" vertical="BASELINE"/>'
             '<hh:heading type="NONE" idRef="0" level="0"/>'
             '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" widowOrphan="0" '
             f'keepWithNext="{keep_next}" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
             '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
             '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
             + margin(1) + '</hp:case><hp:default>' + margin(2) + '</hp:default></hp:switch>'
             '<hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" '
             'connect="0" ignoreMargin="0"/></hh:paraPr>')
        self.para[key] = str(pid)
        self.para_list.append(x)
        return str(pid)

    def tabpr(self, positions):
        positions = tuple(int(p) for p in positions)
        if positions in self.tab_sets:
            return str(self.tab_sets.index(positions) + 1)
        self.tab_sets.append(positions)
        return str(len(self.tab_sets))

    def header_xml(self, tab_positions=None):
        fonts = ""
        langs = ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
        for lg in langs:
            fonts += (f'<hh:fontface lang="{lg}" fontCnt="1"><hh:font id="0" face="{esc(self.font)}" type="TTF" isEmbedded="0">'
                      '<hh:typeInfo familyType="FCAT_GOTHIC" weight="5" proportion="0" contrast="0" strokeVariation="0" '
                      'armStyle="0" letterform="0" midline="0" xHeight="0"/></hh:font></hh:fontface>')
        tabs = '<hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/>'
        for i, ps in enumerate(self.tab_sets):
            tabs += f'<hh:tabPr id="{i + 1}" autoTabLeft="0" autoTabRight="0">'
            for pos in ps:
                tabs += ('<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
                         f'<hh:tabItem pos="{pos}" type="LEFT" leader="NONE" unit="HWPUNIT"/></hp:case>'
                         f'<hp:default><hh:tabItem pos="{pos * 2}" type="LEFT" leader="NONE"/></hp:default></hp:switch>')
            tabs += '</hh:tabPr>'
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            f'<hh:head {NS} version="1.4" secCnt="1">'
            '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>'
            '<hh:refList>'
            f'<hh:fontfaces itemCnt="7">{fonts}</hh:fontfaces>'
            f'<hh:borderFills itemCnt="{len(self.bf_list)}">{"".join(self.bf_list)}</hh:borderFills>'
            f'<hh:charProperties itemCnt="{len(self.char_list)}">{"".join(self.char_list)}</hh:charProperties>'
            f'<hh:tabProperties itemCnt="{len(self.tab_sets) + 1}">{tabs}</hh:tabProperties>'
            '<hh:numberings itemCnt="0"/>'
            f'<hh:paraProperties itemCnt="{len(self.para_list)}">{"".join(self.para_list)}</hh:paraProperties>'
            '<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" '
            'charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/></hh:styles>'
            '</hh:refList>'
            '<hh:compatibleDocument targetProgram="HWP201X"><hh:layoutCompatibility/></hh:compatibleDocument>'
            '<hh:docOption><hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/></hh:docOption>'
            '<hh:trackchageConfig flags="56"/>'
            '</hh:head>')


NS = ('xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
      'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
      'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
      'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
      'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
      'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" '
      'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"')


# ----------------------------------------------------------------------------
# 문단 모델 + 높이 추정
# ----------------------------------------------------------------------------
def text_width_em(s):
    w = 0.0
    for ch in s:
        o = ord(ch)
        if ch == " ":
            w += 0.30
        elif ch == "\t":
            w += 2.0
        elif o < 128:
            w += 0.40 if ch in "il.,:;'|!()[]" else (0.75 if ch in "mwMW@" else 0.56)
        elif 0x2000 <= o <= 0x206F:  # 일반 문장부호(–, ‘’ 등)
            w += 0.55
        else:
            w += 0.94 * 0.97            # 한글/CJK/기호(자간 -3% 반영)
    return w


class Para:
    """runs: [(text, charPrKey dict)], style: paraPr kwargs"""
    def __init__(self, runs, pkw=None, pt=None):
        self.runs = runs
        self.pkw = pkw or {}
        self.pt = pt

    def plain(self):
        return "".join(r[0] for r in self.runs if r[0] != "\t")


class Doc:
    def __init__(self, data, warnings):
        st = data.get("style", {})
        self.S = Styles(font=st.get("font", "맑은 고딕"), base_pt=float(st.get("bodyPt", 10)),
                        line_pct=int(st.get("lineSpacing", 150)), theme=st.get("theme", "teal"))
        self.t = self.S.t
        self.pt = self.S.base_pt
        self.warn = warnings
        self.body = []            # section xml chunks
        self.y = 0                # 현재 쪽에서의 추정 위치(HWPUNIT)
        self.page = 1
        self.tbl_id = 1000
        self.tab_positions = []

    # ---- text helpers ------------------------------------------------------
    def runs_from(self, text, base):
        """'[확인 필요]'는 빨간 굵은 글씨로, **굵게** 지원."""
        out = []
        parts = re.split(r"(\[확인 필요\]|\*\*[^*]+\*\*)", str(text))
        for p in parts:
            if not p:
                continue
            if p == CONFIRM:
                out.append((p, dict(base, bold=True, color="#D60000")))
            elif p.startswith("**") and p.endswith("**"):
                out.append((p[2:-2], dict(base, bold=True)))
            else:
                out.append((p, base))
        return out or [(" ", base)]

    def P(self, text, align="JUSTIFY", bold=False, color="#000000", pt=None, shade=None, left=0, indent=0,
          prev=0, nxt=0, tab="0", keep_next=0):
        base = dict(pt=pt or self.pt, bold=bold, color=color, shade=shade)
        return Para(self.runs_from(text, base), dict(align=align, left=left, indent=indent, prev=prev, nxt=nxt, tab=tab,
                                                     keep_next=keep_next), pt=pt or self.pt)

    def bullet(self, text, level=0, color="#000000", pt=None):
        pt = pt or self.pt
        em = int(pt * 100)
        sym = ["• ", "– ", "‣ ", "· "][min(level, 3)]
        hang = int(text_width_em(sym) * em)
        left = level * int(em * 0.9)
        return self.P(sym + str(text), left=left + hang, indent=-hang, color=color, pt=pt)

    def items_to_paras(self, items, level=0, pt=None):
        out = []
        for it in items or []:
            if isinstance(it, dict):
                if it.get("text"):
                    out.append(self.bullet(it["text"], level, pt=pt))
                out += self.items_to_paras(it.get("sub", []), level + 1, pt=pt)
            elif isinstance(it, list):
                out += self.items_to_paras(it, level + 1, pt=pt)
            else:
                s = str(it)
                if s.startswith("- ") or s.startswith("– "):
                    out.append(self.bullet(s[2:], level + 1, pt=pt))
                else:
                    out.append(self.bullet(s, level, pt=pt))
        return out

    def tag_para(self, tag):
        """도움자료 예시의 음영 표지(탐구/실행/성찰/동기유발 …)"""
        return Para([(" " + tag + " ", dict(pt=self.pt, bold=True, color="#FFFFFF", shade=self.t["tag"]))],
                    dict(align="LEFT", keep_next=1), pt=self.pt)

    # ---- height estimate ---------------------------------------------------
    def para_height(self, para, width):
        pt = para.pt or self.pt
        em = pt * 100
        avail = max(1000, width - 2 * CELL_MX - para.pkw.get("left", 0))
        txt = "".join(r[0] for r in para.runs)
        w = text_width_em(txt) * em
        first_extra = -para.pkw.get("indent", 0)          # 내어쓰기: 첫 줄은 왼쪽으로 hang 만큼 더 넓음
        lines = 1 if w <= avail + first_extra else 1 + math.ceil((w - avail - first_extra) / avail)
        return lines * em * self.S.line_pct / 100 + para.pkw.get("prev", 0) + para.pkw.get("next", 0)

    def cell_height(self, paras, width):
        return sum(self.para_height(p, width) for p in paras) + 2 * CELL_MY

    # ---- xml emit ----------------------------------------------------------
    def para_xml(self, para, page_break=False):
        S = self.S
        pid = S.parapr(**para.pkw)
        x = f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="{1 if page_break else 0}" columnBreak="0" merged="0">'
        for text, ck in para.runs:
            cid = S.charpr(**ck)
            if text == "\t":
                x += f'<hp:run charPrIDRef="{cid}"><hp:t><hp:tab width="4000" leader="0" type="1"/></hp:t></hp:run>'
            else:
                x += f'<hp:run charPrIDRef="{cid}"><hp:t>{esc(text)}</hp:t></hp:run>'
        return x + '</hp:p>'

    def cell_xml(self, c):
        paras = c["paras"] or [self.P(" ")]
        inner = "".join(self.para_xml(p) for p in paras)
        return (f'<hp:tc name="" header="{1 if c.get("header") else 0}" hasMargin="1" protect="0" editable="0" dirty="0" '
                f'borderFillIDRef="{c["bf"]}">'
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="{c.get("valign", "CENTER")}" '
                'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                f'{inner}</hp:subList>'
                f'<hp:cellAddr colAddr="{c["col"]}" rowAddr="{c["row"]}"/>'
                f'<hp:cellSpan colSpan="{c.get("cs", 1)}" rowSpan="{c.get("rs", 1)}"/>'
                f'<hp:cellSz width="{c["w"]}" height="{c["h"]}"/>'
                f'<hp:cellMargin left="{CELL_MX}" right="{CELL_MX}" top="{CELL_MY}" bottom="{CELL_MY}"/></hp:tc>')

    # ---- table builder -----------------------------------------------------
    def table(self, cols, rows, repeat_header=True, min_h=1500, keep_with_prev=False, avoid_orphan=True,
              outline=True, space_before=0, min_start=0.0, first_group_rows=None):
        """cols: 상대 너비 리스트. rows: [[cell,...],...]
        cell: dict(paras=[Para], cs=1, rs=1, kind='label'|'head'|'body'|'title', valign, header=bool)
        rowSpan으로 덮인 칸은 생략해서 넘긴다(왼→오 순서 유지)."""
        total = sum(cols)
        widths = [int(TABLE_W * c / total) for c in cols]
        widths[-1] = TABLE_W - sum(widths[:-1])
        nrow = len(rows)
        occupied = {}
        placed = []
        # 1) 좌표 배치
        for r, row in enumerate(rows):
            c = 0
            for cell in row:
                while (r, c) in occupied:
                    c += 1
                cs, rs = cell.get("cs", 1), cell.get("rs", 1)
                for dr in range(rs):
                    for dc in range(cs):
                        occupied[(r + dr, c + dc)] = True
                cell = dict(cell)
                cell["row"], cell["col"] = r, c
                cell["w"] = sum(widths[c:c + cs])
                placed.append(cell)
                c += cs
        # 2) 행 높이 추정
        row_h = [min_h] * nrow
        for cell in placed:
            if cell.get("rs", 1) == 1:
                h = self.cell_height(cell["paras"], cell["w"])
                row_h[cell["row"]] = max(row_h[cell["row"]], int(h))
        for cell in placed:
            rs = cell.get("rs", 1)
            if rs > 1:
                need = self.cell_height(cell["paras"], cell["w"])
                have = sum(row_h[cell["row"]:cell["row"] + rs])
                if need > have:
                    row_h[cell["row"] + rs - 1] += int(need - have)
        emit_h = [min_h] * nrow
        # 3) 테두리/배경
        thick = "0.4 mm"
        for cell in placed:
            kind = cell.get("kind", "body")
            fill = {"label": self.t["label"], "head": self.t["head"], "title": self.t["title"],
                    "titlesub": self.t["head"], "sub": self.t["label"]}.get(kind)
            r0, r1 = cell["row"], cell["row"] + cell.get("rs", 1) - 1
            if kind in ("title", "titlesub"):
                sides, wd = ("NONE",) * 4, None
            else:
                top = "!" + thick if (outline and r0 == 0) else "0.12 mm"
                bottom = "!" + thick if (outline and r1 == nrow - 1) else "0.12 mm"
                ls = "NONE" if cell["col"] == 0 else "SOLID"
                rsd = "NONE" if cell["col"] + cell.get("cs", 1) == len(cols) else "SOLID"
                top_t = cell.get("top", "SOLID")
                sides, wd = (ls, rsd, top_t, "SOLID"), ("0.12 mm", "0.12 mm", top, bottom)
            cell["bf"] = self.S.border_fill(fill, sides, widths=wd)
            # 저장 높이는 '최소 높이'만 기록(한글이 내용에 맞춰 늘림). 추정 높이를 그대로 쓰면
            # 추정이 클 때 한글에서 빈 공간이 생기므로 쓰지 않는다. 추정치는 쪽 넘김 판단에만 사용.
            cell["h"] = emit_h[r0] if r0 == r1 else sum(emit_h[r0:r1 + 1])
        # 4) 쪽 넘김 추정: 제목행 + 첫 본문행(최대 6줄)이 안 들어가면 표 앞에서 쪽 넘김
        page_break = False
        head_rows = [i for i in range(nrow) if all(c.get("header") for c in placed if c["row"] == i)]
        hh = sum(row_h[i] for i in head_rows)
        first_body = next((i for i in range(nrow) if i not in head_rows), None)
        if first_body is not None and first_group_rows:
            # 첫 묶음(예: '도입' 단계 전체)이 통째로 들어가야 이 쪽에서 시작(최대 쪽의 50%까지만 요구)
            grp = sum(row_h[first_body:first_body + first_group_rows])
            need = hh + min(grp, int(BODY_H * 0.5))
        else:
            need = hh + (min(row_h[first_body], int(6 * self.pt * 100 * self.S.line_pct / 100) + 2 * CELL_MY)
                         if first_body is not None else 0)
        remaining = BODY_H - self.y
        if avoid_orphan and self.y > 0 and (need > remaining or remaining < BODY_H * min_start):
            page_break = True
            self.page += 1
            self.y = 0
        # 쪽 위치 추적(셀 단위로 나뉘므로 연속 누적)
        self.y += space_before
        for h in row_h:
            self.y += h
            while self.y > BODY_H:
                self.y -= BODY_H
                self.page += 1
        self.tbl_id += 1
        body = ""
        for r in range(nrow):
            body += "<hp:tr>" + "".join(self.cell_xml(c) for c in placed if c["row"] == r) + "</hp:tr>"
        tbl = (f'<hp:tbl id="{self.tbl_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
               'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" '
               f'repeatHeader="{1 if repeat_header else 0}" rowCnt="{nrow}" colCnt="{len(cols)}" cellSpacing="0" '
               f'borderFillIDRef="{self.S.border_fill(None, ("NONE",) * 4)}" noAdjust="0">'
               f'<hp:sz width="{TABLE_W}" widthRelTo="ABSOLUTE" height="{sum(emit_h)}" heightRelTo="ABSOLUTE" protect="0"/>'
               '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
               'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
               '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
               f'<hp:inMargin left="{CELL_MX}" right="{CELL_MX}" top="{CELL_MY}" bottom="{CELL_MY}"/>'
               f'{body}</hp:tbl>')
        pid = self.S.parapr(align="LEFT", line=100)
        cid = self.S.charpr(pt=1)
        self.body.append(f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="{1 if page_break else 0}" '
                         f'columnBreak="0" merged="0"><hp:run charPrIDRef="{cid}">{tbl}<hp:t/></hp:run></hp:p>')
        return page_break

    def spacer(self, h_pt=4):
        pid = self.S.parapr(align="LEFT", line=100)
        cid = self.S.charpr(pt=h_pt)
        self.body.append(f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                         f'<hp:run charPrIDRef="{cid}"><hp:t/></hp:run></hp:p>')
        self.y += int(h_pt * 100)

    def new_page(self):
        pid = self.S.parapr(align="LEFT", line=100)
        cid = self.S.charpr(pt=1)
        self.body.append(f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="1" columnBreak="0" merged="0">'
                         f'<hp:run charPrIDRef="{cid}"><hp:t/></hp:run></hp:p>')
        self.page += 1
        self.y = 0

    def title_bar(self, text, pt=None, sub=False):
        """도움자료의 둥근 제목 띠 → 배경색 1칸 표(테두리 없음). sub=True면 옅은 색(과정안 띠)."""
        pt = pt or self.pt + 4
        para = self.P(text, align="CENTER", bold=True, color="#FFFFFF", pt=pt)
        cell = dict(paras=[para], kind="titlesub" if sub else "title")
        self.table([1], [[cell]], repeat_header=False, min_h=int(pt * 100 * 2.1), outline=False)

    # ---- section -------------------------------------------------------------
    def section_xml(self):
        sec = ('<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" '
               'tabStopUnit="HWPUNIT" outlineShapeIDRef="0" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">'
               '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
               '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
               '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" '
               'fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
               '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
               f'<hp:pagePr landscape="WIDELY" width="{PAGE_W}" height="{PAGE_H}" gutterType="LEFT_ONLY">'
               f'<hp:margin header="{MARGIN["header"]}" footer="{MARGIN["footer"]}" gutter="0" left="{MARGIN["left"]}" '
               f'right="{MARGIN["right"]}" top="{MARGIN["top"]}" bottom="{MARGIN["bottom"]}"/></hp:pagePr>'
               '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
               '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
               '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
               '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
               '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
               '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
               '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
               '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
               '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">'
               '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
               '<hp:pageBorderFill type="EVEN" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">'
               '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
               '<hp:pageBorderFill type="ODD" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" fillArea="PAPER">'
               '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
               '</hp:secPr>')
        pid = self.S.parapr(align="LEFT", line=100)
        cid = self.S.charpr(pt=1)
        first = (f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                 f'<hp:run charPrIDRef="{cid}">{sec}<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
                 'sameSz="1" sameGap="0"/></hp:ctrl></hp:run></hp:p>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                f'<hs:sec {NS}>' + first + "".join(self.body) + '</hs:sec>')


# ----------------------------------------------------------------------------
# 양식별 내용 조립
# ----------------------------------------------------------------------------
def need(v, name, warnings, ctx=""):
    """비어 있으면 [확인 필요]로 대체"""
    empty = v is None or (isinstance(v, (list, dict, str)) and len(v) == 0)
    if empty:
        warnings.append(f"{ctx}{name}: 비어 있어 '{CONFIRM}'로 표시함")
        return CONFIRM
    return v


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def label_cell(d, text, rs=1, cs=1, sub=None):
    paras = [d.P(text, align="CENTER", bold=True)]
    if sub:
        paras.append(d.P(sub, align="CENTER", bold=True))
    return dict(paras=paras, kind="label", rs=rs, cs=cs)


def head_cell(d, text, cs=1, rs=1, header=True):
    lines = text.split("\n")
    return dict(paras=[d.P(t, align="CENTER", bold=True, color="#FFFFFF") for t in lines], kind="head",
                cs=cs, rs=rs, header=header)


def body_cell(d, paras, cs=1, rs=1, valign="CENTER", align=None):
    return dict(paras=paras, kind="body", cs=cs, rs=rs, valign=valign)


def text_paras(d, v, bullet=True, align="JUSTIFY"):
    out = []
    for x in as_list(v):
        if isinstance(x, (dict, list)):
            out += d.items_to_paras([x])
        elif bullet:
            out.append(d.bullet(x))
        else:
            out.append(d.P(x, align=align))
    return out or [d.P(CONFIRM)]


def checkbox_paras(d, options, checked, other=None, per_line=4, cell_w=None):
    """■/□ 체크박스. 열마다 가장 긴 항목 너비로 탭 위치를 계산해 도움자료처럼 4열로 맞춘다.
    한 줄에 다 안 들어가면 열 수를 줄인다."""
    checked = {c.replace(" ", "") for c in (checked or [])}
    labels = []
    for o in options:
        mark = "■" if o.replace(" ", "") in checked else "□"
        labels.append(f"{mark} {o}")
    if other is not None:
        labels.append(("■" if other else "□") + f" 기타({other or '            '})")
    em = d.pt * 100
    cell_w = cell_w or (TABLE_W - int(TABLE_W * 72 / 535))
    avail = cell_w - 2 * CELL_MX - 200
    gap = em * 1.2
    for n in range(per_line, 0, -1):
        cols = [labels[i::n] for i in range(n)]
        widths = [max(text_width_em(x) * em for x in c) for c in cols if c]
        if sum(widths) + gap * (len(widths) - 1) <= avail or n == 1:
            break
    # 남는 폭은 열 사이에 고르게 배분
    extra = (avail - sum(widths)) / max(1, len(widths) - 1) if len(widths) > 1 else 0
    pos, x = [], 0
    for w in widths[:-1]:
        x += w + max(gap, extra)
        pos.append(int(x))
    tab = d.S.tabpr(pos) if pos else "0"
    paras = []
    for r in range(0, len(labels), n):
        runs = []
        for i, lab in enumerate(labels[r:r + n]):
            if i:
                runs.append(("\t", dict(pt=d.pt)))
            runs.append((lab, dict(pt=d.pt)))
        paras.append(Para(runs, dict(align="LEFT", tab=tab), pt=d.pt))
    return paras


QCAT = [("factual", "사실적 질문"), ("conceptual", "개념적 질문"), ("debatable", "논쟁적 질문")]


def question_paras(d, q):
    out = []
    if isinstance(q, list):
        return text_paras(d, q)
    for key, name in QCAT:
        items = as_list(q.get(key))
        if not items:
            continue
        out.append(d.P(name, bold=False, color=d.t["accent"]))
        out += d.items_to_paras(items)
    return out or [d.P(CONFIRM)]


def content_elements_cells(d, ce, warnings, rs=1):
    cells = []
    for key, name in [("knowledge", "지식·이해"), ("skills", "과정·기능"), ("values", "가치·태도")]:
        v = need(ce.get(key), f"내용 요소({name})", warnings)
        cells.append(body_cell(d, d.items_to_paras(as_list(v)), valign="TOP"))
    return cells


def standards_paras(d, stds):
    out = []
    for s in stds:
        out.append(d.P(f"[{s['code']}] {s['text']}"))
    return out


# 과정 블록 → 단락 목록(활동 칸) + 단락 목록(주안점 칸)
def block_paras(d, b):
    act = []
    if b.get("tag"):
        act.append(d.tag_para(b["tag"]))
    if b.get("title"):
        act.append(d.P(b["title"], bold=True, color=d.t["accent"], keep_next=1))
    if b.get("question"):
        act.append(d.P(b["question"], color=d.t["accent"], keep_next=1))
    act += d.items_to_paras(b.get("items", []))
    note = []
    for x in as_list(b.get("notes")):
        note += d.items_to_paras([x])
    for x in as_list(b.get("materials")):
        note.append(d.P("㉶ " + str(x), left=int(d.pt * 100 * 1.2), indent=-int(d.pt * 100 * 1.2)))
    for x in as_list(b.get("cautions")):
        note.append(d.P("※ " + str(x), left=int(d.pt * 100 * 1.2), indent=-int(d.pt * 100 * 1.2)))
    return act, note


def split_block(d, b, width, max_h):
    """한 블록이 너무 길면 items 경계에서 여러 행으로 나눔(쪽 넘김이 어색하지 않도록)."""
    act, note = block_paras(d, b)
    if d.cell_height(act, width) <= max_h or len(b.get("items", [])) < 2:
        return [(act, note)]
    chunks, cur = [], []
    head = []
    if b.get("tag"):
        head.append(d.tag_para(b["tag"]))
    if b.get("title"):
        head.append(d.P(b["title"], bold=True, color=d.t["accent"]))
    if b.get("question"):
        head.append(d.P(b["question"], color=d.t["accent"]))
    cur = list(head)
    for it in b.get("items", []):
        ps = d.items_to_paras([it])
        if d.cell_height(cur + ps, width) > max_h and len(cur) > len(head):
            chunks.append(cur)
            cur = []
        cur += ps
    if cur:
        chunks.append(cur)
    out = [(chunks[0], note)]
    for c in chunks[1:]:
        out.append((c, []))
    return out


def render_lesson(d, L, warnings, ctx=""):
    t = d.t
    meta = L.get("meta", {})
    d.title_bar(L.get("title", "깊이있는 수업을 위한 차시별 교수·학습 지도안"))
    d.spacer(5)
    # 열 비율: 도움자료 40쪽(라벨 72 | 155 | 155 | 52 | 101)
    cols = [72, 155, 155, 52, 101]
    per = meta.get("period") or {}
    cur, tot = per.get("current"), per.get("total")
    per_txt = f"{cur if cur else '   '}차시/ {tot if tot else '   '}차시"
    if not (cur and tot):
        warnings.append(f"{ctx}차시(n/m): 비어 있음 → 빈칸으로 둠(교사 기입)")
    rows = [
        [label_cell(d, "학습 주제"), body_cell(d, [d.P(need(meta.get("topic"), "학습 주제", warnings, ctx), align="CENTER")], cs=2),
         label_cell(d, "과 목"), body_cell(d, [d.P(need(meta.get("subject"), "과목", warnings, ctx), align="CENTER")])],
        [label_cell(d, "영 역"), body_cell(d, [d.P(need(meta.get("domain"), "영역", warnings, ctx), align="CENTER")], cs=2),
         label_cell(d, "차 시"), body_cell(d, [d.P(per_txt, align="CENTER")])],
        [label_cell(d, "핵심", sub="아이디어"),
         body_cell(d, text_paras(d, need(L.get("coreIdeas"), "핵심 아이디어", warnings, ctx)), cs=4)],
        [head_cell(d, "범주", header=False), head_cell(d, "지식·이해", header=False), head_cell(d, "과정·기능", header=False),
         head_cell(d, "가치·태도", cs=2, header=False)],
    ]
    ce_cells = content_elements_cells(d, L.get("contentElements", {}), warnings)
    ce_cells[2]["cs"] = 2
    rows.append([label_cell(d, "내용 요소")] + ce_cells)
    stds = L.get("standards") or []
    rows.append([label_cell(d, "성취기준"),
                 body_cell(d, standards_paras(d, stds) or [d.P(CONFIRM)], cs=4)])
    m = L.get("methods", {})
    rows.append([label_cell(d, "교수·학습", sub="방법"),
                 body_cell(d, checkbox_paras(d, METHODS, m.get("checked"), other=m.get("other", "")), cs=4)])
    rows.append([label_cell(d, "수업 의도"),
                 body_cell(d, text_paras(d, need(L.get("intent"), "수업 의도", warnings, ctx), bullet=False), cs=4)])
    rows.append([label_cell(d, "탐구 질문"),
                 body_cell(d, question_paras(d, need(L.get("inquiryQuestions"), "탐구 질문", warnings, ctx)), cs=4)])
    rows.append([label_cell(d, "평가 과제"),
                 body_cell(d, text_paras(d, need(L.get("assessmentTask"), "평가 과제", warnings, ctx)), cs=4)])
    d.table(cols, rows, repeat_header=False, min_h=1700)

    # ---- 과정 표(단계 | 활동 | 주안점), 제목행 반복 ----
    pcols = [70, 330, 135]
    total = sum(pcols)
    w_act = int(TABLE_W * pcols[1] / total)
    max_row_h = int(BODY_H * float(L.get("style", {}).get("maxRowFraction", 0.42)))
    prows = [[head_cell(d, "단계"), head_cell(d, "탐구·실행·성찰을 위한 수업 활동"),
              head_cell(d, "수업·평가\n연계의 주안점")]]
    stages = L.get("process") or []
    first_group = None
    if not stages:
        warnings.append(f"{ctx}수업 과정(process): 비어 있음")
    for stg in stages:
        name = stg.get("stage", "")
        spaced = " ".join(name) if len(name) == 2 else name
        segs = []
        for b in stg.get("blocks", []):
            segs += split_block(d, b, w_act, max_row_h)
        if not segs:
            segs = [([d.P(CONFIRM)], [])]
        if first_group is None:
            first_group = len(segs)
        lab = [d.P(spaced, align="CENTER", bold=True)]
        if stg.get("minutes"):
            lab.append(d.P(f"({stg['minutes']}')", align="CENTER"))
        for i, (act, note) in enumerate(segs):
            row = []
            if i == 0:
                row.append(dict(paras=lab, kind="label", rs=len(segs)))
            c_act = body_cell(d, act, valign="TOP")
            c_note = body_cell(d, note or [d.P(" ")], valign="TOP")
            if i > 0:
                c_act["top"] = "DOT"
                c_note["top"] = "DOT"
            row += [c_act, c_note]
            prows.append(row)
    d.table(pcols, prows, repeat_header=True, min_h=1500, first_group_rows=first_group,
            min_start=float(L.get("style", {}).get("processNewPageThreshold", 0.0)))


def render_unit(d, U, warnings):
    meta = U.get("meta", {})
    d.title_bar(U.get("title", "깊이있는 단원 수업 설계안"))
    d.spacer(5)
    # 도움자료 38쪽: 72 | 155 | 95 | 60 | 45 | 53 | 55
    cols = [72, 155, 95, 60, 45, 53, 55]
    rows = [
        [label_cell(d, "과 목"), body_cell(d, [d.P(need(meta.get("subject"), "과목", warnings), align="CENTER")], cs=2),
         label_cell(d, "대 상"), body_cell(d, [d.P(need(meta.get("target"), "대상", warnings), align="CENTER")]),
         label_cell(d, "총 차시"), body_cell(d, [d.P(str(need(meta.get("totalPeriods"), "총 차시", warnings)), align="CENTER")])],
        [label_cell(d, "주 제"), body_cell(d, [d.P(need(meta.get("topic"), "주제", warnings), align="CENTER")], cs=2),
         label_cell(d, "영 역"), body_cell(d, [d.P(need(meta.get("domain"), "영역", warnings), align="CENTER")], cs=3)],
        [label_cell(d, "핵심", sub="아이디어"),
         body_cell(d, text_paras(d, need(U.get("coreIdeas"), "핵심 아이디어", warnings)), cs=6)],
        [head_cell(d, "범주", header=False), head_cell(d, "지식·이해", header=False),
         head_cell(d, "과정·기능", cs=2, header=False), head_cell(d, "가치·태도", cs=3, header=False)],
    ]
    ce = content_elements_cells(d, U.get("contentElements", {}), warnings)
    ce[1]["cs"] = 2
    ce[2]["cs"] = 3
    rows.append([label_cell(d, "내용 요소")] + ce)
    rows.append([label_cell(d, "성취기준"),
                 body_cell(d, standards_paras(d, U.get("standards") or []) or [d.P(CONFIRM)], cs=6)])
    rows.append([label_cell(d, "삶의 맥락"),
                 body_cell(d, checkbox_paras(d, LIFE_CONTEXTS, U.get("lifeContexts")), cs=6)])
    rows.append([label_cell(d, "탐구 질문"),
                 body_cell(d, question_paras(d, need(U.get("inquiryQuestions"), "탐구 질문", warnings)), cs=6)])
    rows.append([label_cell(d, "평가 과제"),
                 body_cell(d, text_paras(d, need(U.get("assessmentTask"), "평가 과제", warnings)), cs=6)])
    d.table(cols, rows, repeat_header=False, min_h=1700)

    # ---- 수행 수준(A~E) ----
    pl = U.get("performanceLevels")
    levels = ["A", "B", "C", "D", "E"]
    if pl:
        els = pl if isinstance(pl, list) else [pl]
        has_el = any(e.get("element") for e in els)
        n = len(els) + 1
        if has_el:
            cols2 = [72, 80] + [76.6] * 5
            rows2 = [[label_cell(d, "수행 수준", rs=n), head_cell(d, "평가 요소", header=False)]
                     + [head_cell(d, L, header=False) for L in levels]]
            for e in els:
                rows2.append([dict(paras=[d.P(e.get("element", ""), align="CENTER", bold=True)], kind="sub")]
                             + [body_cell(d, text_paras(d, e.get(L, ""), bullet=False, align="LEFT"), valign="TOP")
                                for L in levels])
        else:
            cols2 = [72] + [92.6] * 5
            rows2 = [[label_cell(d, "수행 수준", rs=n)] + [head_cell(d, L, header=False) for L in levels]]
            for e in els:
                rows2.append([body_cell(d, text_paras(d, e.get(L, ""), bullet=False, align="LEFT"), valign="TOP")
                              for L in levels])
        d.table(cols2, rows2, repeat_header=False, min_h=1500)
    else:
        warnings.append("수행 수준(performanceLevels): 비어 있음")

    # ---- 깊이있는 수업 과정안 ----
    d.spacer(8)
    if BODY_H - d.y < 18000:
        d.new_page()
    d.title_bar(U.get("courseTitle", "깊이있는 수업 과정안"), pt=d.pt + 2, sub=True)
    d.spacer(4)
    ccols = [62, 268, 100, 105]
    w_act = int(TABLE_W * ccols[1] / sum(ccols))
    max_row_h = int(BODY_H * 0.42)
    rows3 = [
        [head_cell(d, "차시", rs=2), head_cell(d, "탐구-실행-성찰의 과정", cs=3)],
        [head_cell(d, "탐구·실행·성찰을 위한 수업 활동"), head_cell(d, "수업방법"), head_cell(d, "수업·평가\n연계의 주안점")],
    ]
    course = U.get("course") or []
    if not course:
        warnings.append("과정안(course): 비어 있음")
    for c in course:
        segs = []
        for b in c.get("blocks", []):
            segs += [s[0] for s in split_block(d, b, w_act, max_row_h)]
        if not segs:
            segs = [[d.P(CONFIRM)]]
        for i, act in enumerate(segs):
            row = []
            if i == 0:
                row.append(dict(paras=[d.P(str(c.get("periods", "")), align="CENTER", bold=True)], kind="label",
                                rs=len(segs)))
            ca = body_cell(d, act, valign="TOP")
            if i > 0:
                ca["top"] = "DOT"
            row.append(ca)
            if i == 0:
                row.append(body_cell(d, text_paras(d, c.get("methods", ""), bullet=False, align="CENTER"),
                                     rs=len(segs)))
                row.append(body_cell(d, text_paras(d, c.get("focus", "")), rs=len(segs), valign="TOP"))
            rows3.append(row)
    d.table(ccols, rows3, repeat_header=True, min_h=1500,
            first_group_rows=2, min_start=float(U.get("style", {}).get("processNewPageThreshold", 0.0)))


# ----------------------------------------------------------------------------
# 검증
# ----------------------------------------------------------------------------
def walk_strings(o, path=""):
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk_strings(v, f"{path}[{i}]")


def validate(data, allowed, allow_dialog, errors, warnings):
    mode = data.get("mode", "lesson")
    if mode not in ("lesson", "unit"):
        errors.append(f"mode는 lesson|unit 이어야 함: {mode}")
    docs = [data] + list(data.get("lessons", []) if mode == "unit" else [])
    for i, dd in enumerate(docs):
        stds = dd.get("standards") or []
        if not stds:
            errors.append(f"[{i}] standards(성취기준)가 비어 있음 — cu2022 검색 결과에서 선택해 넣을 것")
        for s in stds:
            if not isinstance(s, dict) or not s.get("code") or not s.get("text"):
                errors.append(f"[{i}] standards 항목은 {{code, text}} 형식이어야 함: {s}")
            elif allowed is not None and s["code"] not in allowed:
                errors.append(f"[{i}] 성취기준 코드 {s['code']} 가 허용 목록(--allowed-codes)에 없음")
    # 본문 속 성취기준 코드도 검사
    if allowed is not None:
        for path, s in walk_strings(data):
            if path.startswith("sources") or ".sources" in path:
                continue
            for code in CODE_RE.findall(s):
                if code not in allowed:
                    errors.append(f"{path}: 허용되지 않은 성취기준 코드 '{code}'")
    if not allow_dialog:
        for path, s in walk_strings(data):
            if DIALOG_RE.search(s):
                errors.append(f"{path}: 교사-학생 문답(대화 스크립트) 형태 감지 → 활동 중심 개조식으로 고칠 것: {s[:40]}…")
    for path, s in walk_strings(data):
        if CONFIRM in s:
            warnings.append(f"{path}: '{CONFIRM}' 표시가 남아 있음")


# ----------------------------------------------------------------------------
# 패키징 (한컴 호환 zip: mimetype 첫 항목·비압축)
# ----------------------------------------------------------------------------
def package(path, header, section, preview, title):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hv:HCFVersion '
               'xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR" major="5" '
               'minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.4" application="Hancom Office Hangul" '
               'appVersion="12, 0, 0, 0 WIN32LEWindows_10"/>')
    container = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><ocf:container '
                 'xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf">'
                 '<ocf:rootfiles><ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
                 '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
                 '<ocf:rootfile full-path="META-INF/container.rdf" media-type="application/rdf+xml"/>'
                 '</ocf:rootfiles></ocf:container>')
    rdf = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" rdf:resource="Contents/header.xml"/></rdf:Description>'
           '<rdf:Description rdf:about="Contents/header.xml"><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#HeaderFile"/></rdf:Description>'
           '<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" rdf:resource="Contents/section0.xml"/></rdf:Description>'
           '<rdf:Description rdf:about="Contents/section0.xml"><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#SectionFile"/></rdf:Description>'
           '<rdf:Description rdf:about=""><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#Document"/></rdf:Description>'
           '</rdf:RDF>')
    manifest = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
    hpf = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
           f'<opf:package {NS} version="" unique-identifier="" id="">'
           f'<opf:metadata><opf:title>{esc(title)}</opf:title><opf:language>ko</opf:language>'
           '<opf:meta name="creator" content="text">deep-lessonplan</opf:meta>'
           f'<opf:meta name="CreatedDate" content="text">{now}</opf:meta>'
           f'<opf:meta name="ModifiedDate" content="text">{now}</opf:meta></opf:metadata>'
           '<opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
           '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
           '<opf:item id="settings" href="settings.xml" media-type="application/xml"/></opf:manifest>'
           '<opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine>'
           '</opf:package>')
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><ha:HWPApplicationSetting '
                'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
                '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in [("version.xml", version), ("Contents/header.xml", header),
                           ("Contents/section0.xml", section), ("Preview/PrvText.txt", preview),
                           ("settings.xml", settings), ("META-INF/container.rdf", rdf),
                           ("Contents/content.hpf", hpf), ("META-INF/container.xml", container),
                           ("META-INF/manifest.xml", manifest)]:
            z.writestr(name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


def build(data, out, allowed=None, allow_dialog=False, strict=False, check_only=False):
    errors, warnings = [], []
    validate(data, allowed, allow_dialog, errors, warnings)
    if errors:
        return errors, warnings, None
    base_style = dict(data.get("style", {}))
    d, lw = layout(data, base_style)
    fit_note = None
    pt0 = float(base_style.get("bodyPt", 10))
    ls0 = int(base_style.get("lineSpacing", 150))
    # 자동 맞춤: 마지막 쪽이 30% 미만으로 조금 넘치면 줄 간격·글자 크기를 단계적으로 줄여 한 쪽을 줄여 본다.
    if base_style.get("autoFit", True) and d.page > 1 and d.y < BODY_H * 0.30:
        cands = []
        for pt, ls in [(pt0, ls0 - 5), (pt0, ls0 - 10), (pt0 - 0.5, ls0 - 10), (pt0 - 0.5, ls0 - 15), (pt0 - 1, ls0 - 15)]:
            if pt >= 9 and ls >= 125 and (pt, ls) != (pt0, ls0):
                cands.append((pt, ls))
        for pt, ls in cands:
            st = dict(base_style, bodyPt=pt, lineSpacing=ls)
            d2, lw2 = layout(data, st)
            if d2.page < d.page:
                fit_note = f"자동 맞춤: 본문 {pt0}pt/{ls0}% → {pt}pt/{ls}% (예상 {d.page}쪽 → {d2.page}쪽)"
                d, lw = d2, lw2
                break
    warnings.extend(lw)
    if fit_note:
        warnings.append(fit_note)
    if strict and any(CONFIRM in w for w in warnings):
        errors.append("--strict: '[확인 필요]' 칸이 남아 있음")
        return errors, warnings, None
    section = d.section_xml()
    header = d.S.header_xml()
    preview = "\n".join(s for _, s in walk_strings(data) if not _.startswith("sources"))[:1000]
    title = data.get("meta", {}).get("topic") or data.get("title") or "lessonplan"
    if not check_only:
        package(out, header, section, preview, title)
    return errors, warnings, dict(estimated_pages=d.page, last_page_fill=round(d.y / BODY_H, 2),
                                  bodyPt=d.S.base_pt, lineSpacing=d.S.line_pct)


def layout(data, style):
    """주어진 style로 문서를 배치(추정)한다."""
    data = dict(data)
    data["style"] = style
    w = []
    d = Doc(data, w)
    content_w = TABLE_W - int(TABLE_W * 72 / 535) - 2 * CELL_MX
    d.tab_positions = [int(content_w * k / 4) for k in (1, 2, 3)]
    if data.get("mode", "lesson") == "unit":
        render_unit(d, data, w)
        for i, L in enumerate(data.get("lessons", [])):
            L = dict(L)
            L["style"] = style
            d.new_page()
            render_lesson(d, L, w, ctx=f"lessons[{i}] ")
    else:
        render_lesson(d, data, w)
    return d, w


def main():
    ap = argparse.ArgumentParser(description="깊이있는 수업 지도안 JSON → HWPX")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--allowed-codes", help="쉼표로 구분한 허용 성취기준 코드(cu2022 검색 결과에서 교사가 고른 것)")
    ap.add_argument("--allow-dialog", action="store_true", help="문답 검사 끄기(비권장)")
    ap.add_argument("--strict", action="store_true", help="[확인 필요]가 남아 있으면 실패")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--dump-text", action="store_true",
                    help="본문 텍스트만 출력(cu2022 lesson_pack_validate의 draft로 넘길 때)")
    a = ap.parse_args()
    data = json.load(open(a.input, encoding="utf-8"))
    if a.dump_text:
        print("\n".join(v for k, v in walk_strings(data) if not k.startswith("sources") and not k.startswith("style")))
        return
    allowed = None
    if a.allowed_codes:
        allowed = {c.strip() for c in a.allowed_codes.split(",") if c.strip()}
    out = a.output or re.sub(r"\.json$", "", a.input) + ".hwpx"
    errors, warnings, info = build(data, out, allowed, a.allow_dialog, a.strict, a.check_only)
    for w in warnings:
        print("WARN:", w, file=sys.stderr)
    if errors:
        for e in errors:
            print("ERROR:", e, file=sys.stderr)
        sys.exit(2)
    print(json.dumps(dict(output=None if a.check_only else out, **info), ensure_ascii=False))


if __name__ == "__main__":
    main()
