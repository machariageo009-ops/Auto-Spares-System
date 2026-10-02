from io import BytesIO
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter


def _write_sheet(ws, headers, rows):
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(list(row))
    for index, _header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(index)].width = 18


def build_excel(parts, sales, suppliers, price_history, schema_sql):
    workbook = Workbook()

    parts_sheet = workbook.active
    parts_sheet.title = "Parts"
    _write_sheet(
        parts_sheet,
        [
            "id", "name", "category", "brand", "engine", "specification",
            "buying_price", "selling_price", "stock_quantity",
            "low_stock_level", "created_at"
        ],
        [
            (
                part["id"], part["name"], part["category"], part["brand"],
                part.get("engine"), part.get("specification"),
                part["buying_price"], part["selling_price"], part["stock_quantity"],
                part["low_stock_level"], part["created_at"]
            )
            for part in parts
        ]
    )

    sales_sheet = workbook.create_sheet("Sales")
    _write_sheet(
        sales_sheet,
        [
            "id", "sale_date", "part_name", "engine", "specification",
            "quantity", "unit_price", "total_amount", "profit"
        ],
        [
            (
                sale["id"], sale["sale_date"], sale["part_name"],
                sale.get("engine"), sale.get("specification"),
                sale["quantity"], sale["unit_price"], sale["total_amount"],
                sale["profit"]
            )
            for sale in sales
        ]
    )

    suppliers_sheet = workbook.create_sheet("Suppliers")
    _write_sheet(
        suppliers_sheet,
        [
            "id", "name", "phone", "company", "parts_supplied",
            "location", "email", "notes", "created_at"
        ],
        [
            (
                supplier["id"], supplier["name"], supplier["phone"],
                supplier["company"], supplier["parts_supplied"],
                supplier["location"], supplier["email"], supplier["notes"],
                supplier["created_at"]
            )
            for supplier in suppliers
        ]
    )

    history_sheet = workbook.create_sheet("PriceHistory")
    _write_sheet(
        history_sheet,
        ["id", "part_name", "old_price", "new_price", "reason", "changed_at"],
        [
            (
                item["id"], item["part_name"], item["old_price"],
                item["new_price"], item["reason"], item["changed_at"]
            )
            for item in price_history
        ]
    )

    sql_sheet = workbook.create_sheet("SQL")
    sql_sheet["A1"] = "schema.sql"
    sql_sheet["A1"].font = Font(bold=True)
    sql_sheet["A2"] = schema_sql
    sql_sheet.column_dimensions["A"].width = 80

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = f"auto_spares_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
    return buffer, filename


def rows_from_excel(file_storage, sheet_name):
    workbook = load_workbook(file_storage, data_only=True)
    if sheet_name not in workbook.sheetnames:
        return []
    sheet = workbook[sheet_name]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(cell).strip().lower() if cell is not None else "" for cell in rows[0]]
    records = []
    for values in rows[1:]:
        if not any(values):
            continue
        records.append({headers[i]: values[i] for i in range(min(len(headers), len(values)))})
    return records
