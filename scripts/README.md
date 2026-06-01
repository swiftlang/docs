## Local validation

To build the combined documentation and view the result locally:

- from the docs repository root:
```bash
set -e
./scripts/build_docs.py
python3 -m http.server 8000 --directory .build-output
```

Then in another terminal:

```bash
open http://localhost:8000/main/documentation/
```
