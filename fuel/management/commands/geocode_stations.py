import subprocess
import sys
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Geocode truck stops with Photon and rewrite data/stations.json. Resumable."

    def add_arguments(self, parser):
        parser.add_argument("--cities-only", action="store_true")
        parser.add_argument("--poi-only", action="store_true")

    def handle(self, *args, **options):
        script = Path(__file__).resolve().parents[3] / "scripts" / "geocode_stations.py"
        command = [sys.executable, str(script)]
        if options["cities_only"]:
            command.append("--cities-only")
        if options["poi_only"]:
            command.append("--poi-only")
        subprocess.check_call(command)
