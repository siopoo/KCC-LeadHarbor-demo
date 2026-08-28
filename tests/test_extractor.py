from __future__ import annotations

import unittest

from leadharbor.extractor import extract_page, merge_page_data
from leadharbor.models import Lead
from leadharbor.scoring import score_breakdown, score_details, score_lead
from leadharbor.sources import OpenStreetMapSource


HTML = """
<!doctype html>
<html><head>
  <title>Acme Packaging Ltd - Sustainable Bottles</title>
  <meta name="description" content="Plastic packaging manufacturer in Berlin">
  <script type="application/ld+json">
  {"@type":"Organization","name":"Acme Packaging Ltd","email":"sales@acme.example","telephone":"+49 30 1234 5678"}
  </script>
</head><body>
  <a href="/contact-us">Contact us</a>
  <p>Email sales@acme.example or call +49 30 1234 5678.</p>
</body></html>
"""


class ExtractorTests(unittest.TestCase):
    def test_extracts_company_data_and_contact_link(self) -> None:
        data, links = extract_page(HTML, "https://acme.example/")
        self.assertEqual(data["name"], "Acme Packaging Ltd")
        self.assertIn("sales@acme.example", data["email"])
        self.assertIn("+49 30 1234 5678", data["phone"])
        self.assertEqual(links, ["https://acme.example/contact-us"])

    def test_merges_and_scores_lead(self) -> None:
        lead = Lead(name="Acme Packaging", website="https://acme.example", category="manufacturer")
        data, _ = extract_page(HTML, "https://acme.example/")
        merge_page_data(lead, data)
        score_lead(lead, "plastic packaging manufacturer")
        self.assertEqual(lead.score, 7)
        self.assertIn("packaging", lead.matched_keywords)

    def test_kcc_icp_scoring_plus_office_bonus_totals_120(self) -> None:
        lead = Lead(
            name="Texas Cabinet Builders", market="Austin, TX", company_type="Builder",
            contact_first_name="Ana", contact_last_name="Diaz",
            website="https://texascabinet.example", email="ana@texascabinet.example",
            phone="512-555-0100", category="RCA association member",
            source="Retail Contractors Association member directory",
            signal="New community construction project with recent permits",
            scale="3 showrooms and 180 homes",
            description="Cabinet sales and installation for multifamily and commercial projects",
        )
        score_lead(lead, "retail cabinet builder")
        self.assertEqual(lead.score, 120)
        self.assertEqual(score_breakdown(lead)["office_assignment"], 20)
        self.assertEqual(sum(score_breakdown(lead).values()), 120)

    def test_contactability_uses_available_business_details(self) -> None:
        lead = Lead(
            name="General Supplier", website="https://supplier.example",
            email="sales@gmail.com", phone="555-0100",
        )
        self.assertEqual(score_breakdown(lead)["contactability"], 4)

    def test_score_details_explain_points_with_supporting_evidence(self) -> None:
        lead = Lead(
            name="Texas Cabinet Builders", market="Austin, TX", company_type="Builder",
            website="https://cabinet.example", email="sales@cabinet.example, team@gmail.com",
            phone="512-555-0100", signal="Recent permit for a new community",
            scale="180 homes", source="RCA association member directory",
        )
        details = score_details(lead)
        self.assertEqual(details["icp_business_type"]["points"], 25)
        self.assertIn("Builder", details["icp_business_type"]["evidence"])
        self.assertIn("sales@cabinet.example", details["contactability"]["evidence"])
        self.assertNotIn("team@gmail.com", details["contactability"]["evidence"])
        self.assertEqual(details["office_assignment"]["evidence"], ["KCC"])

    def test_office_bonus_only_applies_to_assigned_locations(self) -> None:
        assigned = Lead(name="Builder", market="Madison, WI", company_type="Builder")
        unassigned = Lead(name="Builder", market="Dallas, TX", company_type="Builder")
        self.assertEqual(score_breakdown(assigned)["office_assignment"], 20)
        self.assertEqual(score_breakdown(unassigned)["office_assignment"], 0)

    def test_custom_scoring_weights_are_applied(self) -> None:
        weights = {
            "icp_business_type": 35, "cabinet_relevance": 5,
            "active_opportunity": 20, "scale_potential": 15,
            "contactability": 10, "region_match": 10,
            "association_membership": 5, "office_assignment": 20,
        }
        lead = Lead(name="Iowa Builder", market="Iowa", company_type="Builder")
        breakdown = score_breakdown(lead, weights)
        self.assertEqual(breakdown["icp_business_type"], 35)
        self.assertEqual(breakdown["region_match"], 10)
        self.assertEqual(sum(weights.values()), 120)

    def test_unnamed_osm_object_is_left_empty_for_filtering(self) -> None:
        lead = OpenStreetMapSource._to_lead({"type": "node", "id": 1, "tags": {"craft": "bakery"}})
        self.assertEqual(lead.name, "")


if __name__ == "__main__":
    unittest.main()
