# LLM_Proxy Refactor Plan

Last updated: 2026-03-19

## 1. Background

The current proxy is centered around one assumption:

- upstream streaming responses are mostly SSE
- downstream output should be OpenAI-compatible
- request/response format adaptation is implemented by user hooks

This worked for simple integrations, but it creates several structural limits:

- streaming parsing is tightly coupled to `data:` SSE chunks
- HTTP and WebSocket adaptation are normalized too early into one output shape
- provider protocol adaptation and user policy logic are mixed in `input_body_hook` / `output_body_hook`
- it is hard to support multiple upstream stream formats cleanly
- hook behavior is powerful but underspecified, making testing and evolution difficult

The next refactor should be guided by the architectural direction used in `CLIProxyAPI`:

- separate transport execution from format translation
- process streaming responses incrementally instead of assuming one global parser
- keep provider adaptation as first-class internal components
- keep user customization as an explicit extension boundary

## 2. Hard Constraints

These constraints are part of the refactor baseline:

- database tables must remain unchanged
- compatibility for provider configuration, hook API, and internal module layout is not required
- documentation must be updated together with implementation
- README and `docs/architecture-4plus1.md` must be treated as required deliverables, not optional follow-up work
- bold refactoring is allowed if it makes the core pipeline simpler and more extensible

## 3. Refactor Goals

### 3.1 Core goals

- support multiple upstream response formats, not only `data:` SSE
- make streaming processing event-driven and incremental
- separate provider protocol adaptation from user-defined logic
- keep user customization for:
  - request header mutation
  - request guard
  - response guard
- make HTTP and WebSocket go through one conceptual pipeline, while preserving protocol-specific boundaries where needed
- make tests target stream decoding, translation, and guards as separate concerns

### 3.2 Non-goals for phase 1

- preserving the old `input_body_hook` / `output_body_hook` user API
- preserving exact current provider config field semantics
- preserving current internal package boundaries
- adding more database-backed features

## 4. Target Architecture

The refactor should replace the current "controller -> proxy service -> hook -> response adapter" shape with the following pipeline.

```text
Client Request
  -> Request Normalizer
  -> User Header Hook
  -> User Request Guard
  -> Provider Request Translator
  -> Upstream Executor (HTTP / WebSocket)
  -> Stream Decoder
  -> Provider Response Translator
  -> User Response Guard
  -> Downstream Encoder
  -> Client Response
```

### 4.1 New responsibility split

- `User Header Hook`
  - user-owned
  - mutates outbound headers only
  - no protocol conversion responsibility

- `User Request Guard`
  - user-owned
  - runs on normalized request objects
  - for compliance checks, blocklists, prompt rewriting, guardrail injection

- `Provider Request Translator`
  - built-in, provider-owned
  - converts normalized downstream request into upstream provider request shape

- `Upstream Executor`
  - transport-owned
  - sends HTTP or WebSocket requests
  - yields raw response body or incremental stream units

- `Stream Decoder`
  - transport/format-owned
  - decodes upstream stream units into typed events
  - must support at least:
    - SSE JSON
    - SSE text
    - NDJSON
    - raw text chunk
    - WebSocket JSON message
    - WebSocket text message

- `Provider Response Translator`
  - built-in, provider-owned
  - converts typed upstream events or full bodies into OpenAI-compatible downstream objects

- `User Response Guard`
  - user-owned
  - runs after provider translation, before downstream encoding
  - for filtering, redaction, blocking, policy rewriting

- `Downstream Encoder`
  - protocol-owned
  - emits final downstream HTTP response
  - supports:
    - non-stream JSON
    - stream response compatible with downstream OpenAI clients

### 4.2 Key design principle

Provider adaptation and user policy must never be the same abstraction again.

That means:

- provider translation is internal product logic
- user guard/header extensions are external customization logic
- stream decoding is infrastructure logic

## 5. Proposed Module Layout

This is a target shape, not a strict final tree:

```text
src/
  proxy_core/
    contracts/
    pipeline/
    errors/
  proxy_transport/
    http_executor.py
    websocket_executor.py
  proxy_stream/
    decoders/
    events/
    encoders/
  proxy_providers/
    registry.py
    adapters/
  proxy_extensions/
    loader.py
    contracts.py
    sandbox.py
  presentation/
  services/
  repositories/
```

### 5.1 Suggested core contracts

- `NormalizedRequest`
- `UpstreamRequest`
- `UpstreamResponse`
- `StreamEvent`
- `TranslatedEvent`
- `GuardDecision`
- `ProxyError`

### 5.2 Suggested extension contracts

User extension API should become explicit and small:

- `header_hook(ctx, headers) -> headers`
- `request_guard(ctx, request) -> request | GuardAbort | GuardDecision`
- `response_guard(ctx, response_or_event) -> response_or_event | GuardAbort | GuardDecision`

Notes:

- user extension API should not directly own provider translation
- request and response guards must work for both streaming and non-streaming modes
- stream mode should support guard execution per event, not only after the full response completes

## 6. Streaming Strategy

This is the most important architectural change.

### 6.1 Current limitation

The current implementation assumes one main path:

- detect SSE
- split by event boundary
- parse `data:`
- optionally mutate JSON payload

This is not enough for multi-vendor compatibility.

### 6.2 New strategy

Each executor should emit raw upstream units, then a decoder should transform them into typed events.

For HTTP:

- non-stream: return full body
- stream: yield raw byte chunks, then decode according to configured or detected format

For WebSocket:

- yield one message at a time
- preserve message boundaries at the decoder layer

### 6.3 Decoder modes to support first

- `sse_json`
- `sse_text`
- `ndjson`
- `raw_text`
- `ws_json`
- `ws_text`

### 6.4 Translator behavior

The provider translator should be allowed to:

- ignore some upstream events
- map one upstream event to one downstream event
- map one upstream event to multiple downstream events
- accumulate limited local state across stream events

This is the part most directly inspired by `CLIProxyAPI`.

## 7. Provider Model

The new provider configuration model should be simplified around explicit strategy selection.

Suggested fields:

- `name`
- `models`
- `transport`
- `api`
- `auth`
- `request_format`
- `response_format`
- `decoder`
- `adapter`
- `extension`
- `timeout_seconds`
- `max_retries`
- `verify_ssl`
- `proxy`

Notes:

- this may replace the current "one hook does everything" mental model
- provider compatibility with old config is not required
- built-in adapters should be the default path

## 8. Migration Plan

### Phase 0. Freeze and planning

- document the new architecture and extension boundaries
- define the first supported decoder and adapter matrix
- confirm which downstream API surface remains in scope for this iteration

Deliverables:

- this plan document
- updated implementation backlog

### Phase 1. Core contracts and pipeline skeleton

- create core proxy contracts and error model
- introduce executor / decoder / translator / guard interfaces
- keep existing routes, but route them through the new pipeline shell

Deliverables:

- new core packages
- typed contracts
- pipeline orchestration tests

### Phase 2. Transport executors

- extract current HTTP upstream logic into an HTTP executor
- extract current WebSocket upstream logic into a WebSocket executor
- standardize retry, timeout, and upstream error reporting

Deliverables:

- executor tests
- unified upstream error model

### Phase 3. Stream decoders

- implement SSE JSON decoder
- implement SSE text decoder
- implement NDJSON decoder
- implement raw text chunk decoder
- implement WebSocket JSON/text decoders

Deliverables:

- decoder fixtures
- chunk-boundary tests
- UTF-8 split tests

### Phase 4. Provider translators

- replace current `input_body_hook` / `output_body_hook` adaptation path with provider adapters
- implement one built-in OpenAI-compatible adapter first
- add one non-trivial adapter path to validate the architecture

Deliverables:

- provider adapter registry
- stream and non-stream translator tests

### Phase 5. User extensions

- introduce new user extension contracts:
  - header hook
  - request guard
  - response guard
- add extension loading rules, examples, and failure handling
- remove or deprecate the old generic body hooks

Deliverables:

- extension loader
- sample user extension files
- extension error and logging strategy

### Phase 6. Presentation and config cleanup

- simplify controller responsibilities
- reduce protocol knowledge in presentation layer
- redesign provider management UI and config schema around adapters + extensions

Deliverables:

- updated admin flows
- revised config templates

### Phase 7. Documentation and architecture refresh

- rewrite README to describe:
  - new architecture
  - supported stream formats
  - extension model
  - provider adapter model
- rewrite `docs/architecture-4plus1.md`
- add migration notes for old hook users

Deliverables:

- README updated
- 4+1 architecture document updated
- examples updated

### Phase 8. Cleanup and cutover

- remove dead compatibility layers
- remove obsolete hook assumptions
- tighten tests around final contracts

Deliverables:

- cleaned module tree
- stable final tests

## 9. Documentation Work Required

Every implementation phase must update the matching documentation.

At minimum, the final refactor must update:

- `README.md`
- `docs/architecture-4plus1.md`
- provider examples in `config.sample.yaml`
- hook or extension examples under `hooks/`

The README should stop describing the system as primarily a generic hook-based body rewrite proxy.

The 4+1 architecture document should reflect:

- executor / decoder / translator / guard split
- the new extension boundary
- the new stream processing model

## 10. Testing Plan

The refactor is not complete without a new test strategy.

Required test layers:

- contract tests for core pipeline
- decoder tests with fragmented chunks
- translator tests for stream and non-stream paths
- guard tests for block / rewrite / pass-through
- executor tests for retry and upstream error behavior
- end-to-end API tests for `/v1/chat/completions`

Special attention:

- UTF-8 characters split across chunks
- mixed SSE event fields
- non-SSE upstream stream formats
- WebSocket multi-message responses
- guard interruption during streaming

## 11. Risks

- phase 1 and phase 2 may temporarily increase code duplication
- replacing generic hooks with adapters and guards will break current custom integrations
- stream abstraction may become overdesigned if too many formats are introduced at once
- admin UI and provider config editing will need a coordinated redesign

Mitigation:

- implement one complete vertical slice early
- keep the first supported decoder matrix intentionally small
- favor explicit contracts over highly dynamic hook magic

## 12. Recommended First Execution Slice

The first real implementation slice should be:

- keep downstream `POST /v1/chat/completions`
- support upstream HTTP transport
- support `sse_json`, `ndjson`, and non-stream JSON
- introduce:
  - `header_hook`
  - `request_guard`
  - `response_guard`
- add one built-in provider adapter
- remove the assumption that all streaming data is `data:`

Reason:

- this slice proves the new architecture without having to finish every protocol at once
- it gives the highest return on the current pain point

## 13. Definition of Done

This refactor is considered done only when all of the following are true:

- provider adaptation no longer depends on generic user body hooks
- user customization is explicitly limited to header hook and guards
- multi-format streaming is supported through decoders
- HTTP and WebSocket both use the new pipeline model
- README and 4+1 architecture documentation are updated
- database tables remain unchanged
- old SSE-only assumptions are removed from the core design
