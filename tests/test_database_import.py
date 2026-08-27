from __future__ import annotations

import unittest
from io import BytesIO
import sqlite3
import tempfile
from pathlib import Path

from openpyxl import Workbook

from leadharbor.database_import import missing_fields, parse_database_file


class DatabaseImportParserTests(unittest.TestCase):
    def test_csv_accepts_chinese_headers_and_marks_missing_fields(self) -> None:
        payload = (
            "公司名称,地区,企业类型,联系人名,联系人姓,邮箱,电话,官网,评分\n"
            "海岸建筑公司,Alabama,Builder,安娜,陈,ana@example.com,,example.com,88\n"
        ).encode("utf-8")

        leads = parse_database_file("原数据库.csv", payload)

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "海岸建筑公司")
        self.assertEqual(leads[0].website, "https://example.com")
        self.assertEqual(leads[0].score, 88)
        self.assertIn("phone", missing_fields(leads[0]))
        self.assertNotIn("email", missing_fields(leads[0]))

    def test_rows_without_company_name_are_ignored(self) -> None:
        payload = b"Company,Market,Email\n,Texas,unknown@example.com\nValid Co,Texas,\n"
        leads = parse_database_file("database.csv", payload)
        self.assertEqual([lead.name for lead in leads], ["Valid Co"])

    def test_xlsx_import_reads_the_active_sheet(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Company", "Market", "Contact Info", "Score"])
        sheet.append(["Excel Builders", "Ohio", "hello@excel.example", 76])
        payload = BytesIO()
        workbook.save(payload)
        workbook.close()

        leads = parse_database_file("legacy.xlsx", payload.getvalue())

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "Excel Builders")
        self.assertEqual(leads[0].email, "hello@excel.example")
        self.assertEqual(leads[0].score, 76)

    def test_sqlite_import_finds_a_table_with_company_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE old_leads (company_name TEXT, location TEXT, email TEXT, phone TEXT)"
            )
            connection.execute(
                "INSERT INTO old_leads VALUES (?, ?, ?, ?)",
                ("Legacy Builders", "Michigan", "legacy@example.com", "555-0300"),
            )
            connection.commit()
            connection.close()
            payload = path.read_bytes()

        leads = parse_database_file("app.db", payload)

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "Legacy Builders")
        self.assertEqual(leads[0].market, "Michigan")
        self.assertEqual(leads[0].phone, "555-0300")

    def test_rejects_unsupported_file_type(self) -> None:
        with self.assertRaises(ValueError):
            parse_database_file("database.xls", b"not supported")


if __name__ == "__main__":
    unittest.main()
