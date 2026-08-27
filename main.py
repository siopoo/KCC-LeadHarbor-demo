from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from leadharbor.associations import ASSOCIATION_PRESETS, AssociationSource, RcaDirectorySource
from leadharbor.pipeline import LeadPipeline
from leadharbor.search import BraveSearchSource
from leadharbor.sources import OpenStreetMapSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="KCC LeadHarbor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser("crawl", help="Discover and enrich B2B leads")
    crawl.add_argument("--keyword", required=True, help="Target industry or product")
    crawl.add_argument("--location", required=True, help="City, region, or country")
    crawl.add_argument("--limit", type=int, default=20, help="Maximum leads to export")
    crawl.add_argument("--output", type=Path, default=Path("leads.csv"))
    crawl.add_argument("--pages-per-site", type=int, default=4)
    crawl.add_argument("--delay", type=float, default=1.0)
    crawl.add_argument("--no-website-crawl", action="store_true")
    crawl.add_argument(
        "--source",
        choices=("osm", "search", "association", "all"),
        default="all",
        help="Lead discovery source (default: all available sources)",
    )
    crawl.add_argument(
        "--search-query",
        action="append",
        default=[],
        help="Custom web query; repeat this option for multiple queries",
    )
    crawl.add_argument(
        "--association",
        action="append",
        choices=tuple(sorted(ASSOCIATION_PRESETS)),
        default=[],
        help="Built-in public association list, for example: rca",
    )
    crawl.add_argument(
        "--association-url",
        action="append",
        default=[],
        help="Public association member-list URL; repeatable",
    )
    crawl.add_argument(
        "--association-csv",
        action="append",
        type=Path,
        default=[],
        help="CSV with company/name and optional website/email/phone columns",
    )
    return parser


def build_sources(args: argparse.Namespace) -> list[object]:
    sources: list[object] = []
    use_all = args.source == "all"

    if args.source == "osm" or use_all:
        sources.append(OpenStreetMapSource())

    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if args.source == "search" and not api_key:
        raise SystemExit("Set BRAVE_SEARCH_API_KEY before using --source search")
    if api_key and (args.source == "search" or use_all):
        sources.append(BraveSearchSource(api_key=api_key, queries=args.search_query))
    elif use_all:
        logging.info("BRAVE_SEARCH_API_KEY is not set; keyword web search is skipped")

    association_urls = list(args.association_url)
    has_associations = association_urls or args.association_csv or args.association
    if args.source == "association" and not has_associations:
        raise SystemExit("Use --association, --association-url, or --association-csv")
    if has_associations and (args.source == "association" or use_all):
        if association_urls or args.association_csv:
            sources.append(AssociationSource(association_urls, args.association_csv))
        if "rca" in args.association:
            sources.append(RcaDirectorySource())

    return sources


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.limit < 1 or args.pages_per_site < 1 or args.delay < 0:
        raise SystemExit("limit/pages-per-site must be positive and delay cannot be negative")

    pipeline = LeadPipeline(
        pages_per_site=args.pages_per_site,
        request_delay=args.delay,
        crawl_websites=not args.no_website_crawl,
        sources=build_sources(args),
    )
    leads = pipeline.run(
        keyword=args.keyword,
        location=args.location,
        limit=args.limit,
        output=args.output,
    )
    print(f"Exported {len(leads)} leads to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
