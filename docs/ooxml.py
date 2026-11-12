"""Tiny OOXML writer: enough of WordprocessingML for headings, paragraphs with **bold** / `code`
runs, bullet and numbered lists, styled tables, callouts, code blocks, images and a footer."""
from __future__ import annotations

import re
import struct
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

NAVY, BLUE, GREY, ALT = "1F4E79", "2E74B5", "595959", "F4F7FA"
CONTENT_W = 9360          # 6.5in in DXA (US Letter, 1in margins)
LANDSCAPE_W = 14112       # 9.8in (landscape pages use 0.6in margins)
EMU_PER_PX = 9525         # 96 dpi


def _x(s: str) -> str:
    return escape(s, {'"': "&quot;"})


def _rpr(bold=False, italic=False, color=None, size=None, font=None, caps=False, shade=None) -> str:
    out = []
    if font:
        out.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
    if bold:
        out.append("<w:b/><w:bCs/>")
    if italic:
        out.append("<w:i/><w:iCs/>")
    if caps:
        out.append("<w:caps/>")
    if color:
        out.append(f'<w:color w:val="{color}"/>')
    if size:
        out.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if shade:
        out.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    return f"<w:rPr>{''.join(out)}</w:rPr>" if out else ""


def _run(text, **kw) -> str:
    return f'<w:r>{_rpr(**kw)}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'


_TOKEN = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def runs(text: str, *, size=None, color=None, bold=False, italic=False) -> str:
    """Inline markup: **bold**, `code`."""
    out = []
    for part in _TOKEN.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append(_run(part[2:-2], bold=True, size=size, color=color, italic=italic))
        elif part.startswith("`") and part.endswith("`"):
            out.append(_run(part[1:-1], font="Consolas", size=(size or 22) - 3, color=NAVY, bold=bold))
        else:
            out.append(_run(part, size=size, color=color, bold=bold, italic=italic))
    return "".join(out)


def _ppr(style=None, align=None, before=None, after=None, keep_next=False, num=None, shade=None,
         ind_left=None, border_left=None, keep_lines=False) -> str:
    out = []
    if style:
        out.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        out.append("<w:keepNext/>")
    if keep_lines:
        out.append("<w:keepLines/>")
    if num:
        out.append(f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>')
    if border_left:
        out.append(f'<w:pBdr><w:left w:val="single" w:sz="{border_left[1]}" w:space="8" w:color="{border_left[0]}"/></w:pBdr>')
    if shade:
        out.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    if before is not None or after is not None:
        b = f' w:before="{before}"' if before is not None else ""
        a = f' w:after="{after}"' if after is not None else ""
        out.append(f"<w:spacing{b}{a}/>")
    if ind_left is not None:
        out.append(f'<w:ind w:left="{ind_left}"/>')
    if align:
        out.append(f'<w:jc w:val="{align}"/>')
    return f"<w:pPr>{''.join(out)}</w:pPr>" if out else ""


class Doc:
    def __init__(self):
        self.body: list[str] = []
        self.images: list[tuple[str, str, bytes]] = []   # rId, name, data
        self.nums: list[tuple[int, int]] = []            # (numId, abstractNumId)
        self._pic = 0
        self.orient = "portrait"
        self._first_section = True

    @property
    def width(self) -> int:
        return CONTENT_W if self.orient == "portrait" else LANDSCAPE_W

    def _sect(self, orient: str) -> str:
        size = ('<w:pgSz w:w="12240" w:h="15840"/>' if orient == "portrait"
                else '<w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>')
        title = "<w:titlePg/>" if self._first_section else ""
        m = 1440 if orient == "portrait" else 864
        return ('<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>' + size +
                f'<w:pgMar w:top="{m}" w:right="{m}" w:bottom="{m}" w:left="{m}" w:header="500" w:footer="400" w:gutter="0"/>'
                + title + '</w:sectPr>')

    def switch(self, orient: str):
        """End the current section and start a new page in `orient` (portrait | landscape)."""
        if orient == self.orient:
            return
        self.body.append(f"<w:p><w:pPr>{self._sect(self.orient)}</w:pPr></w:p>")
        self._first_section = False
        self.orient = orient

    # ---- basic blocks ----
    def para(self, text="", *, style=None, align=None, before=None, after=None, keep_next=False,
             size=None, color=None, bold=False, italic=False, raw_runs=None):
        r = raw_runs if raw_runs is not None else runs(text, size=size, color=color, bold=bold, italic=italic)
        self.body.append(f"<w:p>{_ppr(style, align, before, after, keep_next)}{r}</w:p>")

    def h1(self, t):
        self.body.append(f'<w:p>{_ppr("Heading1", keep_next=True)}{runs(t)}</w:p>')

    def h2(self, t):
        self.body.append(f'<w:p>{_ppr("Heading2", keep_next=True)}{runs(t)}</w:p>')

    def h3(self, t):
        self.body.append(f'<w:p>{_ppr("Heading3", keep_next=True)}{runs(t)}</w:p>')

    def page_break(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def _new_num(self, abstract: int) -> int:
        nid = len(self.nums) + 2   # numId 1 is the shared bullet list
        self.nums.append((nid, abstract))
        return nid

    def bullets(self, items, level=0):
        nid = 1  # shared bullet instance
        for it in items:
            if isinstance(it, (list, tuple)):
                self.bullets(it, level + 1)
                continue
            self.body.append(f'<w:p>{_ppr("ListParagraph", num=(nid, level), after=60)}{runs(it)}</w:p>')

    def numbered(self, items):
        nid = self._new_num(1)
        for it in items:
            self.body.append(f'<w:p>{_ppr("ListParagraph", num=(nid, 0), after=60)}{runs(it)}</w:p>')

    def code(self, lines, size=16):
        for i, ln in enumerate(lines):
            before = 80 if i == 0 else 0
            after = 80 if i == len(lines) - 1 else 0
            self.body.append(
                f'<w:p>{_ppr("Code", before=before, after=after, shade=ALT, keep_lines=True, keep_next=i < 4)}'
                f'{_run(ln if ln else " ", font="Consolas", size=size)}</w:p>')

    # ---- tables ----
    def table(self, headers, rows, widths, *, size=19, first_bold=False, header=True, keep=True):
        total = self.width
        assert sum(widths) == total, (sum(widths), total, headers)
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        b = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="BFC9D4"/>'
                    for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="{total}" w:type="dxa"/>'
               f'<w:tblBorders>{b}</w:tblBorders><w:tblLayout w:type="fixed"/>'
               f'<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
               f'<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
               f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>']

        n_rows = len(rows) + (1 if header else 0)
        row_no = [0]

        def cell(content, w, fill=None, hdr=False, bold=False):
            kn = keep and row_no[0] < n_rows - 1
            shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
            paras = content if isinstance(content, (list, tuple)) else [content]
            ps = "".join(
                f'<w:p>{_ppr(after=40, before=0, keep_next=kn)}'
                f'{runs(str(p), size=size, color="FFFFFF" if hdr else None, bold=hdr or bold)}</w:p>'
                for p in paras)
            return f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}</w:tcPr>{ps}</w:tc>'

        if header:
            out.append('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>'
                       + "".join(cell(h, w, NAVY, hdr=True) for h, w in zip(headers, widths)) + "</w:tr>")
            row_no[0] += 1
        for i, row in enumerate(rows):
            fill = ALT if i % 2 == 0 else None
            out.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>' + "".join(
                cell(c, w, fill, bold=(first_bold and j == 0)) for j, (c, w) in enumerate(zip(row, widths))) + "</w:tr>")
            row_no[0] += 1
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self.para("", after=60)

    def callout(self, title, lines, *, fill="EAF1F8", border=NAVY):
        """One-cell shaded box with a thick left rule."""
        bd = (f'<w:top w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/>'
              f'<w:left w:val="single" w:sz="24" w:space="0" w:color="{border}"/>')
        ps = []
        if title:
            ps.append(f'<w:p>{_ppr(after=60, keep_next=True)}{runs(title, bold=True, color=border)}</w:p>')
        for ln in lines:
            ps.append(f'<w:p>{_ppr(after=60)}{runs(ln)}</w:p>')
        cw = self.width
        self.body.append(
            f'<w:tbl><w:tblPr><w:tblW w:w="{cw}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            f'<w:tblCellMar><w:top w:w="120" w:type="dxa"/><w:left w:w="200" w:type="dxa"/>'
            f'<w:bottom w:w="80" w:type="dxa"/><w:right w:w="200" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            f'<w:tblGrid><w:gridCol w:w="{cw}"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr><w:tc><w:tcPr>'
            f'<w:tcW w:w="{cw}" w:type="dxa"/><w:tcBorders>{bd}</w:tcBorders>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/></w:tcPr>{"".join(ps)}</w:tc></w:tr></w:tbl>')
        self.para("", after=60)

    # ---- images ----
    def image(self, path, caption, *, max_w_in=None, max_h_in=None, scale=2):
        if max_w_in is None:
            max_w_in = 6.5 if self.orient == "portrait" else 9.8
        if max_h_in is None:
            max_h_in = 8.4 if self.orient == "portrait" else 6.6
        data = open(path, "rb").read()
        w_px, h_px = struct.unpack(">II", data[16:24])
        w_px, h_px = w_px / scale, h_px / scale          # rendered at 2x for sharpness
        cx, cy = w_px * EMU_PER_PX, h_px * EMU_PER_PX
        f = min(1.0, max_w_in * 914400 / cx, max_h_in * 914400 / cy)
        cx, cy = int(cx * f), int(cy * f)
        self._pic += 1
        rid = f"rIdImg{self._pic}"
        name = f"image{self._pic}.png"
        self.images.append((rid, name, data))
        n = self._pic
        drawing = (
            f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Figure {n}"/>'
            f'<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            f'</wp:inline></w:drawing></w:r>')
        self.body.append(f'<w:p>{_ppr(align="center", before=120, after=60, keep_next=True)}{drawing}</w:p>')
        self.body.append(f'<w:p>{_ppr("Caption", align="center")}{runs(caption)}</w:p>')

    # ---- package ----
    def save(self, path, *, title, footer_text, author):
        W = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
             'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
             'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
        sect = self._sect(self.orient)
        document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>'
                    + "".join(self.body) + sect + "</w:body></w:document>")
        footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W}><w:p>'
                  f'<w:pPr><w:pStyle w:val="Footer"/><w:jc w:val="center"/></w:pPr>'
                  f'{_run(footer_text + "  ·  page ", color=GREY, size=16)}'
                  f'<w:r>{_rpr(color=GREY, size=16)}<w:fldChar w:fldCharType="begin"/></w:r>'
                  f'<w:r>{_rpr(color=GREY, size=16)}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                  f'<w:r>{_rpr(color=GREY, size=16)}<w:fldChar w:fldCharType="separate"/></w:r>'
                  f'{_run("1", color=GREY, size=16)}'
                  f'<w:r>{_rpr(color=GREY, size=16)}<w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
        styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial" w:cs="Arial"/>
<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="360" w:after="140"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="{BLUE}"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="260" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="{BLUE}"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="{NAVY}"/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="200"/></w:pPr><w:rPr><w:i/><w:color w:val="{GREY}"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:ind w:left="120" w:right="120"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/></w:style>
</w:styles>'''
        bullet = "".join(
            f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="{c}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{720 + 360 * i}" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:hint="default"/></w:rPr></w:lvl>'
            for i, (c, f) in enumerate([("•", "Arial"), ("–", "Arial"), ("•", "Arial")]))
        decimal = ('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                   '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>')
        nums = '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>' + "".join(
            f'<w:num w:numId="{nid}"><w:abstractNumId w:val="{ab}"/>'
            f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
            for nid, ab in self.nums)
        numbering = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     f'<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                     f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{bullet}</w:abstractNum>'
                     f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/>{decimal}</w:abstractNum>'
                     f'{nums}</w:numbering>')
        rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                + "".join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{nm}"/>'
                          for rid, nm, _ in self.images) + "</Relationships>")
        settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/>'
                    '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
                    '</w:settings>')
        ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                  '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                  '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                  '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                  '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                  '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
                  '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                  '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                  '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                  '</Types>')
        root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                     '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                     '</Relationships>')
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f'<dc:title>{_x(title)}</dc:title><dc:creator>{_x(author)}</dc:creator>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
        app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
               '<Application>Microsoft Office Word</Application></Properties>')
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", ctypes)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("docProps/core.xml", core)
            z.writestr("docProps/app.xml", app)
            z.writestr("word/document.xml", document)
            z.writestr("word/styles.xml", styles)
            z.writestr("word/numbering.xml", numbering)
            z.writestr("word/settings.xml", settings)
            z.writestr("word/footer1.xml", footer)
            z.writestr("word/_rels/document.xml.rels", rels)
            for _, nm, data in self.images:
                z.writestr(f"word/media/{nm}", data)
