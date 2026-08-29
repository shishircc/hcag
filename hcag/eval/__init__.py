"""`eval` CLI — run an evalgen CSV against a live chatbot and score answers.

See DESIGN.md §7 for the full spec. The implementation shells out to
promptfoo for parallel execution + HTML rendering; the multi-turn
conversation loop, LLM-as-judge, and CSV I/O all live in Python.
"""
