"""Fetch ARASAAC pictograms for the curated concept catalog.

For each concept in src/my20q/pictograms/data/concepts.yaml this resolves
the concept's `arasaac_query` against the ARASAAC API, downloads the top
pictogram to assets/arasaac/<concept_id>.png, and writes an attribution
file. ARASAAC pictograms are CC BY-NC-SA — attribution is required.

Run once (needs network and the package installed):

    python scripts/fetch_icons.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2

from my20q.pictograms import load_catalog

_SEARCH = "https://api.arasaac.org/api/pictograms/en/search/{query}"
_IMAGE = "https://static.arasaac.org/pictograms/{id}/{id}_500.png"
_DEST = Path(__file__).resolve().parent.parent / "assets" / "arasaac"

_ATTRIBUTION = """\
# ARASAAC pictograms

The pictograms in this directory are from ARASAAC (https://arasaac.org),
licensed CC BY-NC-SA. Author: Sergio Palao. Origin: ARASAAC. Owner:
Government of Aragon (Spain). They are fetched by scripts/fetch_icons.py
and are not committed to the repository.

"""


def main() -> int:
    catalog = load_catalog()
    _DEST.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    with httpx2.Client(timeout=30.0, follow_redirects=True) as client:
        for concept in catalog:
            try:
                results = client.get(_SEARCH.format(query=concept.arasaac_query)).json()
            except (httpx2.HTTPError, ValueError) as exc:
                print(f"  ! {concept.id}: search failed ({exc})")
                continue
            if not isinstance(results, list) or not results:
                print(f"  ! {concept.id}: no ARASAAC match for {concept.arasaac_query!r}")
                continue
            arasaac_id = results[0].get("_id")
            image = client.get(_IMAGE.format(id=arasaac_id))
            if image.status_code != 200:
                print(f"  ! {concept.id}: image #{arasaac_id} download failed")
                continue
            (_DEST / f"{concept.id}.png").write_bytes(image.content)
            manifest.append({"concept": concept.id, "arasaac_id": arasaac_id})
            print(f"  + {concept.id} <- ARASAAC #{arasaac_id}")

    lines = "\n".join(
        f"- `{m['concept']}.png` — ARASAAC #{m['arasaac_id']}" for m in manifest
    )
    (_DEST / "ATTRIBUTION.md").write_text(_ATTRIBUTION + lines + "\n", encoding="utf-8")
    (_DEST / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nFetched {len(manifest)}/{len(catalog)} pictograms to {_DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
