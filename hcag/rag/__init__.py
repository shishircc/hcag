"""`rag` CLI — index a KB folder into a local LanceDB store for hybrid search.

See DESIGN.md §8 for the full spec. The tool is a one-shot indexer; retrieval
lives in downstream code (a notebook, retriever service, or eval baseline).
"""
