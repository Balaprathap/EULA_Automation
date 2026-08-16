from app.services.policy_builder import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_SEVERITY_WEIGHT,
    normalize_policy_draft,
)


def test_normalize_policy_draft_assigns_deterministic_scoring_defaults():
    raw = {
        "name": " SaaS Vendor Policy ",
        "description": " Reviews SaaS contract risks. ",
        "rules": [
            {
                "category": "AI Training",
                "display_name": "AI Training",
                "description": "Detect use of customer data for model training.",
                "retrieval_guidance": "Look for AI and machine-learning training rights.",
                "keywords": ["AI training", "Machine Learning", "AI training"],
            },
            {
                "category": "data sharing",
                "display_name": "Data Sharing",
                "description": "Detect sharing with third parties.",
                "retrieval_guidance": "Look for affiliates and service providers.",
                "keywords": ["third party", "affiliate"],
            },
            {
                "category": "retention",
                "display_name": "Data Retention",
                "description": "Detect post-termination data retention.",
                "retrieval_guidance": "Look for deletion and retention periods.",
                "keywords": ["retain", "delete"],
            },
        ],
    }

    draft = normalize_policy_draft(raw, requested_rule_count=8)

    assert draft["name"] == "SaaS Vendor Policy"
    assert len(draft["rules"]) == 3

    ai_rule = draft["rules"][0]

    assert ai_rule["category"] == "ai_training"
    assert ai_rule["severity_weight"] == DEFAULT_SEVERITY_WEIGHT
    assert ai_rule["confidence_threshold"] == DEFAULT_CONFIDENCE_THRESHOLD
    assert ai_rule["escalate"] is False
    assert ai_rule["is_enabled"] is True
    assert ai_rule["keywords"] == ["AI training", "Machine Learning"]


def test_normalize_policy_draft_deduplicates_categories():
    raw = {
        "name": "Privacy Policy",
        "description": "Privacy review.",
        "rules": [
            {
                "category": "data sharing",
                "display_name": "External Sharing",
                "description": "Find external disclosure rights.",
                "retrieval_guidance": "Look for third-party disclosure.",
                "keywords": ["third party"],
            },
            {
                "category": "data-sharing",
                "display_name": "Affiliate Sharing",
                "description": "Find affiliate disclosure rights.",
                "retrieval_guidance": "Look for affiliates.",
                "keywords": ["affiliate"],
            },
            {
                "category": "retention",
                "display_name": "Retention",
                "description": "Find retention obligations.",
                "retrieval_guidance": "Look for retention periods.",
                "keywords": ["retain"],
            },
        ],
    }

    draft = normalize_policy_draft(raw, requested_rule_count=8)

    categories = [rule["category"] for rule in draft["rules"]]

    assert categories == [
        "data_sharing",
        "data_sharing_2",
        "retention",
    ]


def test_normalize_policy_draft_respects_requested_rule_count():
    rules = []

    for index in range(8):
        rules.append(
            {
                "category": f"risk_{index}",
                "display_name": f"Risk {index}",
                "description": f"Description {index}",
                "retrieval_guidance": f"Guidance {index}",
                "keywords": [f"keyword {index}"],
            }
        )

    draft = normalize_policy_draft(
        {
            "name": "Test Policy",
            "description": "Test",
            "rules": rules,
        },
        requested_rule_count=5,
    )

    assert len(draft["rules"]) == 5
    assert [rule["sort_order"] for rule in draft["rules"]] == [0, 1, 2, 3, 4]
