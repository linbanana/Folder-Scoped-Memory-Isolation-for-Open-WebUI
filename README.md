# Folder-Scoped Memory Isolation for Open WebUI

Version **0.0.4** — for **Open WebUI v0.12.5**.

A Filter Function that manages persistent user memories within a folder/chat scope. One OWUI model call decides whether to add, replace, remove, or skip memories before generating the answer. No language keyword list or separate extraction API key is required.

## 安裝與設定 / Setup

1. In **Workspace → Functions**, upload `folder_memory_isolation.py`, save it, and enable one instance of the filter.
2. Apply this filter globally, or to every model/chat that needs isolation. Native memory tools in unfiltered chats can still retrieve tagged rows globally.
3. **Leave Settings → Personalization → Memory enabled; there is no need to switch it off manually.** Keep the admin memory service and user memory permissions enabled as well. For each filtered request, the plugin takes over memory handling and disables the native global memory context, tools, and background review to avoid duplicate processing.
4. Leave `memory_model` empty to use the current chat model, or set it to an accessible OWUI model ID. Use a standard server-side OpenAI-compatible or Ollama model. Custom Pipes, arena, and direct-only models need a standard server-side `memory_model`.

在「工作區 → 函式」上傳並啟用插件，設為全域套用。**「設定 → 個人化 → 記憶」維持開啟即可，不需要手動關閉。** 管理員記憶服務與使用者權限也需啟用。插件會接管每次請求的記憶處理，停用該次請求的原生全域記憶流程，避免重複處理。預設沿用聊天模型，也可用 `memory_model` 指定 OWUI 內已設定的模型，不必另填 API 金鑰。

## 行為 / Behavior

| User message | Intended behavior |
| --- | --- |
| 我習慣用繁體中文 / I prefer Traditional Chinese | Automatically save a concise durable preference |
| 我今天吃水餃 / I ate dumplings today | Skip a transient event unless explicitly asked to retain it |
| 記住 / Remember that, after a user statement | Save the referenced user information |
| 我今天吃什麼？ / What did I eat today? | Read scoped memories; do not store the question/answer |
| 刪除此記憶 / Forget that | Remove the clearly identified scoped row through the memory API |
| 改成我偏好 Python / Update that: I prefer Python | Replace the identified fact, preserving its tags/type/path |
| Ambiguous delete/update | Ask for clarification; no mutation |

These are expected model decisions, not hardcoded phrase matches. Results depend on the selected model and require real-server testing.

在回答前完成資料庫操作，再將實際結果注入模型。記憶屬於持久資料，不是僅在本次聊天有效。模型只可根據實際結果說明新增、更新或刪除是否成功；原生記憶工具關閉不代表插件沒有刪除能力。模型回覆文字仍可能不準，驗收請以管理記憶介面的實際資料為準。

## 範圍 / Scope

- A user-owned folder shares that user's memories across chats in that exact folder. Parent/child folders are separate.
- All root chats for the same user share one global pool: all memories without a folder tag. `C_ID` records the source chat only; it does not restrict root-chat retrieval.
- The saved chat record is authoritative; client folder IDs do not override it.
- Temporary, unsaved, unavailable, unauthorized chats and non-owned shared folders skip memory operations.
- Moving a chat changes future scope. Existing rows retain their original scope; they are not automatically migrated.
- Models see only candidates from the validated scope. Mutation IDs are checked against a fresh scoped read before execution. A move during model planning cancels processing.

## Valves

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | True | Enable the service |
| `intercept_core` | True | Required for isolation; False bypasses all behavior |
| `priority` | 100 | Run after ordinary inlet filters |
| `memory_model` | empty | OWUI decision model ID; empty uses the current chat model |
| `memory_max_tokens` | 768 | Decision output budget; truncated output is rejected without writes |
| `extraction_timeout` | 30 | Decision request timeout in seconds |
| `max_inject` | 5 | Most recently updated scoped memories shown in the answer context; 0 disables memory text injection, not management |
| `debug_mode` | False | Show entry/scope/plan/execution stages and concise diagnostic statuses; no memory text or credentials in logs |
| User Valve `show_status` | True | Concise English load/save/update/delete/failure status |

Normal skipped/duplicate operations are quiet. There is no automatic saving in outlet, so assistant responses are not fed back into memory.

For a missing write, temporarily set `debug_mode=True` and User Valve `show_status=True`. A running filter shows `Memory check started (folder; N stored)`, followed by a save/error or `Memory check complete: no changes (N stored)`. Backend `FolderMemory stage=...` logs distinguish entry, scope resolution, proposed/validated operation counts, and applied backend statuses. A missing start status plus no entry log means this code was not invoked; it is not evidence that memory extraction failed. Normal no-change results explicitly tell the answer model that no write occurred and that notes/chat search cannot verify this memory store.

若沒有寫入，暫時開啟 `debug_mode` 與使用者的 `show_status`。請回報出現的英文狀態，或服務端 `FolderMemory stage=...` 日誌；不要只憑模型口頭回答判斷成功與否。尚無實際執行紀錄時，無法斷定是套用、判斷、驗證或資料庫哪一階段出問題。

## Latency

Memory decisions finish before the main answer, adding a model round trip. Choose a fast non-reasoning OWUI model in `memory_model` for short JSON decisions. Provider-specific thinking flags are not guessed. Reasoning can consume the output budget on some models; select a non-reasoning model or raise `memory_max_tokens` when needed. Turning off debug only hides diagnostics; it does not remove the model call.

With debug enabled, `Memory check: 2.3s (model 2.0s; write 0.0s)` reports total plugin time, decision time, and mutation API time (including embeddings/vector work). It excludes the main answer's generation time. Backend logs expose the same timings. Actual speed improvements require measurement on your deployment; simulated tests are not performance benchmarks.

## Conflicts and limitations

- **Other filters/tools:** do not run competing memory filters. A later filter re-enabling native memory, external memory tools, or custom Pipes can bypass isolation. Knowledge retrieval and reference-chat tools have independent scope rules.
- **Persistence API:** add/replace/remove use OWUI's `update_memories` API to coordinate database rows, embeddings/vector removal, and events. Database changes may commit before a vector operation fails; errors are reported as failed/partial, followed by a fresh read. Inspect Manage Memories before retrying.
- **Deletion authorization:** removal/replacement requires a semantic request from the latest user turn. The planner must quote that turn; IDs must belong to the current scope. Ambiguous requests must not mutate data. Semantic authorization still depends on model accuracy; this is not deterministic natural-language understanding.
- **Model inputs:** each turn adds one non-streaming model request with the last six user messages (up to 2,000 characters each) and the latest 20 scoped memory previews (up to 600 characters each). Assistant claims/system prompts are excluded. Older/unseen memories may require manual management.
- **Model output:** the whole operation list is validated before sending it to OWUI: action, scope ID, source index/quotation, evidence from the latest turn, and content length. Validation cannot prove that every model-written fact is true. Invalid output or timeout causes no new planned writes.
- **Duplicates:** exact normalized facts, including `User: ...` rows, are skipped. The planner also checks supplied candidates for semantic duplicates. Simultaneous requests and differently phrased facts can still produce duplicates. Existing duplicates are not automatically removed.
- **Retries:** a repeated inlet rechecks current rows. No durable request ledger is provided; model-generated paraphrases can still differ on a regenerated turn.
- **Tag storage:** `[F_ID:...][C_ID:...]` remains in stored content for compatibility. `F_ID` determines the folder scope; no `F_ID` means the user's shared global pool. `C_ID` records provenance only. These tags are a retrieval convention, not database access control.
- **Deletion of a chat:** does not automatically delete folder-wide memories. Use explicit memory deletion or Manage Memories.
- **Time:** no automatic expiry or short-term memory store is implemented. An explicitly retained transient fact persists until removed; dates should be stated clearly.

## Verification

```powershell
python -B -m unittest discover -s tests -v
git diff --check
```

Local tests simulate planner decisions and OWUI storage APIs. They verify orchestration, scope validation, status/context results and failure handling; they do not establish actual model accuracy or live database/vector integration.

Source references for v0.12.5: [memory API](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/routers/memories.py), [storage operations](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/models/memories.py), [chat generation](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/utils/chat.py), [middleware](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/utils/middleware.py).

### 實機驗收 / Running-server checks

1. In an Open WebUI v0.12.5 test instance, enable the function globally, leave Personalization → Memory enabled, and set an accessible `memory_model` if needed.
2. Say a transient event, then ask to remember it. Confirm `Saved 1 memory` and a row in Manage Memories.
3. Say “delete that memory”. Confirm `Deleted 1 memory`, refresh Manage Memories, and verify the row is absent. A natural-language acknowledgement alone is insufficient.
4. Add a preference and correct it. Verify replacement retains folder tags and changes the existing row rather than adding contradictory facts.
5. Test recall questions, negations, ambiguous deletions, several languages, and a second folder/user. Verify no cross-scope mutation or retrieval.
6. Verify native memory tools/background review stay disabled while unrelated tools still work. Test timeouts and vector failures separately.
