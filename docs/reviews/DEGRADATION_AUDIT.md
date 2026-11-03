# Degradation audit

_2026-11-03_ — This file inventories every broad `except Exception` handler (plus the one `except BaseException`) in `src/agentic_graphrag/`; the final "New handling" column records what the fix did.

Notes on reading the table:

- `File:line` is the **pre-fix** snapshot from the audit grep. Some rows moved when the fix split modules to stay under the 300-line gate (`retrieval/graph.py` helpers → `retrieval/graph_candidates.py`, tool handlers → `agent/executor_tools.py`, ingest stages → `knowledge/index_pipeline.py` / `knowledge/index_graph_stage.py` (the per-chunk checkpointed pipeline from the index-pipeline merge), eval retrievers → `cli/cases_retrievers.py`).
- All recording goes through one helper, `observability.degradation.record_degradation(component, reason, exc, *, severity, health, **attrs)` (log WARNING + `agr_degradation_total{component,reason}` + OTel span event + per-run channel health). `degradation_guard(...)` is the context-manager form used where the handler re-raises.
- `component`/`reason` are low-cardinality slugs; the exception **type** is logged, never `str(exc)` (it carries URLs, document text, credentials).
- Query workers bind the public query ID and tenant/user identity. `observability.context.submit_with_context` copies context separately for every query, tool and DAG-branch task; SSE keeps its own context across iteration and cleanup without leaking it to callers.

| File:line | Type caught | try-body operation | Current handling | Classification | New handling |
| --- | --- | --- | --- | --- | --- |
| `agent/checkpointer.py:105` | `Exception` | build `SqliteSaver(connection)` from a freshly opened sqlite connection | `connection.close()` then re-`raise` | cleanup | unchanged — cleanup + re-raise, the caller fails loudly |
| `agent/critic_offline_rel.py:234` | `Exception` | `parse_edges([c.content])` offline edge parse | `return None` | degradation | `record_degradation("critic", unexpected)`, then `return None` |
| `agent/entity_linking.py:44` | `Exception` | `store.neighbors(...)` degree probe in `_degree` (noqa: BLE001 — a missing node simply has no prior) | `return 0` | degradation | `record_degradation(graph, graph_store_unavailable)`, then `return 0` |
| `agent/entity_mentions.py:34` | `Exception` | `_load_seed_lexicon()` reading `data/processed/seed_triples.jsonl` | `return ()` | degradation | `record_degradation("entity_lexicon", unexpected)`, then `return ()` |
| `agent/executor_dispatch.py:265` | `Exception` | `fut.result()` for a parallel tool call in `_run_parallel` | append `(spec, [], type(exc).__name__)`, continue | degradation | `_record_tool_failure(executor, spec.tool, exc)` → channel marked `failed`; degraded trace kept |
| `agent/executor_dispatch.py:284` | `Exception` | `dispatch(...)` single tool call in `_safe_dispatch` | `return [], type(exc).__name__` | degradation | `_record_tool_failure(executor, spec.tool, exc)`; the retriever's precise reason wins when it recorded first |
| `agent/executor_plan.py:239` | `Exception` | LLM `complete_structured(...)` plan in `_try_llm_plan` | `return None` (fall back to heuristic plan) | degradation | `record_degradation(llm, llm_unavailable, health=executor.health)` marks the run degraded, then `return None` |
| `agent/loop_recover.py:83` | `Exception` | `graph.get_state(invoke_config(thread_id))` checkpoint read | `return None` | degradation | `record_degradation("agent_recovery", unexpected, step="checkpoint_read")` |
| `agent/loop_recover.py:95` | `Exception` | `ReasoningChain.model_validate(state["chain"])` | `pass`, build a fresh `ReasoningChain` | degradation | `record_degradation("agent_recovery", unexpected, step="chain_parse")` |
| `agent/loop_recover.py:107` | `Exception` | `Candidate.model_validate(raw)` per persisted evidence item | `continue` (skip item) | degradation | `record_degradation("agent_recovery", unexpected, step="evidence_parse")` |
| `agent/register_progress.py:63` | `Exception` | `verify(item, evidence, llm)` entailment check (re-raises `BudgetExceeded`) | `return {"entailed": False, "error": ...}` | degradation | `record_degradation("requirement_register", verifier_unavailable)`, same abstention dict |
| `agent/register_runtime.py:124` | `Exception` | `initialize(question, seed, ...)` LLM register init | return fallback single-requirement `Register` + error name | degradation | `record_degradation("requirement_register", llm_unavailable)`, same fallback (except block only) |
| `agent/register_runtime.py:207` | `Exception` | `self._update(...)` register critic update (re-raises `BudgetExceeded`) | `reports = [{"error": ..., "entailed": False}]` | degradation | `record_degradation("requirement_register", unexpected, step="update")`, same reports |
| `agent/triage.py:243` | `Exception` | LLM `complete_structured(...)` triage in `_llm_triage` | `return None` (rule-based triage) | degradation | `record_degradation(llm, llm_unavailable, health=health)` uses the executor's per-run health passed by the pipeline, then `return None` |
| `api/app.py:192` | `Exception` | `svc.bundle.graph.counts()` health probe in `_check_graph` | `checks["graph"]="error:..."`, `return False` | control-flow | unchanged — the probe result is returned to the caller in `/healthz`; counting every probe failure would spam the counter during an outage |
| `api/app.py:201` | `Exception` | access `svc.bundle.vector` probe in `_check_vector` | `checks["vector"]="error:..."` | control-flow | unchanged — same reason |
| `api/app.py:211` | `Exception` | `svc.bundle.graph.ping()` probe in `_check_graph_ping` | `checks["graph_ping"]="error:..."`, `return False` | control-flow | unchanged — same reason |
| `api/app.py:221` | `Exception` | `svc.review_queue.counts()` in `_add_operational_checks` | `pass` | control-flow | `record_degradation("review_queue", unexpected, step="counts")` |
| `api/auth.py:182` | `Exception` | `dotenv_values(".env")` key reload in `_try_reload_key` | `pass` | best-effort | `record_degradation("auth_config", unexpected, source="dotenv")` |
| `api/auth.py:244` | `Exception` | import `logging_setup` + `bind_context(...)` in `_bind_request_context` | `pass` | best-effort | `record_degradation("request_context", unexpected, step="bind")` |
| `api/auth.py:253` | `Exception` | import `logging_setup` + `clear_context()` in `_clear_request_context` | `pass` | best-effort | `record_degradation("request_context", unexpected, step="clear")` |
| `api/auth.py:267` | `Exception` | `emit_audit_event(AUTH_FAILURE, ...)` in `_emit_auth_failure` | `pass` | best-effort | `record_degradation("audit_events", audit_write_failed, severity="failed")` — a lost auth-failure record is a security blind spot |
| `api/auth.py:282` | `Exception` | `emit_audit_event(RATE_LIMITED, ...)` in `_emit_rate_limit` | `pass` | best-effort | same as above |
| `api/routes/knowledge_upload.py:132` | `Exception` | `svc.bundle.docs.save(record)` per uploaded doc (noqa: BLE001) | log error, set `row["error"]="save_failed:..."` | degradation | `record_degradation("doc_store", doc_write_failed, severity="failed", doc_id=...)` (replaces the `logger.error`), `row["error"]` kept |
| `api/routes/knowledge_upload.py:164` | `Exception` | import + `emit_audit_event(...)` in `emit_audit` | `pass` | best-effort | `record_degradation("audit_events", audit_write_failed, severity="failed")` |
| `app/answer_cache.py:77` | `Exception` | `svc.retrieval_cache.set_answer(...)` in `_maybe_cache_answer` | `pass` | best-effort | `record_degradation("answer_cache", cache_write_failed)` |
| `app/query_agent.py:106` | `Exception` | `agent_run_query(...)` query execution in `run_agent` | `raise map_query_exception(exc) from exc` | re-raise | unchanged — mapped to a 500/503 envelope, already visible to the caller |
| `app/query_run.py:105` | `Exception` | reserve budget → run agent → finalize → persist/commit → metrics in `execute_query_chain` | if not settled: `_release_budget(...)`; `raise` | cleanup | unchanged — budget release + re-raise |
| `app/query_run.py:134` | `Exception` | `audit_events.emit_audit_event(BUDGET_EXCEEDED, ...)` in `_budget_api_error` | `pass` | best-effort | `record_degradation("audit_events", audit_write_failed, severity="failed")`; the except variable was renamed `audit_error` so it cannot shadow the `BudgetExceeded` parameter (mypy caught it) |
| `app/query_stream.py:86` | `Exception` | reserve budget + stream agent events in `_stream_live` (finally releases budget) | `yield EVENT_ERROR, {...}` | degradation | `record_degradation("query_stream", unexpected, severity="failed")` before the error frame — previously the server logged nothing |
| `app/telemetry.py:22` | `Exception` | `audit_store.save(chain)` in `save_chain_audit` | `logger.error(...)`, `return False` | best-effort | `record_degradation("audit_store", audit_write_failed, severity="failed", query_id=...)`; `return False` kept |
| `bootstrap/stores.py:32` | `Exception` | `create_bundle(...)` store wiring in `build_stores` (noqa — re-raised with profile context) | `raise ProfileError(...) from exc` | re-raise | unchanged — re-raised with profile context, startup fails loudly |
| `cli/_common.py:65` | `Exception` | open `Neo4jGraphStore(...)` + `store.ping()` in `_try_neo4j` | close store; fall back to in-memory graph (or `sys.exit(1)`) | degradation | `record_degradation(graph, graph_store_unavailable, severity="failed")` before the fallback/exit |
| `cli/_common.py:83` | `Exception` | `store.close()` in `_close_quiet` | `pass` | cleanup | unchanged — teardown path, no user-visible effect |
| `cli/cases_run.py:103` | `Exception` | construct `QdrantVectorStore(...)` + `VectorRetriever` in `_vector_and_llm` | print warning, `return None, llm` | degradation | `record_degradation(vector, vector_store_unavailable, severity="failed")` + same warning; code moved to `cli/cases_retrievers.py` |
| `cli/cases_run.py:157` | `Exception` | `_invoke_case(...)` in `run_case_row` (eval runner) | `return _error_row(case, q, exc)` | control-flow | `record_degradation("eval", case_failed, severity="failed")` + same error row |
| `cli/cases_run.py:290` | `Exception` | `app.run_query_chain(...)` in `run_case_row_via_app` (eval runner) | `return _error_row(case, q, exc)` | control-flow | same as above |
| `core/events.py:149` | `Exception` | `sub.handler(event)` subscriber delivery in `_deliver` (noqa — one bad subscriber must not break writers) | retry loop; log; `handler_failures[...] += 1` | degradation | unchanged **on purpose**: already logged per subscription level and counted (`handler_failures` → `agr_event_handler_failures_total`); `core/` must not import the observability package (rules §6) |
| `core/events.py:164` | `Exception` | `failure_hook(event, error)` fail-safe call in `_fail_safe` | `logger.exception(...)` | degradation | unchanged — same layering rule; logged at ERROR with traceback |
| `generation/answer.py:206` | `Exception` | `_generate_with_llm(...)` answer generation (noqa — degrade, do not 500 the API) | `return _degrade(...)` offline extractive answer (re-raises `BudgetExceeded`) | degradation | `record_degradation(llm, llm_unavailable, error=<ExcType>)` inside `_degrade`; `metadata.llm_degraded` now also yields a `degraded_channels` entry for the `llm` channel |
| `generation/entailment.py:78` | `Exception` | LLM `_ask(claims, evidence, llm)` entailment judge (noqa — abstain on judge error) | log warning; return `("", {JUDGE_ABSTAINED: ...})` (re-raises `BudgetExceeded`) | degradation | `record_degradation(llm, llm_unavailable, step="entailment")` replaces the warning; `_deeper_markers` promotes the existing `JUDGE_ABSTAINED` report into `degraded_channels`, preventing answer caching even when generation succeeds |
| `governance/audit_events.py:264` | `Exception` | `get_audit_event_store().record(event)` in `emit_audit_event` | `logger.exception(...)` | best-effort | unchanged — already logged with traceback at ERROR; **residual gap**: the event is still lost without a counter (see Residual gaps) |
| `knowledge/extract_chunk.py:86` | `Exception` | `_attempt_extract(...)` in `_try_once` (retry policy) | if `retry.should_retry` → `return None` (retry), else `_failed_result(...)` | control-flow | `record_degradation("knowledge_extract", extract_failed, chunk_id=...)`; retry/quarantine behaviour unchanged |
| `knowledge/extract_pipeline.py:216` | `Exception` | `_merge_one_doc(...)` persisting provenance in `persist_doc_provenance` | `continue` | degradation | `record_degradation("doc_provenance", store_write_failed, doc_id=...)`, then `continue` |
| `knowledge/graph_ingest.py:117` | `Exception` | `extract_from_chunk(chunk, schema, llm)` LLM extraction (noqa — one bad chunk must not sink the doc) | log warning; set `result.error`; fall back to `extract_rules_from_chunk` | degradation | `record_degradation("knowledge_extract", extract_failed, chunk_id=...)` replaces the warning; rules fallback kept |
| `knowledge/incremental.py:139` | `Exception` | `self.store.list_relations(...)` in `_iter_store_relations` | log warning; `return []` | degradation | `record_degradation("conflict_index", index_write_failed)`, then `return []` |
| `knowledge/incremental.py:260` | `Exception` | `deleter(old.id, tenant_id=...)` in `_call_deleter` | log warning | degradation | `record_degradation(graph, store_write_failed, severity="failed", relation_id=...)` |
| `knowledge/ingest_worker.py:155` | `Exception` | `graph_ingestor.ingest_chunks(...)` in `_ingest_graph` (noqa: BLE001) | log error; return `{"error": "graph_ingest_failed:..."}` | degradation | `record_degradation("graph_ingest", extract_failed, severity="failed")` + same error dict, per chunk; now `_graph_chunk` in `knowledge/index_graph_stage.py` |
| `knowledge/ingest_worker.py:172` | `Exception` | `embed_fn(chunk.text)` per chunk in `_embed_chunks` (noqa: BLE001) | `failures += 1`; log warning; may return `embed_failed_all` | degradation | `record_degradation(embedding, embedding_unavailable, doc_id=..., chunks=...)` per failed embedding batch in `IndexPipeline._embed_missing`; vector upserts that fail record `vector_index`/`index_write_failed`; incomplete chunks leave the task `partial` instead of `embed_failed_all` |
| `knowledge/ingest_worker.py:188` | `Exception` | `fulltext_store.index(chunks)` in `_index_chunks` (noqa: BLE001) | log warning | degradation | `record_degradation("fulltext_index", index_write_failed, doc_id=...)` in `IndexPipeline._fulltext` |
| `knowledge/ingest_worker.py:236` | `Exception` | `self._process_task_docs(task)` in `_run_task` (noqa: BLE001) | `_finalize(... FAILED ...)`; `return []` | degradation | `record_degradation("ingest", unexpected, severity="failed", task_id=...)` + same finalize |
| `knowledge/ingest_worker.py:290` | `Exception` | `self.run_once()` in the background `_loop` (noqa: BLE001) | log error (loop continues) | degradation | `record_degradation("ingest", unexpected, severity="failed")` replaces the log; loop continues |
| `knowledge/resolution.py:269` | `Exception` | `complete_structured(llm, ...)` merge decision in `_llm_decide` | return `MergeDecision(UNCERTAIN, ...)` | degradation | `record_degradation(llm, llm_unavailable, step="merge")` + same decision (goes to human review) |
| `knowledge/review/executor_ops.py:36` | `Exception` | `graph.delete_relation(rid, ...)` per relation in `delete_relations` (noqa: BLE001) | log warning | degradation | `record_degradation(graph, store_write_failed, severity="failed", relation_id=...)` |
| `llm/provider.py:205` | `Exception` | `httpx` POST + `raise_for_status()` in `_post` | `breaker.record_failure()`; `raise` | re-raise | unchanged — the per-route circuit breaker records it and the error reaches the caller |
| `observability/otel_bridge.py:78` | `Exception` | `_TRACER.start_as_current_span(...)` span in `otel_span` (noqa — never break app on OTel failure) | `yield None` | best-effort | **narrowed** to tracing operations: `ExitStack.enter_context` guards construction and lazy span entry; exit errors are suppressed separately. Application exceptions retain their original type and instance, including when cleanup fails. No counter: tracing is optional and `record_degradation` itself uses OTel |
| `observability/otel_bridge.py:97` | `Exception` | `propagate.extract(...)` + `context.attach(...)` in `extract_otel_context` (noqa: BLE001) | `yield None` | best-effort | unchanged — recording from here would recurse into the OTel path it guards |
| `observability/otel_bridge.py:111` | `Exception` | `propagate.inject(out)` in `inject_trace_context` (noqa: BLE001) | `return headers` | best-effort | unchanged — same reason |
| `retrieval/graph.py:133` | `Exception` | `scorer(rel.type, sub_question)` embed-sim in `_score_edge` (noqa — lexical fallback) | `sim = None` | degradation | `record_degradation(embedding, relation_embed_unavailable)` **once per run** (per-edge logging flooded the log); the call now goes through the guarded `BeamExpander.relation_sim` |
| `retrieval/graph_beam.py:132` | `Exception` | `scorer(relation_type, sub_question)` in `_embed_sim` (noqa — never break retrieval on embed failure) | `return None` | degradation | same record, once per expansion (`_note_embed_failure`); lexical fallback kept |
| `retrieval/rerankers.py:86` | `Exception` | `self._score(...)` HTTP cross-encoder in `rerank` (noqa — degrade to fused order) | `self.fallbacks += 1`; log warning; return fused order | degradation | `record_degradation(rerank, rerank_unavailable)` replaces the warning; `fallbacks` counter and `metadata.rerank_fallback` tag kept |
| `stores/factory.py:161` | `Exception` | `create_vector_store(...)` in `create_bundle` | close graph (`graph.close()`); `raise` | cleanup | unchanged — cleanup + re-raise, no implicit fallback by design |
| `webapp/accounts.py:112` | `Exception` | `mail.send(...)` password-reset email in `forgot_password` | log error (response stays generic) | degradation | `record_degradation("mail", mail_delivery_failed, severity="failed")`; the generic public response is unchanged on purpose |
| `webapp/database.py:49` | `BaseException` | `yield session` + `session.commit()` in `transaction` | `session.rollback()`; `raise` | cleanup | unchanged — rollback + re-raise |
| `webapp/routes_invites.py:101` | `Exception` | `ctx.mail.send(...)` invite email in `_send` | log warning | degradation | `record_degradation("mail", mail_delivery_failed)` |

## New broad handlers added by this fix

| File:line | Operation | Why broad | Handling |
| --- | --- | --- | --- |
| `observability/degradation.py` `degradation_guard` | wrapped block | called from inside `except` blocks | `record_degradation(...)` then re-raise |
| `observability/degradation.py` `_log` / `_count` | structured log line / metrics increment | a recorder must never break the caller | swallow (log one line, keep going) |
| `retrieval/vector.py` `_embed` / `_search_store` | `llm.embed(query)`, `store.search(...)` | 401s and unreachable Qdrant | record `embedding`/`vector` (`failed`) with the real reason, then re-raise |
| `retrieval/fulltext.py` `_search_store` | `store.search(...)` | unreadable BM25 index | record `fulltext` (`failed`), then re-raise |
| `observability/otel_bridge.py` `otel_span` | span context construction, entry and exit | optional dependency | yield `None` after failed entry; suppress tracing cleanup failures without suppressing application exceptions |

## Totals

| Classification | Count |
| --- | --- |
| degradation | 37 |
| best-effort | 13 |
| control-flow | 7 |
| cleanup | 5 |
| re-raise | 3 |
| **Total** | **65** |

Of those 65, **48** now record through `record_degradation` / `degradation_guard` / `_record_tool_failure`; **16** are deliberately unchanged, each for a reason stated in the table (re-raise, cleanup/teardown, probe results already returned to the caller, the `core/` layering rule, or "recording here would recurse into OTel"); and **1** (`observability/otel_bridge.py:78`) is *narrowed* rather than recorded — tracing operations are guarded separately from application code, so application exceptions propagate unchanged.

## Residual gaps (not fixed here)

- `governance/audit_events.py:264` — a lost audit event is logged with a traceback but not counted; it needs its own counter before anyone can alert on audit loss.
- `api/app.py:192/201/211` — the three `/healthz` probes surface their failure in the response body but are not counted, so a long outage is invisible in `agr_degradation_total` unless Prometheus scrapes `/healthz` separately.
- Live-only channels (`neo4j`, `qdrant`) are reported through `vector` / `graph`; there is no separate per-backend label. Adding one would raise label cardinality for little operator gain.
