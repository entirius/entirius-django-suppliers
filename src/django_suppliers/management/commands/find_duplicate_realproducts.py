# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Report RealProducts that share an EAN -- operator triage for the cross-supplier merge dashboard.

Read-only by design. Thin CLI wrapper around
`services.duplicate_detection_service.find_duplicates_by_ean` (extracted
so the REST endpoint `GET /api/suppliers/v2/admin/duplicates/` and
this command share the same query + suggestion logic).

Example:
  docker compose exec volkanos python manage.py find_duplicate_realproducts --by ean
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Find RealProducts that share an EAN and report linked suppliers + merge suggestion (read-only)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--by",
            required=True,
            choices=["ean"],
            help="Grouping key. Only 'ean' for now; future stages may add 'name', 'name+weight'.",
        )
        parser.add_argument(
            "--tolerance-pct",
            type=float,
            default=10.0,
            help="Weight diff threshold (%%) used to split MERGE vs REVIEW suggestions. Default: 10.",
        )

    def handle(self, *args, **options) -> None:
        if options["by"] != "ean":
            raise CommandError(f"Unsupported --by value: {options['by']!r}")
        self._report_ean_duplicates(tolerance_pct=options["tolerance_pct"])

    def _report_ean_duplicates(self, *, tolerance_pct: float) -> None:
        from django_suppliers.services.duplicate_detection_service import find_duplicates_by_ean

        groups = find_duplicates_by_ean(tolerance_pct=tolerance_pct)
        if not groups:
            self.stdout.write("No EAN-duplicate groups found.")
            return

        self.stdout.write(f"Found {len(groups)} EAN group(s) with multiple RealProduct:")
        for group in groups:
            self.stdout.write(f"\n  EAN {group.ean} ({len(group.realproducts)} RealProducts):")
            for rp in group.realproducts:
                supplier_str = (
                    ", ".join(f"{s['idx']}{' ★' if s['is_preferred'] else ''}" for s in rp.suppliers)
                    if rp.suppliers
                    else "(no links)"
                )
                self.stdout.write(
                    f"    - {rp.sku} weight={rp.weight} width={rp.width} height={rp.height} deep={rp.deep} "
                    f"suppliers={supplier_str}"
                )
            self.stdout.write(f"    Suggestion: {group.suggestion.upper()} ({group.suggestion_detail})")
