"""Seed the default compliance policy, and optionally a demo dataset.

Reproducible and idempotent - running it twice changes nothing. Demo mode
(``--demo``) writes through the ordinary schema and is flagged with
``organizations.is_demo``, so demo rows are always distinguishable from real
ones and production code paths never generate them.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

DEFAULT_POLICY_NAME = "Default Compliance Policy"

# The twelve categories the default policy ships with. Weights encode how
# serious a confirmed clause in that category is; thresholds encode how sure
# the model must be before it counts. Both are operator-editable and are never
# sent to the model.
CATEGORIES = [
    {
        "category": "data_retention",
        "display_name": "Data Retention",
        "description": (
            "How long the vendor keeps customer data, whether data is retained after "
            "termination, and whether deletion can be requested and enforced."
        ),
        "retrieval_guidance": (
            "Look for retention periods, deletion timelines, archival rights, and language "
            "about keeping data after the agreement ends."
        ),
        "keywords": [
            "retain",
            "retention",
            "delete",
            "deletion",
            "archive",
            "purge",
            "indefinitely",
        ],
        "severity_weight": 0.75,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 1,
    },
    {
        "category": "data_sharing",
        "display_name": "Data Sharing",
        "description": (
            "Whether customer data is disclosed, sold, or shared with third parties, "
            "affiliates, advertisers, or analytics providers, and on what terms."
        ),
        "retrieval_guidance": (
            "Look for disclosure to third parties, affiliates, partners, advertisers, or "
            "sale of data, and whether notice or consent is required."
        ),
        "keywords": [
            "disclose",
            "share",
            "sell",
            "third party",
            "affiliates",
            "partners",
            "advertising",
        ],
        "severity_weight": 0.85,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 2,
    },
    {
        "category": "subprocessors",
        "display_name": "Subprocessors",
        "description": (
            "Use of subprocessors to handle customer data, whether the customer is notified "
            "of changes, and whether they may object."
        ),
        "retrieval_guidance": "Look for subprocessor, sub-processor, service provider, and vendor engagement terms.",
        "keywords": [
            "subprocessor",
            "sub-processor",
            "service provider",
            "vendor",
            "notify",
            "object",
        ],
        "severity_weight": 0.60,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 3,
    },
    {
        "category": "ip_ownership",
        "display_name": "Intellectual Property Ownership",
        "description": (
            "Who owns the software, the service, customer content, feedback, and any "
            "derivative works created from them."
        ),
        "retrieval_guidance": "Look for ownership, title, right, interest, work made for hire, and assignment.",
        "keywords": [
            "ownership",
            "title",
            "intellectual property",
            "derivative works",
            "assign",
            "feedback",
        ],
        "severity_weight": 0.70,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 4,
    },
    {
        "category": "content_licensing",
        "display_name": "Content Licensing",
        "description": (
            "The licence the customer grants the vendor over uploaded content - its scope, "
            "duration, sublicensing rights, and whether it survives termination."
        ),
        "retrieval_guidance": (
            "Look for perpetual, irrevocable, royalty-free, sublicensable, worldwide, and "
            "transferable licence grants, and any use for model training."
        ),
        "keywords": [
            "license",
            "perpetual",
            "irrevocable",
            "royalty-free",
            "sublicensable",
            "worldwide",
            "training",
        ],
        "severity_weight": 0.80,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 5,
    },
    {
        "category": "automatic_renewal",
        "display_name": "Automatic Renewal",
        "description": (
            "Whether the subscription renews automatically, on what notice, and at what price."
        ),
        "retrieval_guidance": "Look for automatically renew, successive terms, notice of non-renewal, and then-current price.",
        "keywords": [
            "automatically renew",
            "auto-renew",
            "renewal",
            "successive",
            "non-renewal",
            "then-current",
        ],
        "severity_weight": 0.55,
        "confidence_threshold": 0.35,
        "escalate": False,
        "sort_order": 6,
    },
    {
        "category": "cancellation",
        "display_name": "Cancellation and Termination",
        "description": (
            "How the customer may cancel, what notice and method are required, and whether "
            "any refund is available."
        ),
        "retrieval_guidance": "Look for cancellation method, written notice, certified mail, refund, and non-refundable terms.",
        "keywords": ["cancel", "cancellation", "terminate", "notice", "refund", "non-refundable"],
        "severity_weight": 0.60,
        "confidence_threshold": 0.35,
        "escalate": False,
        "sort_order": 7,
    },
    {
        "category": "indemnification",
        "display_name": "Indemnification",
        "description": (
            "Which party must defend and indemnify the other, for what claims, and whether "
            "the obligation is one-sided."
        ),
        "retrieval_guidance": "Look for indemnify, defend, hold harmless, and the scope of covered claims.",
        "keywords": [
            "indemnify",
            "indemnification",
            "defend",
            "hold harmless",
            "claims",
            "attorneys' fees",
        ],
        "severity_weight": 0.85,
        "confidence_threshold": 0.40,
        "escalate": True,
        "sort_order": 8,
    },
    {
        "category": "limitation_of_liability",
        "display_name": "Limitation of Liability",
        "description": (
            "Caps on the vendor's liability, exclusions of damages, and whether the cap is "
            "disproportionate to the fees paid."
        ),
        "retrieval_guidance": "Look for liability caps, aggregate liability, consequential and indirect damages, and dollar limits.",
        "keywords": [
            "liability",
            "limitation",
            "aggregate",
            "consequential",
            "indirect",
            "damages",
            "exceed",
        ],
        "severity_weight": 0.90,
        "confidence_threshold": 0.40,
        "escalate": True,
        "sort_order": 9,
    },
    {
        "category": "governing_law",
        "display_name": "Governing Law and Jurisdiction",
        "description": (
            "Which jurisdiction's law applies and where disputes must be brought, "
            "particularly where that is inconvenient or unfavourable to the customer."
        ),
        "retrieval_guidance": "Look for governed by, construed in accordance with, exclusive jurisdiction, and venue.",
        "keywords": ["governing law", "jurisdiction", "venue", "courts", "conflict of law"],
        "severity_weight": 0.50,
        "confidence_threshold": 0.35,
        "escalate": False,
        "sort_order": 10,
    },
    {
        "category": "arbitration",
        "display_name": "Binding Arbitration",
        "description": (
            "Whether disputes must go to binding arbitration instead of court, and whether "
            "a jury trial is waived."
        ),
        "retrieval_guidance": "Look for binding arbitration, arbitrator, AAA, JAMS, and jury trial waiver.",
        "keywords": [
            "arbitration",
            "arbitrator",
            "binding",
            "jury trial",
            "waive",
            "dispute resolution",
        ],
        "severity_weight": 0.70,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 11,
    },
    {
        "category": "class_action_waiver",
        "display_name": "Class Action Waiver",
        "description": (
            "Whether the customer gives up the right to participate in a class, collective, "
            "or representative action."
        ),
        "retrieval_guidance": "Look for class action, class member, collective, representative proceeding, and individual basis.",
        "keywords": [
            "class action",
            "class member",
            "collective",
            "representative",
            "individual basis",
            "waiver",
        ],
        "severity_weight": 0.75,
        "confidence_threshold": 0.40,
        "escalate": False,
        "sort_order": 12,
    },
]


async def seed_policy(connection, org_id: str) -> str:
    policy_id = await connection.fetchval(
        "SELECT id FROM policies WHERE org_id = $1 AND name = $2 AND version = 1",
        org_id,
        DEFAULT_POLICY_NAME,
    )
    if policy_id is None:
        policy_id = await connection.fetchval(
            """
            INSERT INTO policies (org_id, name, description, is_default, is_active)
            VALUES ($1, $2, $3, TRUE, TRUE) RETURNING id
            """,
            org_id,
            DEFAULT_POLICY_NAME,
            "Twelve compliance categories covering the risks most commonly found in "
            "EULAs, terms of service, and SaaS agreements.",
        )
        print(f"  + created policy '{DEFAULT_POLICY_NAME}'")
    else:
        print(f"  = policy '{DEFAULT_POLICY_NAME}' already exists")

    for rule in CATEGORIES:
        await connection.execute(
            """
            INSERT INTO policy_rules
                (org_id, policy_id, category, display_name, description, retrieval_guidance,
                 keywords, severity_weight, confidence_threshold, escalate, sort_order)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (policy_id, category) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                retrieval_guidance = EXCLUDED.retrieval_guidance,
                keywords = EXCLUDED.keywords
            """,
            org_id,
            policy_id,
            rule["category"],
            rule["display_name"],
            rule["description"],
            rule["retrieval_guidance"],
            rule["keywords"],
            rule["severity_weight"],
            rule["confidence_threshold"],
            rule["escalate"],
            rule["sort_order"],
        )
    print(f"  = {len(CATEGORIES)} policy rules present")
    return str(policy_id)


async def seed_demo(connection) -> None:
    """Create a demo organization with a sample agreement, through the normal schema."""
    from app.services.chunking import chunk_document
    from app.services.normalization import content_hash, normalize_text

    org_id = await connection.fetchval("SELECT id FROM organizations WHERE slug = 'demo-workspace'")
    if org_id is None:
        org_id = await connection.fetchval(
            "INSERT INTO organizations (name, slug, is_demo) "
            "VALUES ('Demo Workspace', 'demo-workspace', TRUE) RETURNING id"
        )
        print("  + created demo organization")

    await seed_policy(connection, str(org_id))

    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_eula.txt"
    if not fixture.exists():
        print("  ! sample agreement fixture is missing; skipping demo document")
        return

    text = normalize_text(fixture.read_text(encoding="utf-8"))
    document_id = await connection.fetchval(
        "SELECT id FROM documents WHERE org_id = $1 AND content_sha256 = $2",
        org_id,
        content_hash(text),
    )
    if document_id is None:
        document_id = await connection.fetchval(
            """
            INSERT INTO documents
                (org_id, title, vendor_name, source_type, normalized_text, content_sha256,
                 page_count, char_count, status)
            VALUES ($1,$2,$3,'txt',$4,$5,$6,$7,'ready') RETURNING id
            """,
            org_id,
            "Acme Cloud Services End User License Agreement",
            "Acme Cloud Services",
            text,
            content_hash(text),
            max(1, len(text) // 3000),
            len(text),
        )
        for chunk in chunk_document(text):
            await connection.execute(
                """
                INSERT INTO document_chunks
                    (org_id, document_id, ordinal, heading, chunk_text,
                     start_offset, end_offset, token_count, content_sha256)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (document_id, ordinal) DO NOTHING
                """,
                org_id,
                document_id,
                chunk.ordinal,
                chunk.heading,
                chunk.text,
                chunk.start_offset,
                chunk.end_offset,
                chunk.token_count,
                content_hash(chunk.text),
            )
        print("  + created demo document with chunks")
    else:
        print("  = demo document already exists")

    print(
        "\nDemo data ready. Run an analysis against the demo document to populate "
        "findings using the real pipeline - no fake findings are inserted."
    )


async def run(demo: bool, org_id: str | None) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    import asyncpg

    connection = await asyncpg.connect(database_url)
    try:
        if demo:
            print("Seeding demo data...")
            await seed_demo(connection)
        else:
            targets = (
                [org_id]
                if org_id
                else [str(r["id"]) for r in await connection.fetch("SELECT id FROM organizations")]
            )
            if not targets:
                print(
                    "No organizations exist yet. Sign up through the app first, or run "
                    "with --demo to create a demo workspace."
                )
                return 0
            for target in targets:
                print(f"Seeding policy for organization {target}...")
                await seed_policy(connection, target)
        print("\nSeed complete.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nSEED FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed ClauseGuard reference data.")
    parser.add_argument(
        "--demo", action="store_true", help="Create the demo organization and sample agreement."
    )
    parser.add_argument(
        "--org-id", default=None, help="Seed the default policy for one organization."
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.demo, args.org_id)))
