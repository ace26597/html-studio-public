"""A complete PowerPoint package written from XML parts (a master, a layout and a theme besides the
slides), so LibreOffice opens it: the deck `test_filecheck.make_pptx` writes has only what the file
check reads. Used by the large-document tests."""

import zipfile

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
TYPE = "application/vnd.openxmlformats-officedocument.presentationml"

THEME = (f'<a:theme xmlns:a="{A}" name="Test"><a:themeElements>'
         '<a:clrScheme name="Test"><a:dk1><a:srgbClr val="1F2328"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
         '<a:dk2><a:srgbClr val="2B3A55"/></a:dk2><a:lt2><a:srgbClr val="EEF1F5"/></a:lt2>'
         + "".join(f'<a:accent{i}><a:srgbClr val="{c}"/></a:accent{i}>' for i, c in
                   enumerate(("3366AA", "AA6633", "33AA66", "AA3366", "6633AA", "66AA33"), 1))
         + '<a:hlink><a:srgbClr val="3366AA"/></a:hlink><a:folHlink><a:srgbClr val="6633AA"/></a:folHlink></a:clrScheme>'
         '<a:fontScheme name="Test"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/>'
         '</a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
         '</a:fontScheme><a:fmtScheme name="Test"><a:fillStyleLst>'
         + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
         + '</a:fillStyleLst><a:lnStyleLst>' + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
         + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
         + '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
         + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')
TREE = ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr/>{}</p:spTree>')
MASTER = (f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:bg><p:bgPr><a:solidFill>'
          '<a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>' + TREE.format("") + '</p:cSld>'
          '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
          'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
          '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>')
LAYOUT = (f'<p:sldLayout xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" type="blank"><p:cSld name="Blank">'
          + TREE.format("") + '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def _rels(*targets) -> str:
    return (f'<Relationships xmlns="{PKG}">' + "".join(
        f'<Relationship Id="rId{i}" Type="{R}/{kind}" Target="{target}"/>' for i, (kind, target) in enumerate(targets, 1))
        + "</Relationships>")


def _xfrm(shape: str, index: int) -> str:
    """Give a shape from test_filecheck a position, so LibreOffice draws it where it can be seen."""
    y = 400_000 + 900_000 * (index % 7)
    return shape.replace("<p:spPr/>", f'<p:spPr><a:xfrm><a:off x="500000" y="{y}"/><a:ext cx="11000000" cy="800000"/>'
                                      '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>', 1) \
                .replace("<p:xfrm/>", f'<p:xfrm><a:off x="500000" y="{y}"/><a:ext cx="11000000" cy="1200000"/></p:xfrm>', 1)


def make_real_pptx(path, slides) -> None:
    """slides: lists of shape XML (test_filecheck._shape and _table). A 16:9 deck in slide order."""
    count = len(slides)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="{CT}">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   f'<Override PartName="/ppt/presentation.xml" ContentType="{TYPE}.presentation.main+xml"/>'
                   f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="{TYPE}.slideMaster+xml"/>'
                   f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="{TYPE}.slideLayout+xml"/>'
                   '<Override PartName="/ppt/theme/theme1.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
                   + "".join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="{TYPE}.slide+xml"/>'
                             for i in range(1, count + 1)) + "</Types>")
        z.writestr("_rels/.rels", _rels(("officeDocument", "ppt/presentation.xml")))
        z.writestr("ppt/presentation.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:p="{P}" '
                   f'xmlns:a="{A}" xmlns:r="{R}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
                   '</p:sldMasterIdLst><p:sldIdLst>'
                   + "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 3}"/>' for i in range(count))
                   + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
                   "</p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels",
                   _rels(("slideMaster", "slideMasters/slideMaster1.xml"), ("theme", "theme/theme1.xml"),
                         *[("slide", f"slides/slide{i}.xml") for i in range(1, count + 1)]))
        z.writestr("ppt/theme/theme1.xml", THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels",
                   _rels(("slideLayout", "../slideLayouts/slideLayout1.xml"), ("theme", "../theme/theme1.xml")))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                   _rels(("slideMaster", "../slideMasters/slideMaster1.xml")))
        for i, shapes in enumerate(slides, 1):
            body = "".join(_xfrm(shape, n) for n, shape in enumerate(shapes))
            z.writestr(f"ppt/slides/slide{i}.xml",
                       f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:p="{P}" xmlns:a="{A}" '
                       f'xmlns:r="{R}"><p:cSld>{TREE.format(body)}</p:cSld><p:clrMapOvr><a:masterClrMapping/>'
                       "</p:clrMapOvr></p:sld>")
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", _rels(("slideLayout", "../slideLayouts/slideLayout1.xml")))
