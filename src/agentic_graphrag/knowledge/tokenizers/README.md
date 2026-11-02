`cl100k_base.tiktoken` is the public tiktoken vocabulary from
https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken.

SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
Downloaded 2026-11-03. The encoder pattern and vocabulary use tiktoken's MIT
license, reproduced in `LICENSE`. Bundling avoids a network/cache dependency on
the first offline ingestion run. The loader checks the hash before use.

`knowledge.tokenizer` defaults to `cl100k_base`. To use another offline encoder,
set it to a project-relative (or absolute) JSON file containing `name`, `pat_str`
and `ranks_path`. `ranks_path` names a local `.tiktoken` vocabulary relative to
that JSON file. For example:

```json
{"name": "custom", "pat_str": "(?s).+", "ranks_path": "custom.tiktoken"}
```

Use the chosen encoder's actual pre-tokenization regex and vocabulary. Special
token strings in source documents are always encoded as ordinary text.
