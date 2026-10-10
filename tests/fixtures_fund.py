"""Synthetic Screener-style data sheets (Rs crore) for tests."""
import csv, io, zipfile
from xml.sax.saxutils import escape

YEARS = [f"{y}-03-31" for y in range(2016, 2026)]


def quality_company(years=YEARS, g=0.15, price_mult=1.0):
    n = len(years)
    sales = [1000 * (1 + g) ** i for i in range(n)]
    np_ = [s * 0.15 for s in sales]
    dep = [s * 0.03 for s in sales]
    intr = [5.0] * n
    tax = [p / 0.75 * 0.25 for p in np_]
    pbt = [p + t for p, t in zip(np_, tax)]
    other = [s * 0.01 for s in sales]
    div = [p * 0.4 for p in np_]
    cap = [10.0] * n
    res = [500.0]
    for i in range(1, n):
        res.append(res[-1] + np_[i] - div[i])
    debt = [50.0] * n
    nb = [300 * (1 + g) ** i for i in range(n)]
    cwip = [10.0] * n
    recv = [s * 0.10 for s in sales]
    inv = [s * 0.12 for s in sales]
    cash = [100 * (1 + g) ** i for i in range(n)]
    total = [c + r + d + 200 for c, r, d in zip(cap, res, debt)]
    cfo = [p * 1.05 + d for p, d in zip(np_, dep)]
    cfi = [-(nb[i] - nb[i - 1] + dep[i]) if i else -dep[0] for i in range(n)]
    shares = [10.0] * n                      # 10 crore shares
    price = [p / 10 * 25 * price_mult for p in np_]   # P/E 25 each year
    return dict(years=years, sales=sales, np=np_, dep=dep, intr=intr, tax=tax, pbt=pbt, other=other, div=div, cap=cap,
                res=res, debt=debt, nb=nb, cwip=cwip, recv=recv, inv=inv, cash=cash, total=total, cfo=cfo, cfi=cfi,
                shares=shares, price=price, cur_price=price[-1], mcap=price[-1] * shares[-1])


def weak_company(years=YEARS):
    d = quality_company(years, g=0.02)
    n = len(years)
    d["np"] = [40, -20, 30, 10, -5, 25, 15, 5, 20, 8][:n]
    d["pbt"] = [p * 1.2 if p > 0 else p for p in d["np"]]
    d["debt"] = [900.0] * n
    d["cfo"] = [p * 0.3 for p in d["np"]]
    d["recv"] = [s * (0.1 + 0.05 * i) for i, s in enumerate(d["sales"])]
    d["shares"] = [10 * 1.06 ** i for i in range(n)]
    d["price"] = [100.0] * n
    d["cur_price"] = 100.0
    d["mcap"] = 100.0 * d["shares"][-1]
    return d


def to_grid(d, name="Test Co Ltd"):
    y = d["years"]
    rows = [["COMPANY NAME", name], [], ["META"], ["Number of shares", d["shares"][-1] * 1e7], ["Face Value", 1],
            ["Current Price", d["cur_price"]], ["Market Capitalization", d["mcap"]], [],
            ["PROFIT & LOSS"], ["Report Date", *y], ["Sales", *d["sales"]], ["Raw Material Cost", *[0] * len(y)],
            ["Other Income", *d["other"]], ["Depreciation", *d["dep"]], ["Interest", *d["intr"]],
            ["Profit before tax", *d["pbt"]], ["Tax", *d["tax"]], ["Net profit", *d["np"]], ["Dividend Amount", *d["div"]],
            [], ["Quarters"], ["Report Date", "2025-06-30"], ["Sales", 999999], ["Net profit", 999999], [],
            ["BALANCE SHEET"], ["Report Date", *y], ["Equity Share Capital", *d["cap"]], ["Reserves", *d["res"]],
            ["Borrowings", *d["debt"]], ["Other Liabilities", *[200] * len(y)], ["Total", *d["total"]],
            ["Net Block", *d["nb"]], ["Capital Work in Progress", *d["cwip"]], ["Investments", *[0] * len(y)],
            ["Other Assets", *[0] * len(y)], ["Total", *[1] * len(y)], ["Receivables", *d["recv"]], ["Inventory", *d["inv"]],
            ["Cash & Bank", *d["cash"]], [], ["CASH FLOW:"], ["Report Date", *y],
            ["Cash from Operating Activity", *d["cfo"]], ["Cash from Investing Activity", *d["cfi"]],
            ["Cash from Financing Activity", *[0] * len(y)], [], ["PRICE:", *d["price"]], [], ["DERIVED:"],
            ["Adjusted Equity Shares in Cr", *d["shares"]]]
    return rows


def to_csv(d, name="Test Co Ltd"):
    buf = io.StringIO()
    csv.writer(buf).writerows(to_grid(d, name))
    return buf.getvalue().encode()


def _ref(r, c):
    s = ""
    c += 1
    while c:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return f"{s}{r + 1}"


def to_xlsx(d, name="Test Co Ltd", date_serial=True):
    """Tiny xlsx writer. Report dates become Excel serials like Screener's export."""
    import datetime as dt
    rows_xml = []
    shared = []
    for r, row in enumerate(to_grid(d, name)):
        cells = []
        for c, v in enumerate(row):
            if v is None:
                continue
            if isinstance(v, str) and date_serial and row and row[0] == "Report Date" and c > 0:
                v = (dt.date.fromisoformat(v) - dt.date(1899, 12, 30)).days
            if isinstance(v, str):
                shared.append(v)
                cells.append(f'<c r="{_ref(r, c)}" t="s"><v>{len(shared) - 1}</v></c>')
            else:
                cells.append(f'<c r="{_ref(r, c)}"><v>{v}</v></c>')
        rows_xml.append(f'<row r="{r + 1}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Profit &amp; Loss" sheetId="1" r:id="rId1"/><sheet name="Data Sheet" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/><Relationship Id="rId2" Target="worksheets/sheet2.xml" Type="x"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData/></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")
    return buf.getvalue()
