"""Offline diagnosis: no document text, paths or evidence are printed or uploaded."""

import argparse
import json
from pathlib import Path

from tz_auditor.checks import check_numeric
from tz_auditor.parsing.docx_parser import parse_docx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("documents", nargs="+")
args = parser.parse_args()

for index, filename in enumerate(args.documents, 1):
    try:
        findings = check_numeric(parse_docx(Path(filename)))
        print(
            json.dumps(
                {
                    "document_number": index,
                    "findings": [
                        {
                            "category": f.category,
                            "status": f.status,
                            "sources": [
                                {"block_id": e.block_id, "locator": e.locator.model_dump()}
                                for e in f.evidence
                            ],
                        }
                        for f in findings
                    ],
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        print(json.dumps({"document_number": index, "error": "Local parsing failed"}))
        raise SystemExit(1) from None
