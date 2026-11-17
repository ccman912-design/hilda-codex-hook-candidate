#!/usr/bin/env python3
"""Reconcile the pinned Codex release lockfile's *workspace-only* 0.0.0 entries.

No external package, checksum, git source, or locked dependency version is changed.
All edits are fail-closed and checked against Cargo metadata --no-deps.
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tomllib

def main():
    root = Path(sys.argv[1]).resolve()
    lock = root / "Cargo.lock"
    before = lock.read_text()
    parsed = tomllib.loads(before)
    metadata = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout)
    workspace_ids = set(metadata["workspace_members"])
    workspace = {p["name"]: p["version"] for p in metadata["packages"] if p["id"] in workspace_ids}
    to_update = {p["name"] for p in parsed["package"]
                 if "source" not in p and p["version"] == "0.0.0"}
    if not to_update:
        print("WORKSPACE_LOCK_NORMALIZATION: NOOP")
        return
    unknown = to_update - workspace.keys()
    if unknown:
        raise RuntimeError("unknown source-less package names; refusing update: " + repr(sorted(unknown)))
    if len(to_update) > 300:
        raise RuntimeError("unexpected number of local package versions")
    text = before
    for name in sorted(to_update):
        version = workspace[name]
        if version == "0.0.0" or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.]+)?", version):
            raise RuntimeError(f"invalid expected version for {name!r}: {version!r}")
        # Only a package stanza for a source-less local package may change.
        pkg_pattern = re.compile(
            r"(?m)^(\[\[package\]\]\nname = \"" + re.escape(name) +
            r"\"\nversion = \")0\.0\.0(\"(?:\n|$))"
        )
        text, num = pkg_pattern.subn(lambda m: m.group(1) + version + m.group(2), text)
        if num != 1:
            raise RuntimeError(f"local package entry does not have one exact anchor: {name}: {num}")
        # References qualified with version require the new local workspace version.
        text = text.replace(f'"{name} 0.0.0"', f'"{name} {version}"')
    after = tomllib.loads(text)
    for old_pkg, new_pkg in zip(parsed["package"], after["package"], strict=True):
        if old_pkg["name"] != new_pkg["name"]:
            raise RuntimeError("package order/name changed unexpectedly")
        if old_pkg["name"] in to_update and "source" not in old_pkg:
            if old_pkg["version"] == "0.0.0" and new_pkg["version"] == workspace[old_pkg["name"]]:
                old_pkg = {**old_pkg, "version": workspace[old_pkg["name"]]}
        if old_pkg != new_pkg:
            # Dependencies may contain local-name/version references but no changed sources.
            if any(k not in ("dependencies",) and old_pkg.get(k) != new_pkg.get(k)
                   for k in old_pkg.keys() | new_pkg.keys()):
                raise RuntimeError(f"unexpected lockfile change: {old_pkg['name']}")
            # Require every altered version qualifier to refer to a local workspace dependency.
            for a, b in zip(old_pkg.get("dependencies", []), new_pkg.get("dependencies", []), strict=True):
                if a == b:
                    continue
                local_name = a.removesuffix(" 0.0.0")
                if a != f"{local_name} 0.0.0" or local_name not in to_update or b != f"{local_name} {workspace[local_name]}":
                    raise RuntimeError(f"unexpected dependency reference change {a} -> {b}")
    lock.write_text(text)
    print("WORKSPACE_LOCK_NORMALIZATION: PASS")
    print("LOCAL_PACKAGES_RECONCILED:", len(to_update))
    print("EXTERNAL_PACKAGE_SOURCES_AND_CHECKSUMS: UNCHANGED")

if __name__ == "__main__":
    main()
