#!/usr/bin/env python3
"""Create a clean source archive and optionally build a Fedora RPM."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from umbratlas import __version__

out = ROOT / "dist"
out.mkdir(exist_ok=True)
archive = out / f"umbratlas-{__version__}.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    for item in ("umbratlas", "assets", "tests", "packaging", "scripts", "install.sh", "README.md", "RELEASE.md"):
        tar.add(ROOT / item, arcname=f"umbratlas-{__version__}/{item}",
                filter=lambda info: None if "__pycache__" in info.name or info.name.endswith(".pyc") else info)
artifacts = [archive]
if "--rpm" in sys.argv:
    with TemporaryDirectory(prefix="umbratlas-rpm-", dir="/tmp") as build_dir:
        top = Path(build_dir)
        (top / "SOURCES").mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, top / "SOURCES" / archive.name)
        subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {top}", "--define", "dist %{nil}",
                        str(ROOT / "packaging/umbratlas.spec")], check=True)
        for rpm in (top / "RPMS").rglob("*.rpm"):
            target = out / rpm.name
            shutil.copy2(rpm, target)
            artifacts.append(target)
(out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in artifacts))
for path in artifacts:
    print(path)
