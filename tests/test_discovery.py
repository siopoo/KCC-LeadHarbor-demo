from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import Mock

from pypdf import PdfWriter

from leadharbor.associations import AssociationSource, PdfAssociationSource, RcaDirectorySource
from leadharbor.search import BraveSearchSource


class DiscoveryTests(unittest.TestCase):
    def test_parses_public_association_members(self) -> None:
        html = """
        <ul>
          <li>Acme Retail Construction, Inc.</li>
          <li>Events</li>
          <li>Find A Contractor</li>
          <li>At least three years of experience as a retail construction supervisor.</li>
          <li>Customer Focused Construction</li>
        </ul>
        <a href="https://builder.example">Example Commercial Builders LLC</a>
        <a href="https://facebook.com/example">Example Facebook</a>
        """
        leads = AssociationSource.parse_html(html, "https://association.example/members")
        self.assertEqual({lead.name for lead in leads}, {
            "Acme Retail Construction, Inc.", "Example Commercial Builders LLC",
        })
        builder = next(lead for lead in leads if lead.name.startswith("Example"))
        self.assertEqual(builder.website, "https://builder.example")

    def test_parses_rca_directory_contacts(self) -> None:
        page = """
        2024 Retail Contractors Association Members
        Acme Enterprises Inc.
        Jeff Lomber,
        President/CEO
        Roseville, MI
        586-771-4800
        jlomber@acme-enterprises.com
        www.acme-enterprises.com
        Pinnacle Commercial
        Development, Inc.
        Dennis Rome,
        Vice President
        Point Pleasant, NJ
        732-892-0080
        dennis@pinnaclecommercial.us
        www.pinnaclecommercial.us
        Wolverine Building Group
        Michael J Houseman
        Grand Rapids, MI
        616-949-3360
        mhouseman@wolvgroup.com
        www.wolvgroup.com
        Winkel Construction, Inc.
        Richard Winkel,
        C.E.O.
        Inverness, FL
        352-860-0500
        rickw@winkel-construction.com
        www.winkel-construction.com
        """
        leads = RcaDirectorySource.parse_pages([page])
        self.assertEqual(len(leads), 4)
        self.assertEqual(leads[0].name, "Acme Enterprises Inc.")
        self.assertEqual(leads[0].contact_first_name, "Jeff")
        self.assertEqual(leads[0].contact_last_name, "Lomber")
        self.assertEqual(leads[0].market, "Roseville, MI")
        self.assertEqual(leads[0].phone, "586-771-4800")
        self.assertEqual(leads[0].email, "jlomber@acme-enterprises.com")
        self.assertEqual(leads[1].name, "Pinnacle Commercial Development, Inc.")
        self.assertEqual(leads[2].contact_last_name, "J Houseman")
        self.assertEqual(leads[3].name, "Winkel Construction, Inc.")

    def test_rca_empty_parse_is_reported_as_an_error(self) -> None:
        pdf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        response = Mock(content=pdf.getvalue())
        response.raise_for_status.return_value = None
        source = RcaDirectorySource()
        source.session.get = Mock(return_value=response)
        source.parse_pages = Mock(return_value=[])

        with self.assertRaisesRegex(ValueError, "no member records"):
            source.discover("retail contractor", "United States", 100)

    def test_parses_generic_text_pdf_member_blocks(self) -> None:
        page = """
        2026 Member Directory
        Coastal Builders LLC
        Jane Smith
        Owner
        Mobile, AL
        251-555-0100
        jane@coastal.example
        www.coastal.example
        Delta Construction Inc.
        Marco Ruiz
        President
        Gulfport, MS
        228-555-0199
        marco@delta.example
        https://delta.example
        """
        leads = PdfAssociationSource.parse_pages(
            [page], "Coastal Contractors Association", "https://association.example/members.pdf"
        )
        self.assertEqual(len(leads), 2)
        self.assertEqual(leads[0].name, "Coastal Builders LLC")
        self.assertEqual(leads[0].contact_first_name, "Jane")
        self.assertEqual(leads[0].contact_last_name, "Smith")
        self.assertEqual(leads[0].market, "Mobile, AL")
        self.assertEqual(leads[0].phone, "251-555-0100")
        self.assertEqual(leads[1].name, "Delta Construction Inc.")

    def test_converts_search_result_to_lead(self) -> None:
        lead = BraveSearchSource._to_lead({
            "title": "Acme Retail Builders - Home",
            "url": "https://acme.example/retail",
            "description": "General contractor for national retail rollouts.",
        }, "retail builder Texas")
        self.assertIsNotNone(lead)
        assert lead is not None
        self.assertEqual(lead.name, "Acme Retail Builders")
        self.assertEqual(lead.website, "https://acme.example/retail")

    def test_ignores_social_search_result(self) -> None:
        lead = BraveSearchSource._to_lead({
            "title": "Acme on LinkedIn",
            "url": "https://www.linkedin.com/company/acme",
        }, "retail builder")
        self.assertIsNone(lead)

    def test_ignores_procore_directory_and_generic_category_results(self) -> None:
        procore = BraveSearchSource._to_lead({
            "title": "Find General Contractors in Florida",
            "url": "https://network.procore.com/us/fl?types=general-contractors",
        }, "general contractor Florida")
        generic = BraveSearchSource._to_lead({
            "title": "Find General Contractors in Florida",
            "url": "https://directory.example/florida-contractors",
        }, "general contractor Florida")
        self.assertIsNone(procore)
        self.assertIsNone(generic)


if __name__ == "__main__":
    unittest.main()
