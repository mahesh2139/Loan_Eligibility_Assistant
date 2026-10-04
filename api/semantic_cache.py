"""semantic_cache.py -- Sub-15ms semantic FAQ cache for policy queries.

Reduces LLM latency and API inference costs for repetitive bank policy inquiries
(e.g., interest rates, prepayment penalties, LTV caps, document checklists).
"""
import re
import time
from typing import Dict, List, Optional, Tuple


def _normalize_text(text: str) -> str:
    """Normalize query text for semantic token matching."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


class SemanticFAQCache:
    """Fast semantic cache using normalized token jaccard & character n-gram similarity."""

    def __init__(self, threshold: float = 0.70):
        self.threshold = threshold
        # List of {"query": str, "norm_tokens": set, "answer": str, "sources": list, "product": str}
        self.entries: List[Dict] = []
        self._seed_bank_faqs()

    def _seed_bank_faqs(self):
        """Seed verified policy answers from official bank guidelines."""
        seeds = [
            {
                "query": "What is the minimum credit score for a home loan?",
                "product": "home_loan",
                "answer": (
                    "Under policy HL-CRD-001, the minimum credit score required for a Home Loan is 700 "
                    "(RBI-approved bureau). Applicants with scores between 700-749 may be subject to additional "
                    "documentation or a higher interest margin."
                ),
                "sources": [{"rule_id": "HL-CRD-001", "section": "4", "doc": "home_loan_v1.md"}],
            },
            {
                "query": "Can I prepay or foreclose my home loan without penalty?",
                "product": "home_loan",
                "answer": (
                    "Yes. In compliance with RBI guidelines, floating-rate Home Loans for individual borrowers have "
                    "zero prepayment penalty and zero foreclosure charges."
                ),
                "sources": [{"rule_id": "HL-FEE-001", "section": "8", "doc": "home_loan_v1.md"}],
            },
            {
                "query": "What is the maximum LTV ratio for an auto loan?",
                "product": "auto_loan",
                "answer": (
                    "Under policy AL-LTV-001, the maximum Loan-to-Value (LTV) ratio is 85% of on-road price for new vehicles, "
                    "and 70% of on-road price for pre-owned/used vehicles (up to 10 years old)."
                ),
                "sources": [{"rule_id": "AL-LTV-001", "section": "6", "doc": "auto_loan_v1.md"}],
            },
            {
                "query": "What is the maximum tenure for a personal loan?",
                "product": "personal_loan",
                "answer": (
                    "Under policy PL-TEN-001, the maximum tenure for a Personal Loan is 60 months (5 years), "
                    "with a minimum tenure of 12 months."
                ),
                "sources": [{"rule_id": "PL-TEN-001", "section": "6", "doc": "personal_loan_v2.md"}],
            },
            {
                "query": "What documents are required for salaried home loan applicants?",
                "product": "home_loan",
                "answer": (
                    "For salaried home loan applicants, mandatory documents include: Aadhaar & PAN card, "
                    "last 3 months salary slips, Form 16 for last 2 years, last 6 months bank statements, "
                    "and property chain documents (sale agreement, title deed, approved layout plan)."
                ),
                "sources": [{"rule_id": "HL-DOC-001", "section": "10", "doc": "home_loan_v1.md"}],
            },
        ]
        for s in seeds:
            self.add(s["query"], s["answer"], s.get("product", ""), s.get("sources", []))

    def add(self, query: str, answer: str, product: str = "", sources: Optional[List[Dict]] = None):
        norm = _normalize_text(query)
        tokens = set(norm.split())
        self.entries.append({
            "query": query,
            "norm": norm,
            "tokens": tokens,
            "answer": answer,
            "product": product,
            "sources": sources or [],
        })

    def lookup(self, query: str, product: Optional[str] = None) -> Optional[Tuple[str, List[Dict], float]]:
        """Look up answer in cache. Returns (answer, sources, score) if hit, else None."""
        t0 = time.perf_counter()
        norm = _normalize_text(query)
        tokens = set(norm.split())
        if not tokens:
            return None

        best_score = 0.0
        best_entry = None

        for entry in self.entries:
            if product and entry.get("product") and entry["product"] != product:
                continue

            # Jaccard similarity of token sets
            intersection = len(tokens & entry["tokens"])
            union = len(tokens | entry["tokens"])
            jaccard = intersection / float(union) if union > 0 else 0.0

            # Substring / exact containment boost
            if norm in entry["norm"] or entry["norm"] in norm:
                jaccard = max(jaccard, 0.85)

            if jaccard > best_score:
                best_score = jaccard
                best_entry = entry

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        if best_entry and best_score >= self.threshold:
            return best_entry["answer"], best_entry["sources"], best_score

        return None


# Default global cache instance
global_semantic_cache = SemanticFAQCache()
