# Learnings
- Python 3.13+ removed `cgi`. A raw-body upload with an `X-Filename` header avoids hand-written multipart parsing.
- When rejecting an oversized upload, drain the body (with a cap) before responding. Otherwise the client sees a broken pipe instead of the 413 message.
- A default argument like `sleep=time.sleep` is bound when the function is defined, so tests cannot patch it. Look it up at call time instead.
- Live check: three real public APIs returned 873 postings. The first uncached search took 29.2 s, almost all of it network time (a cached repeat took 3.4 s). Large `content=true` board payloads dominate. A future fix is a per-source page-size or description cap and streaming first results.
- Most large MNCs do not expose public ATS board APIs, so a licensed aggregator is the only compliant way to reach them.
