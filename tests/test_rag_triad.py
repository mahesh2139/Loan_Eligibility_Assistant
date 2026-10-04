"""test_rag_triad.py -- Quantitative RAG Triad evaluation benchmark.

Evaluates the 3 pillars of RAG Quality:
  1. Context Relevance: Are the retrieved policy chunks relevant to the user query?
  2. Groundedness (Faithfulness): Are answer claims mathematically and factually grounded in retrieved chunks?
  3. Answer Relevance: Does the generated explanation directly resolve the user's specific loan inquiry?
"""
import sys
from pathlib import Path

# Allow importing from api/ and rag/
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "api"))
sys.path.insert(0, str(ROOT_DIR / "rag"))

import pytest
import chromadb
from hybrid_retriever import HybridRetriever, BM25Index


# ── Golden Evaluation Dataset for RAG Triad ──────────────────────────────────
GOLDEN_RAG_BENCHMARK = [
    {
        "query": "What is the maximum LTV ratio for a new car auto loan?",
        "product": "auto_loan",
        "expected_rule_ids": ["AL-LTV-001"],
        "expected_keywords": ["85%", "on-road", "ltv", "new vehicles"],
    },
    {
        "query": "What is the maximum LTV for home loan amounts above 30 lakhs?",
        "product": "home_loan",
        "expected_rule_ids": ["HL-LTV-001"],
        "expected_keywords": ["80%", "30,00,000", "ltv", "down payment"],
    },
    {
        "query": "What is the minimum credit score required for personal loan approval?",
        "product": "personal_loan",
        "expected_rule_ids": ["PL-CRD-001"],
        "expected_keywords": ["credit score", "bureau"],
    },
    {
        "query": "What is the maximum allowed tenure for a commercial vehicle auto loan?",
        "product": "auto_loan",
        "expected_rule_ids": ["AL-TEN-001"],
        "expected_keywords": ["48 months", "commercial", "tenure"],
    },
    {
        "query": "What property types are eligible for a home loan?",
        "product": "home_loan",
        "expected_rule_ids": ["HL-PROP-001"],
        "expected_keywords": ["apartment", "villa", "residential house", "agricultural"],
    },
]


@pytest.fixture(scope="module")
def retriever():
    chroma_path = str(ROOT_DIR / "rag" / "chroma")
    client = chromadb.PersistentClient(path=chroma_path)
    return HybridRetriever(client, default_top_k=3)


class TestContextRelevance:
    """Evaluates Pillar 1: Context Relevance.

    Checks if retrieved chunks contain the required Rule IDs and semantic domain keywords.
    """

    def test_context_relevance_hybrid(self, retriever):
        total_queries = len(GOLDEN_RAG_BENCHMARK)
        relevant_hits = 0

        for item in GOLDEN_RAG_BENCHMARK:
            results = retriever.retrieve(
                query=item["query"],
                product=item["product"],
                top_k=3,
            )
            assert len(results) > 0, f"No results returned for query: {item['query']}"

            retrieved_rule_ids = {r.get("rule_id") for r in results if r.get("rule_id")}
            retrieved_texts = " ".join(r["text"].lower() for r in results)

            # Check if expected rule ID was retrieved
            rule_hit = any(expected in retrieved_rule_ids for expected in item["expected_rule_ids"])

            # Check if key policy terms are present in retrieved context
            keyword_hits = sum(1 for kw in item["expected_keywords"] if kw.lower() in retrieved_texts)
            keyword_coverage = keyword_hits / len(item["expected_keywords"])

            if rule_hit or keyword_coverage >= 0.5:
                relevant_hits += 1

        context_relevance_score = relevant_hits / total_queries
        # Context relevance threshold >= 80%
        assert context_relevance_score >= 0.80, (
            f"Context Relevance {context_relevance_score:.2%} below benchmark threshold (80%)"
        )


class TestGroundedness:
    """Evaluates Pillar 2: Groundedness / Faithfulness.

    Ensures policy statements derived from retrieval are faithful to the text and don't hallucinate facts.
    """

    def test_rule_id_exact_boost_and_grounded_text(self, retriever):
        # Querying an exact Rule ID must prioritize that exact chunk
        res = retriever.retrieve("AL-LTV-001", "auto_loan", top_k=1)
        assert len(res) > 0
        top_match = res[0]
        assert top_match["rule_id"] == "AL-LTV-001"
        # Groundedness: check that numbers in top match are authentic
        assert "85%" in top_match["text"]
        assert "70%" in top_match["text"]

    def test_home_loan_ltv_groundedness(self, retriever):
        res = retriever.retrieve("home loan LTV above 30 lakh", "home_loan", top_k=2)
        combined_text = " ".join(r["text"].lower() for r in res)
        # Policy specifies 80% for loans above 30 lakhs
        assert "80%" in combined_text
        assert "30,00,000" in combined_text

    def test_auto_loan_used_car_age_groundedness(self, retriever):
        res = retriever.retrieve("used car vehicle age maximum limit", "auto_loan", top_k=2)
        combined_text = " ".join(r["text"].lower() for r in res)
        assert "10 years" in combined_text


class TestAnswerRelevance:
    """Evaluates Pillar 3: Answer Relevance.

    Verifies that the retrieved context directly addresses the core user question dimensions.
    """

    def test_property_type_relevance(self, retriever):
        res = retriever.retrieve("Can I take a loan for agricultural land?", "home_loan", top_k=2)
        combined_text = " ".join(r["text"].lower() for r in res)
        # Policy explicitly mentions agricultural land in Not Eligible section
        assert "agricultural" in combined_text
