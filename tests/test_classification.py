from __future__ import annotations

import unittest

from leadharbor.classification import infer_market, infer_office, prepare_output_fields
from leadharbor.models import Lead


class ClassificationTests(unittest.TestCase):
    def test_infers_output_market_and_builder_type(self) -> None:
        lead = Lead(name="ABC Homes", description="Regional home builder")
        prepare_output_fields(lead, "Chicago")
        self.assertEqual(lead.market, "Illinois")
        self.assertEqual(lead.company_type, "Builder")

    def test_infers_remodeler_before_contractor(self) -> None:
        lead = Lead(name="XYZ Renovation", description="Retail renovation contractor")
        prepare_output_fields(lead, "Orlando")
        self.assertEqual(lead.company_type, "Remodeler")

    def test_assigns_offices_by_state_and_texas_city(self) -> None:
        cases = (
            ("Chicago, IL", "KCC"), ("Detroit, Michigan", "KCC"),
            ("Madison, WI", "KCCWI"), ("Minneapolis, Minnesota", "KCCWI"),
            ("Orlando, FL", "KCCFL"), ("Austin, TX", "KCC"),
            ("San Antonio, Texas", "KCC"), ("Dallas, TX", ""),
        )
        for market, expected in cases:
            with self.subTest(market=market):
                self.assertEqual(infer_office(Lead(name="Test", market=market)), expected)

    def test_assigns_all_eleven_target_state_markets(self) -> None:
        cases = (
            ("Chicago, IL", "Illinois"), ("Indianapolis, IN", "Indiana"),
            ("Des Moines, IA", "Iowa"), ("New Orleans, LA", "Louisiana"),
            ("Detroit, Michigan", "Michigan"), ("Minneapolis, MN", "Minnesota"),
            ("Jackson, MS", "Mississippi"), ("Columbus, OH", "Ohio"),
            ("Dallas, TX", "Texas"), ("Madison, WI", "Wisconsin"),
            ("Miami, FL", "Florida"), ("Mobile, AL", ""),
        )
        for location, expected in cases:
            with self.subTest(location=location):
                self.assertEqual(infer_market(Lead(name="Test", address=location)), expected)

    def test_output_market_replaces_raw_location_and_preserves_address(self) -> None:
        lead = Lead(name="Detroit Builder", market="Detroit, MI")
        prepare_output_fields(lead, "Michigan")
        self.assertEqual(lead.market, "Michigan")
        self.assertEqual(lead.address, "Detroit, MI")
        self.assertEqual(lead.office, "KCC")


if __name__ == "__main__":
    unittest.main()
