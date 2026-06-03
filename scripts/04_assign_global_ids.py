"""Assign global superintendent identifiers by merging within-state super_ids
using confirmed cross-state disambiguation matches.

Uses union-find to transitively merge super_ids: if AR's ar_00003 and MO's
mo_12144 are confirmed as the same person, all their rows get one global_id.

Usage:
    python scripts/04_assign_global_ids.py
"""

import csv
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MAIN_CSV = ROOT / "data" / "processed" / "combined_superintendents.csv"
MATCHES_CSV = ROOT / "disambiguation" / "output" / "critic_combined_passed.csv"
OUTPUT_CSV = ROOT / "data" / "processed" / "combined_superintendents_global.csv"


# --- Union-Find ---


class UnionFind:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# --- Main ---


def load_super_id_lookup(path: Path) -> dict[tuple[str, str], str]:
    """Build (name_clean, leaid) -> super_id lookup from main dataset."""
    lookup = {}
    with open(path, encoding="latin-1", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["name_clean"].strip(), row["leaid"].strip())
            lookup[key] = row["super_id"].strip()
    return lookup


def load_matches(path: Path) -> list[dict]:
    """Load confirmed cross-state matches."""
    rows = []
    with open(path, encoding="latin-1", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("state1", "").strip() != row.get("state2", "").strip():
                rows.append(row)
    return rows


def main():
    if not MAIN_CSV.exists():
        logger.error(f"Main dataset not found: {MAIN_CSV}")
        sys.exit(1)
    if not MATCHES_CSV.exists():
        logger.error(f"Matches file not found: {MATCHES_CSV}")
        sys.exit(1)

    # Step 1: Build lookup
    logger.info("Loading main dataset...")
    lookup = load_super_id_lookup(MAIN_CSV)
    all_super_ids = set(lookup.values())
    logger.info(f"  {len(lookup):,} (name, leaid) pairs -> {len(all_super_ids):,} unique super_ids")

    # Step 2: Load matches
    logger.info("Loading confirmed matches...")
    matches = load_matches(MATCHES_CSV)
    logger.info(f"  {len(matches)} confirmed cross-state pairs")

    # Step 3: Union-find on super_id pairs
    uf = UnionFind()
    # Initialize all super_ids
    for sid in all_super_ids:
        uf.find(sid)

    def find_sid(name: str, leaid: str) -> str | None:
        """Look up super_id, trying both raw and 0-padded LEAID."""
        sid = lookup.get((name, leaid))
        if sid:
            return sid
        # Try 0-padded (disambiguation uses 6-digit, main dataset uses 7-digit)
        return lookup.get((name, "0" + leaid))

    matched = 0
    skipped = 0
    for row in matches:
        name = row["name"].strip()
        leaid1 = row["leaid1"].strip()
        leaid2 = row["leaid2"].strip()

        sid1 = find_sid(name, leaid1)
        sid2 = find_sid(name, leaid2)

        if sid1 and sid2:
            uf.union(sid1, sid2)
            matched += 1
        else:
            skipped += 1
            if not sid1:
                logger.debug(f"  Skip: ({name}, {leaid1}) not in main dataset")
            if not sid2:
                logger.debug(f"  Skip: ({name}, {leaid2}) not in main dataset")

    logger.info(f"  Merged {matched} pairs, skipped {skipped} (missing from main dataset)")

    # Step 4: Assign global_ids
    # Map each root super_id to a sequential global_id
    root_to_global = {}
    counter = 1
    for sid in sorted(all_super_ids):
        root = uf.find(sid)
        if root not in root_to_global:
            root_to_global[root] = f"g_{counter:05d}"
            counter += 1

    sid_to_global = {sid: root_to_global[uf.find(sid)] for sid in all_super_ids}

    # Count merges
    unique_globals = len(set(sid_to_global.values()))
    merges = len(all_super_ids) - unique_globals
    logger.info(f"  {len(all_super_ids):,} super_ids -> {unique_globals:,} global_ids ({merges} merged)")

    # Step 5: Write output
    logger.info(f"Writing output to {OUTPUT_CSV}...")
    input_fields = None
    rows_written = 0

    with open(MAIN_CSV, encoding="latin-1", newline="") as fin:
        reader = csv.DictReader(fin)
        input_fields = reader.fieldnames
        output_fields = input_fields + ["global_id"]

        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as fout:
            writer = csv.DictWriter(fout, fieldnames=output_fields)
            writer.writeheader()

            for row in reader:
                sid = row["super_id"].strip()
                row["global_id"] = sid_to_global.get(sid, "")
                writer.writerow(row)
                rows_written += 1

    logger.info(f"  {rows_written:,} rows written")

    # Step 6: Verification
    logger.info("Verification:")

    # Check every row has a global_id
    missing_gid = 0
    with open(OUTPUT_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("global_id", "").strip():
                missing_gid += 1
    logger.info(f"  Rows missing global_id: {missing_gid}")

    # Check matched pairs share global_id
    gid_lookup = {}
    with open(OUTPUT_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            gid_lookup[(row["name_clean"].strip(), row["leaid"].strip())] = row["global_id"]

    def find_gid(name: str, leaid: str) -> str | None:
        """Look up global_id, trying both raw and 0-padded LEAID."""
        gid = gid_lookup.get((name, leaid))
        if gid:
            return gid
        return gid_lookup.get((name, "0" + leaid))

    mismatches = 0
    for row in matches:
        name = row["name"].strip()
        gid1 = find_gid(name, row["leaid1"].strip())
        gid2 = find_gid(name, row["leaid2"].strip())
        if gid1 and gid2 and gid1 != gid2:
            mismatches += 1
            logger.warning(f"  MISMATCH: {name} -> {gid1} vs {gid2}")

    logger.info(f"  Matched pairs with different global_ids: {mismatches}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
