"""Minimal read-only .xlsx reader (standard library). Returns each sheet as a grid of cell values.
Numbers come back as float, text as str, empty as None. Formula cells use their cached value."""
import datetime as dt
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAX_UNCOMPRESSED = 60_000_000


def _col(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def excel_date(serial):
    return (dt.date(1899, 12, 30) + dt.timedelta(days=int(round(serial)))).isoformat()


def read_workbook(file_like):
    with zipfile.ZipFile(file_like) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise ValueError("workbook too large")
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", NS)}
        sheets = {}
        for s in wb.findall("m:sheets/m:sheet", NS):
            target = targets[s.get("{%s}id" % NS["r"])].lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            sheets[s.get("name")] = _read_sheet(z.read(path), shared)
        return sheets


def _read_sheet(xml_bytes, shared):
    root = ET.fromstring(xml_bytes)
    grid = {}
    for row in root.iter("{%s}row" % NS["m"]):
        r = int(row.get("r")) - 1
        for c in row.findall("m:c", NS):
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
            elif t in ("str", "e"):
                val = v.text if v is not None else None
            elif t == "b":
                val = bool(int(v.text)) if v is not None else None
            else:
                val = float(v.text) if v is not None and v.text not in (None, "") else None
            grid.setdefault(r, {})[_col(c.get("r"))] = val
    if not grid:
        return []
    width = max(max(cols) for cols in grid.values()) + 1
    return [[grid.get(r, {}).get(c) for c in range(width)] for r in range(max(grid) + 1)]
