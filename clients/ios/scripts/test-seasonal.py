#!/usr/bin/env python3
"""Run actual Foundation-only iOS discovery sources on Swift/Linux or macOS.
SettingsStore's Combine persistence is replaced only at the platform boundary.
No replacement seasonal logic or network responses are used outside fixtures.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--foreground", action="store_true", help="run the production foreground scheduler tests")
args = parser.parse_args()
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    raise SystemExit("swiftc unavailable; install Swift or run the Xcode tests")
sources = ["Models/Models.swift", "Data/TmdbDtos.swift", "Data/TmdbRows.swift",
           "Data/TmdbRequest.swift", "Data/TmdbRepository.swift", "Data/TmdbClient.swift"]
if (root / "Velvet/Data/SeasonalCatalog.swift").exists():
    sources.append("Data/SeasonalCatalog.swift")
shim = '''import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@MainActor final class SettingsStore {
    var backendUrl = "https://backend.example.test/prefix"
    var syncUrl = "https://backend.example.test/prefix"
    var syncToken = "fixture-token"
    var tmdbApiKey = "never-send-this"
    var activeProfileId: String? = "profile-a"
}
'''
with tempfile.TemporaryDirectory(prefix="ios-seasonal-", dir=os.environ.get("TMPDIR")) as temp:
    source = Path(temp) / "Production.swift"
    source.write_text(shim + "\n".join((root / "Velvet" / path).read_text() for path in sources))
    binary = Path(temp) / "seasonal-tests"
    verification = "verify-seasonal-foreground.swift" if args.foreground else "verify-seasonal.swift"
    subprocess.run([swiftc, "-swift-version", "5", "-parse-as-library", str(source),
                    str(root / "scripts" / verification), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
