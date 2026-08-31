import sqlite3
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
class Command(BaseCommand):
    help="Verify a restored SQLite backup using integrity checks and required-table probes."
    def add_arguments(self,parser):parser.add_argument("backup")
    def handle(self,*args,**options):
        path=Path(options["backup"]).resolve()
        if not path.is_file():raise CommandError("Backup file does not exist.")
        try:
            connection=sqlite3.connect(f"file:{path}?mode=ro",uri=True);integrity=connection.execute("PRAGMA integrity_check").fetchone()[0];tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")};connection.close()
        except sqlite3.Error as error:raise CommandError(f"Backup could not be opened: {error}")from error
        required={"django_migrations","core_user","core_order","core_listing"};missing=required-tables
        if integrity!="ok"or missing:raise CommandError(f"Backup verification failed; integrity={integrity}; missing={sorted(missing)}")
        self.stdout.write(self.style.SUCCESS(f"Backup restoration verification passed for {path.name}."))
