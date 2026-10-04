"""hybrid_retriever.py -- Hybrid RAG retrieval combining dense vector search and BM25 sparse search.

Combines:
  1. Dense semantic search via ChromaDB vector embeddings
  2. Sparse lexical search via BM25 with token-level frequency & IDF
  3. Reciprocal Rank Fusion (RRF) with Rule-ID exact match boosting

Mathematical Formulation:
  RRF_Score(d) = (w_dense / (k + rank_dense(d))) + (w_sparse / (k + rank_sparse(d))) + Boost(d)
  where k=60 (standard Cormack & Clarke constant),
  Boost(d) = 0.25 if document rule_id matches explicit Rule ID in query.
"""
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


RULE_ID_PATTERN = re.compile(r"\b([A-Z]{2,4}-[A-Z]{3,4}-\d{3}|[A-Z]{2,4}-\d{3})\b")


class BM25Index:
    """In-memory BM25 index over text documents."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus: List[Dict[str, Any]] = []
        self.doc_lengths: List[int] = []
        self.avg_dl: float = 0.0
        self.doc_freqs: Counter = Counter()
        self.idf: Dict[str, float] = {}
        self.num_docs: int = 0

    @staticmethod
    def tokenize(text: str) -> List[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def index_documents(self, docs: List[Dict[str, Any]]):
        """Index a list of documents where each doc has at least 'id', 'text', and 'metadata'."""
        self.corpus = docs
        self.num_docs = len(docs)
        if self.num_docs == 0:
            self.avg_dl = 0.0
            return

        self.doc_lengths = []
        self.doc_freqs = Counter()

        tokenized_corpus = []
        total_len = 0
        for doc in docs:
            tokens = self.tokenize(doc.get("text", ""))
            tokenized_corpus.append(tokens)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_len += doc_len
            unique_tokens = set(tokens)
            for t in unique_tokens:
                self.doc_freqs[t] += 1

        self.avg_dl = total_len / float(self.num_docs) if self.num_docs > 0 else 0.0

        # Calculate Robertson-Spärck Jones IDF
        self.idf = {}
        for term, df in self.doc_freqs.items():
            # Standard smoothed BM25 IDF
            self.idf[term] = math.log(1.0 + (self.num_docs - df + 0.5) / (df + 0.5))

    def score(self, query: str, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        """Score all indexed documents against query and return top_k (doc, score)."""
        if self.num_docs == 0:
            return []

        q_tokens = self.tokenize(query)
        if not q_tokens:
            return []

        scores: List[float] = [0.0] * self.num_docs

        for doc_idx, doc in enumerate(self.corpus):
            tokens = self.tokenize(doc.get("text", ""))
            t_counts = Counter(tokens)
            doc_len = self.doc_lengths[doc_idx]
            score = 0.0

            for q_term in q_tokens:
                if q_term not in self.idf:
                    continue
                tf = t_counts.get(q_term, 0)
                if tf == 0:
                    continue
                idf = self.idf[q_term]
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / (self.avg_dl or 1.0)))
                score += idf * (numerator / denominator)

            scores[doc_idx] = score

        # Rank documents
        ranked_indices = sorted(range(self.num_docs), key=lambda i: scores[i], reverse=True)
        results = []
        for idx in ranked_indices[:top_k]:
            if scores[idx] > 0.0:
                results.append((self.corpus[idx], scores[idx]))
        return results


class HybridRetriever:
    """Combines ChromaDB vector retrieval with BM25 keyword retrieval using RRF."""

    def __init__(self, chroma_client, default_top_k: int = 5):
        self.chroma = chroma_client
        self.default_top_k = default_top_k
        self._bm25_indices: Dict[str, BM25Index] = {}

    def get_or_build_bm25_index(self, collection_name: str, version: Optional[str] = None) -> BM25Index:
        """Fetch all documents from ChromaDB collection and build an in-memory BM25 index."""
        cache_key = f"{collection_name}_{version or 'all'}"
        if cache_key in self._bm25_indices:
            return self._bm25_indices[cache_key]

        try:
            col = self.chroma.get_collection(collection_name)
            where_filter = {"version": version} if version else None
            data = col.get(where=where_filter, include=["documents", "metadatas"])
            docs = []
            if data and data.get("ids"):
                for i, doc_id in enumerate(data["ids"]):
                    docs.append({
                        "id": doc_id,
                        "text": data["documents"][i] if data.get("documents") else "",
                        "metadata": data["metadatas"][i] if data.get("metadatas") else {},
                    })
            idx = BM25Index()
            idx.index_documents(docs)
            self._bm25_indices[cache_key] = idx
            return idx
        except Exception:
            empty_idx = BM25Index()
            self._bm25_indices[cache_key] = empty_idx
            return empty_idx

    def retrieve(
        self,
        query: str,
        product: str,
        version: Optional[str] = None,
        top_k: Optional[int] = None,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        rrf_k: int = 60,
    ) -> List[Dict[str, Any]]:
        """Execute hybrid search using Reciprocal Rank Fusion."""
        k = top_k or self.default_top_k
        candidate_pool_size = max(k * 2, 10)

        # 1. Dense retrieval from ChromaDB
        dense_results = []
        try:
            col = self.chroma.get_collection(product)
            count = col.count()
            if count > 0:
                n = min(candidate_pool_size, count)
                where_filter = {"version": version} if version else None
                q_res = col.query(
                    query_texts=[query],
                    n_results=n,
                    where=where_filter,
                )
                if q_res and q_res.get("ids") and len(q_res["ids"]) > 0:
                    for i in range(len(q_res["ids"][0])):
                        dense_results.append({
                            "id": q_res["ids"][0][i],
                            "text": q_res["documents"][0][i],
                            "metadata": q_res["metadatas"][0][i],
                            "distance": q_res["distances"][0][i] if q_res.get("distances") else 0.0,
                        })
        except Exception:
            # Fallback to generic collection
            try:
                col = self.chroma.get_collection("eligibility")
                count = col.count()
                if count > 0:
                    n = min(candidate_pool_size, count)
                    q_res = col.query(query_texts=[query], n_results=n)
                    if q_res and q_res.get("ids") and len(q_res["ids"]) > 0:
                        for i in range(len(q_res["ids"][0])):
                            dense_results.append({
                                "id": q_res["ids"][0][i],
                                "text": q_res["documents"][0][i],
                                "metadata": q_res["metadatas"][0][i],
                                "distance": q_res["distances"][0][i] if q_res.get("distances") else 0.0,
                            })
            except Exception:
                dense_results = []

        # 2. Sparse BM25 retrieval
        bm25_idx = self.get_or_build_bm25_index(product, version)
        sparse_hits = bm25_idx.score(query, top_k=candidate_pool_size)

        # Check for explicit Rule ID in query (e.g. "HL-INC-001" or "AL-LTV-001")
        rule_match = RULE_ID_PATTERN.search(query.upper())
        explicit_rule_id = rule_match.group(1) if rule_match else None

        # 3. Reciprocal Rank Fusion (RRF)
        # Map doc_id -> merged info & RRF score
        doc_map: Dict[str, Dict[str, Any]] = {}
        rrf_scores: Dict[str, float] = {}

        # Dense ranking
        for rank, item in enumerate(dense_results, start=1):
            doc_id = item["id"]
            doc_map[doc_id] = item
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (dense_weight / (rrf_k + rank))

        # Sparse ranking
        for rank, (doc, _score) in enumerate(sparse_hits, start=1):
            doc_id = doc["id"]
            if doc_id not in doc_map:
                doc_map[doc_id] = doc
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (sparse_weight / (rrf_k + rank))

        # Apply Rule-ID exact match boost
        if explicit_rule_id:
            for doc_id, item in doc_map.items():
                item_rule = item.get("metadata", {}).get("rule_id", "")
                if item_rule and item_rule.upper() == explicit_rule_id:
                    rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 0.25

        # Sort by final RRF score
        sorted_doc_ids = sorted(doc_map.keys(), key=lambda d_id: rrf_scores[d_id], reverse=True)

        final_output = []
        for doc_id in sorted_doc_ids[:k]:
            item = doc_map[doc_id]
            meta = item.get("metadata", {})
            score = round(rrf_scores[doc_id], 4)
            # Retain compatibility fields
            final_output.append({
                "doc": meta.get("doc", ""),
                "section": meta.get("section", ""),
                "rule_id": meta.get("rule_id", "-"),
                "version": meta.get("version", version or "v1"),
                "policy_id": meta.get("policy_id", ""),
                "product": meta.get("product", product),
                "text": item.get("text", ""),
                "score": score,
                "distance": round(item.get("distance", 1.0 - min(score, 1.0)), 4),
            })

        return final_output
