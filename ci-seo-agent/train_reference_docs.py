#!/usr/bin/env python3
"""One-off trainer to fetch Google Search Central reference docs into ChromaDB.
Run: python3 /home/agents/ci-seo-agent/train_reference_docs.py
"""
from reference_docs import reference_docs_trainer

if __name__ == "__main__":
    print("Starting reference docs training (max_pages=120, max_depth=3)")
    res = reference_docs_trainer.train_google_search_docs(max_pages=120, max_depth=3)
    print("Training result:", res)
