import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import HistoricalMarketPrice


CROPS = ("beans", "cassava", "groundnuts", "maize", "rice")
REQUIRED_COLUMNS = {
    "adm1_name", "adm2_name", "mkt_name", "lat", "lon", "geo_id",
    "price_date", "currency", "data_coverage", "data_coverage_recent",
    "index_confidence_score", "spatially_interpolated",
    *(f"c_{crop}" for crop in CROPS),
    *(f"o_{crop}" for crop in CROPS),
    *(f"h_{crop}" for crop in CROPS),
    *(f"l_{crop}" for crop in CROPS),
    *(f"trust_{crop}" for crop in CROPS),
}


def decimal_or_none(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise CommandError(f"Invalid decimal value: {value}") from exc


class Command(BaseCommand):
    help = "Import a versioned World Bank Malawi monthly market-price CSV snapshot."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        parser.add_argument("--dataset-version", help="Dataset version in YYYY-MM-DD format; inferred from filename by default.")
        parser.add_argument("--replace", action="store_true", help="Replace rows when this dataset version has already been imported.")

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_path"].resolve()
        if not csv_path.is_file():
            raise CommandError(f"CSV file not found: {csv_path}")

        version_text = options.get("dataset_version")
        if not version_text:
            match = re.search(r"(20\d{2}-\d{2}-\d{2})", csv_path.name)
            if not match:
                raise CommandError("Could not infer the dataset version; pass --dataset-version YYYY-MM-DD.")
            version_text = match.group(1)
        try:
            source_version = date.fromisoformat(version_text)
        except ValueError as exc:
            raise CommandError("Dataset version must use YYYY-MM-DD format.") from exc

        existing = HistoricalMarketPrice.objects.filter(source_version=source_version)
        if existing.exists() and not options["replace"]:
            self.stdout.write(self.style.SUCCESS(
                f"Dataset version {source_version} is already present ({existing.count():,} estimates); import skipped."
            ))
            return

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS.difference(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

            if options["replace"]:
                existing.delete()
            pending = []
            created = 0
            skipped = 0
            for line_number, row in enumerate(reader, start=2):
                try:
                    price_date = date.fromisoformat(row["price_date"])
                    latitude = decimal_or_none(row["lat"])
                    longitude = decimal_or_none(row["lon"])
                except (ValueError, InvalidOperation) as exc:
                    raise CommandError(f"Invalid location or date on CSV line {line_number}.") from exc

                for crop in CROPS:
                    closing_price = decimal_or_none(row[f"c_{crop}"])
                    if closing_price is None:
                        skipped += 1
                        continue
                    pending.append(HistoricalMarketPrice(
                        source_version=source_version,
                        region=row["adm1_name"].strip(),
                        district=row["adm2_name"].strip(),
                        market=row["mkt_name"].strip(),
                        geo_id=row["geo_id"].strip(),
                        latitude=latitude,
                        longitude=longitude,
                        price_date=price_date,
                        crop=crop,
                        currency=(row["currency"].strip() or "MWK")[:3],
                        opening_price=decimal_or_none(row[f"o_{crop}"]),
                        high_price=decimal_or_none(row[f"h_{crop}"]),
                        low_price=decimal_or_none(row[f"l_{crop}"]),
                        closing_price=closing_price,
                        trust_score=decimal_or_none(row[f"trust_{crop}"]),
                        data_coverage=decimal_or_none(row["data_coverage"]),
                        recent_data_coverage=decimal_or_none(row["data_coverage_recent"]),
                        index_confidence_score=decimal_or_none(row["index_confidence_score"]),
                        spatially_interpolated=row["spatially_interpolated"].strip().lower() in {"1", "true", "yes"},
                    ))
                    if len(pending) >= 2000:
                        HistoricalMarketPrice.objects.bulk_create(pending, batch_size=1000)
                        created += len(pending)
                        pending.clear()

            if pending:
                HistoricalMarketPrice.objects.bulk_create(pending, batch_size=1000)
                created += len(pending)

        self.stdout.write(self.style.SUCCESS(
            f"Imported {created:,} price estimates for version {source_version}; skipped {skipped:,} empty crop values."
        ))
