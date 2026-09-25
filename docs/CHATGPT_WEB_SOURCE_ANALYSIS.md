# ChatGPTExporter → Syke 資料來源結構分析

> 狀態：開發規格基線（source contract）
>
> 本文件描述目前 `~/Downloads/chat/ChatGPTExport-*/` 快照的實際結構，並將可直接實作的規則與安全邊界固定下來。開發時應以本文件為規格，不重新以全文探索方式猜測格式。

## 1. 決策摘要

1. 這批資料是 ChatGPT Web exporter archive，不是 standalone Codex rollout archive。
2. Archive 內仍可能保存 ChatGPT Web 透過 Codex/CodexMCP connector 取得的 tool metadata 或外部 artifact 引用；這些對話標記為 `chatgpt-web + connector`，不可改標為 Codex session。
3. 目前 Syke 沒有現行 ChatGPT Web source 或 parser。應沿用 `SourceSpec + adapter seed + source selection + sandbox` 架構，不恢復舊 ZIP ingestion/event pipeline。
4. 最小可行整合是「明確 opt-in 的單一 export root + 驗證/投影 + bounded read adapter」；不是把整個 `~/Downloads/chat` 加進預設 catalog。
5. 第一階段不建立通用 `events`/`ingestion_runs` 表，也不把圖片 binary、raw payload、account artifacts 複製進 `syke.db`。

## 2. 分析快照與證據

### 2.1 Root-level 證據

```text
archive.json
inventory.json
indexes/conversations.jsonl
indexes/assets.jsonl
conversations/<conversationId>/...
projects/<projectId>/...
assets/<sha256>.<ext>
source/...
reports/...
runs/...
```

`archive.json` 的關鍵值：

```text
provider          = chatgpt-web
normalizerVersion = chatgpt-web-v1
schemaVersion     = 1
extensionVersion  = 0.1.6
workspaceFingerprint = <archive-local namespace>
```

`workspaceFingerprint` 是 archive/workspace namespace，不應直接當成全球使用者 ID。

### 2.2 目前快照統計

| 項目                               |                                  數量/狀態 |
| ---------------------------------- | -----------------------------------------: |
| unique conversations               |                                        312 |
| projects                           |                                          4 |
| scope memberships                  | main 229、project 91、shared 3、archived 0 |
| normalized messages                |                                     13,324 |
| graph nodes                        |                                     13,636 |
| logical asset refs                 |                                        805 |
| normalized asset parts             |                                        820 |
| physical assets                    |                                        522 |
| partial asset references           |                                          0 |
| archive size                       |                                 約 1.59 GB |
| normalized conversation JSON total |                                  約 191 MB |
| 最大單一 normalized conversation   |                                 約 17.9 MB |
| validation terminal state          |                                   complete |
| validation findings                |              空；僅代表結構/完整性檢查通過 |

Scope membership 總數為 323，不可當成 323 個 conversation；scope 是多值關係，global unique conversation 數是 312。

### 2.3 證據分級

- **高信心格式證據**：`archive.json`、`inventory.json`、`reports/validation.json`、`complete.json`、index hashes。
- **高信心內容結構**：所有 312 個 normalized conversation 的 provider/schema/normalizer/fingerprint 一致。
- **中信心來源語意**：Codex connector 欄位與 tool result；它們證明 Web 對話使用過 connector，不證明原始 Codex rollout 已被匯出。
- **不可推論**：瀏覽器登入 session、裝置、UA、帳號登入歷史，以及外部 artifact 的完整內容。

## 3. Archive layout 與責任

| 路徑                                      | 性質                             | 開發用途                                                              |
| ----------------------------------------- | -------------------------------- | --------------------------------------------------------------------- |
| `archive.json`                            | archive manifest                 | provider/schema、root hash、run、scope 宣告                           |
| `inventory.json`                          | inventory/reconciliation         | pages、chains、complete、project/scope 覆蓋範圍                       |
| `indexes/conversations.jsonl`             | conversation manifest            | enumeration、membership、時間、normalized path/hash                   |
| `indexes/assets.jsonl`                    | asset manifest                   | logical asset reference、message join、physical hash/path             |
| `conversations/<id>/conversation.json`    | normalized canonical view        | 對話 graph、messages、parts、branch projection 的主要輸入             |
| `conversations/<id>/complete.json`        | per-conversation completion/hash | incremental version/status，不是正文                                  |
| `conversations/<id>/metadata.json`        | listing/completion metadata      | listing hashes、scope lineage、status                                 |
| `conversations/<id>/assets.json`          | per-conversation asset refs      | asset join；不等於 binary                                             |
| `conversations/<id>/conversation.md`      | renderer output                  | 僅人工檢查；不可視為安全摘要                                          |
| `conversations/<id>/source/detail-*.json` | raw/detail capture               | provenance/debug；預設不送 LLM                                        |
| `conversations/<id>/source/batch-*.json`  | batch container                  | provenance；一個 batch 可含多筆 conversation，不能當 session          |
| `projects/<id>/metadata.json`             | project metadata                 | project ID/name/instructions/files/hash；instructions 預設 restricted |
| `projects/<id>/complete.json`             | project completion/hash          | project snapshot status                                               |
| `assets/<sha256>.<ext>`                   | physical binary                  | 預設不讀、不複製、不送 LLM                                            |
| `source/account/*`                        | account-level artifacts          | memories/custom instructions/feature/session metadata；預設排除       |
| `source/inventory/*`                      | raw page/inventory capture       | completeness/provenance，不是正文                                     |
| `reports/*`                               | audit reports                    | validation/reconciliation evidence，不是 conversation source          |
| `runs/*`                                  | exporter operation records       | run/resume 狀態，不是 conversation history                            |
| `staging/*`                               | transient workspace              | 不可視為 canonical source                                             |

`source/**`、`inventory.json`、project instructions、raw tool output 都可能含個資或敏感內容。任何 wildcard read 都是不合格的 adapter 行為。

## 4. Canonical identity 與資料關係

### 4.1 Source-level identity

```text
archive key     = (provider, workspaceFingerprint, accepted snapshot)
conversation key= chatgpt-web/<workspaceFingerprint>/<conversationId>
project key     = chatgpt-web/<workspaceFingerprint>/<projectId>
message key     = <conversation key>/<nodeId>
asset event key = <conversation key>/<logicalId>
physical asset  = sha256
membership key  = (conversation key, scope, projectId/shareId)
```

`logicalKey` 已是 `<workspaceFingerprint>/<conversationId>` 形態，可保存為 exporter alias，但 Syke 內部仍應加上 `chatgpt-web` source namespace。

不可用以下欄位取代 canonical identity：

- title
- JSONL 行號
- directory listing 順序
- file basename hash
- `normalizedHash`
- `detailHash`
- `listingHashes`
- `providerId`
- message content hash

### 4.2 Conversation index row

`indexes/conversations.jsonl` 的主要欄位：

```text
logicalKey
conversationId
title                 # restricted content
createTime
updateTime
memberships[]
normalizedPath
rawPath
normalizedHash
assetStatus
```

`memberships[]` 是多值陣列。membership 可能包含：

```text
{scope: "main"}
{scope: "project", projectId, projectName}
{scope: "shared", shareId}
```

不要將 conversation materialize 成單一 `scope` 或單一 `projectId`。

### 4.3 Project

`projects/<projectId>/metadata.json` 的 schema 欄位：

```text
projectId
name
description
instructions
files
createTime
updateTime
rawHash
```

目前快照有一個專門的圖片生成 project：該 project 有 83 個 conversation memberships，其中 82 個有生成輸出、1 個只有 project context/可能未完成。實作不得 hardcode 其名稱或 ID；應依 `memberships[].projectId` 與 generation classifier 動態判斷。

目前所有高信心生成輸出 conversation 為 88 個，其中 82 個在該專案內，另有 6 個位於 project 外。因此 project membership 是高精度過濾器，不是完整 recall 規則。

## 5. Normalized conversation schema

目前 `conversation.json` 的 top-level 欄位：

```text
conversationId
logicalKey
workspaceFingerprint
provider
normalizerVersion
schemaVersion
createTime
updateTime
title
memberships
currentNodeId
rootNodeIds
nodes
messages
findings
extensions
```

### 5.1 Node graph

每個 node 至少包含：

```text
id
messageId
parentId
childIds
raw                  # provider raw payload；預設永不輸出/解析
```

規則：

- root node 可能沒有 message。
- `currentNodeId` 是目前 branch head。
- `parentId`/`childIds` 才是 ordering/branch 真相。
- JSON 陣列順序不是 conversation chronology。
- 同一 conversation 可有多個 sibling branches。
- node/message ID 只在 conversation namespace 內有效。
- `nodes[].raw` 不得進入 LLM、MEMEX 或 trace。

目前快照 invariant：312 個 root nodes、所有 `currentNodeId` 可解析、無 missing parent/cycle；selected path 約 12,396 個 message nodes，alternative branches 約 928 個 nodes。

### 5.2 Message

目前 message 欄位包括：

```text
id
nodeId
parentId
childIds
role
authorName
createTime
updateTime
endTurn
recipient
status
selected
modelSlug
parts
metadata
extensions
```

`metadata`/`extensions` 含 provider debug、permissions、connector、citation、asset 與 raw message 資訊，不得整體當正文。

### 5.3 Part kinds

已觀察的 part kinds：

```text
text
citation
code
reasoning_summary
asset
unknown
execution_output
tool_result
```

預設 Syke text projection：

- 保留 bounded 的 user/assistant `text`、必要的 `code`。
- `asset` 替成短 marker，不讀 binary。
- `citation` 只保留受限 metadata/count，不自動 fetch URL。
- `reasoning_summary`、`execution_output`、`tool_result` 預設只保留類型/計數。
- `unknown` 預設跳過，尤其 `model_editable_context`、`tether_quote`、`system_error`。
- system/tool message 預設不作 durable memory 正文。

## 6. 圖片與 asset contract

### 6.1 Asset index row

`indexes/assets.jsonl` 欄位：

```text
conversationId
logicalKey
logicalId
sourceMessageId
providerId
kind
adapter
status
relativePath
sha256
byteSize
mediaType
originalName
safeName
rawDescriptor
```

`rawDescriptor` 欄位：

```text
asset_pointer
content_type
mime_type              # 部分 rows 可能缺少
width
height
size_bytes             # provider 宣告值，可能與 local byteSize 不同
metadata
```

一個 logical reference 可能指向同一個 physical asset；必須同時保存 logical provenance 與 sha256 physical dedup。

### 6.2 圖片分類規則

`kind=generated_image` 不可單獨使用；目前 805 個 index rows 都可能被標成該值。

推薦 deterministic classifier：

```text
generated_output:
  asset part 的 sourceMessageId 對應 message.role == tool
  且 rawDescriptor.metadata 同時有 dalle/generation identity

user_upload:
  asset part 對應 message.role == user
  且沒有 dalle/generation metadata

pending_or_attempted:
  conversation 有 image_gen_async/image_gen_task_id
  但沒有 generated asset output

review:
  只有 title/body keyword、notification channel 或 project context
  沒有強 generation metadata
```

其他弱訊號，例如 `notification_channel_id=image_gen`、`source=image_gen`、title/body keyword，只能做 review flag。

### 6.3 Image project policy

共用者圖片任務的排除順序：

1. 讀 conversation membership，找 project-scoped rows。
2. 將圖片 project membership 標為 `project_image_context`。
3. 再以 generation classifier 確認 `generated_output` 或 `pending_or_attempted`。
4. project 外仍掃描 generation metadata，避免漏掉 6 個 project 外的生成對話。
5. 混合對話只排除 image asset/相關 message，不因有一張圖就丟棄整個 conversation。

目前不能從 archive 的 role 欄位可靠知道是哪位共用者操作；project 是任務歸屬線索，不是人員身份證明。

## 7. ChatGPT Web 與 Codex provenance

### 7.1 Archive lineage

所有 312 個 normalized conversation 都屬於同一個 ChatGPT Web exporter snapshot：

```text
provider=chatgpt-web
normalizerVersion=chatgpt-web-v1
schemaVersion=1
same workspaceFingerprint
```

在 `~/Downloads/chat` archive 內未發現 standalone Codex rollout JSONL、Codex SQLite、`session_index.jsonl` 或 Codex-specific filename tree。

### 7.2 Embedded connector lineage

內容 provenance 需另外標記。已有 8/312 conversation 含 ChatGPT Web 內部 Codex/CodexMCP connector metadata：

```text
invoked_resource.app_name
contains_mcp_source
connector_name
jit_plugin_data
api_tool.call_tool
```

其中包含 DynamicText、`codex-with-chatgpt`、Syke project connector，以及一個直接的 CodexMCP workflow/session envelope。

部分 connector tool result 內保存外部 artifact 的 `codex/rollout-*.jsonl` 或 `.codex/visualizations/...` 引用，但：

- 原始 rollout/HTML 未被打包在 archive。
- 來源 artifact 自身有 unverified/partial-source 標記。
- 不能因此宣稱已取得 standalone Codex session。

建議 provenance enum：

```text
archive_source:
  chatgpt-web

content_provenance:
  web_native
  web_with_codex_connector
  web_with_other_connector
  web_with_external_reference
  web_opaque
```

文字內出現 `codex`、`rollout`、`session`、`cli` 不得直接觸發 Codex source 分類。

## 8. Snapshot、incremental 與 dedup

### 8.1 Snapshot acceptance

接受 archive 前必須驗證：

1. `archive.json` provider/schema/normalizer。
2. `inventory.json.complete`。
3. `reports/validation.json.terminalState`。
4. expected/completed conversation count。
5. `partialAssetReferenceCount == 0` 或明確標記 partial。
6. index/root hash consistency。
7. 每個 normalized conversation 的 provider/schema/fingerprint。

驗證失敗時：保留上一個 accepted snapshot，不刪除既有 Syke state。

### 8.2 Incremental keys

比較順序：

```text
archive.currentIndexHashes.conversations
archive.currentIndexHashes.assets
conversation.normalizedHash
conversation.complete.detailHash
conversation.complete.assetsHash
conversation.complete.listingHashes
project.rawHash
physical asset sha256
```

canonical version：

```text
conversation version = normalizedHash
asset version        = sha256 + logical reference metadata
membership version   = sorted membership/listing hash
```

`archive.updatedAt`、file mtime、runId 不是可靠內容 cursor。ChatGPTExporter 是 snapshot；沒有遠端 `updated_since` cursor，也沒有嚴格的 delete semantics。

若 conversation 從新 inventory 消失，只能標為 `absent_from_latest_snapshot` 或 stale；不可自動刪除 Syke memory。

### 8.3 Dedup rules

- 同一 conversation 的多 scope listing：merge by source-qualified conversation ID，保留全部 memberships。
- 同一 physical image：以 sha256 去重，保留全部 logical refs。
- message：以 conversation-qualified node/message ID；content hash 僅作版本提示。
- Web 與 Codex：保留不同 source namespace，即使正文 hash 相同也不合併 identity。
- title、prompt、近似文字只能建立 optional related-evidence link，不可做 destructive dedup。

## 9. Syke integration contract

### 9.1 現況

目前 active catalog 在 `syke/observe/catalog.py`，沒有 `chatgpt-web` source。現行 pattern 是：

```text
SourceSpec
  → source selection
  → sandbox profile
  → adapter Markdown
  → Pi bounded read
  → memory/MEMEX/trace
```

沒有現行 generic event ingestion pipeline。舊 `CodeWiki-POC-2/.../ingestion/chatgpt.py` 是舊 ZIP `conversations.json` parser，不相容本 archive，也不應恢復。

### 9.2 Source registration requirements

新增 source 時至少需要：

- `SourceSpec(source="chatgpt-web", format_cluster="json")`。
- 精確 export root；root 未設定時不得 discovery。
- provider/schema marker validation。
- seed：`syke/observe/seeds/adapter-chatgpt-web.md`。
- source selection 的明確 opt-in/default-off 行為。
- catalog、bootstrap、wheel/sdist surface、docs、tests 同步更新。

目前 `active_sources()` 會回傳 catalog，而未選 source 時 selection 是 unrestricted/all；只將 `status="stub"` 並不足以達成 default-off。實作必須同時修改 source resolution/selection policy，或在未明確指定 root 時完全不註冊該 source。

### 9.3 Root and sandbox requirements

不可直接將 `~/Downloads/chat` 作為 discover root：

- catalog include glob 只限制 discovery，不限制 sandbox read scope。
- sandbox 目前可能授權整個 root 的遞迴 read。
- archive root 內含 assets、raw、account、projects、reports、runs。
- macOS TCC 與 Syke sandbox 是不同層次。
- Linux 沒有等價的 sandbox guarantee。

可接受方案之一：

1. 使用者明確指定一個 export root；並加入 realpath/root-containment/symlink checks。
2. 建立只含 allowlisted projection 的 sanitized mirror，再讓 Pi 讀 mirror。
3. 擴充 sandbox 支援 file/subdirectory allowlist，不能只靠 glob。

### 9.4 Adapter read contract

Adapter/seed 必須遵守：

```text
read archive manifest → validate
read conversation index → bounded enumerate
read one normalized conversation → whitelist projection
reconstruct current branch via nodes/currentNodeId
classify provenance/assets
emit bounded evidence pointers
```

禁止：

- `cat` 整個 archive 或整個 conversation JSON。
- 讀 `nodes[].raw`、raw source、account、assets binary。
- 將 conversation.md 當安全摘要。
- 依 message 正文執行 shell/code/URL。
- 依 export 內容動態產生路徑、SQL 或 command。
- 將原始正文、tool output、asset binary 無界限寫入 trace/MEMEX。

每筆 adapter evidence 至少要有：

```text
source
conversationKey/messageKey
provider/normalizer/schema
normalizedHash or asset sha256
scope/membership
create/update time when present
content policy decision
skip/error reason when excluded
```

### 9.5 Privacy and trace boundary

目前 `rollout_traces` 會保存 Pi input/output/transcript/tool calls；source 內容一旦被 Pi 讀取，可能進入 Syke DB 或 MEMEX。Seed prompt 是 advisory，不是 hard privacy boundary。

因此至少需要：

- source-specific consent，明確說明內容可能送至設定的 LLM provider。
- source projection 先做 field allowlist、size cap、asset/raw/account exclusion。
- durable memory 只保存抽象結論，不保存整段 ChatGPT transcript、project instructions 或 secrets。
- 若需求是「原文絕不進 trace」，必須在 source projection/trace layer 做 deterministic enforcement，不能只寫 seed 指令。

## 10. 建議實作分期

### Phase 0：驗證器與 fixture

- 建立 synthetic archive fixture，不含真實 prompt、email、binary。
- 驗證 marker、index、scope、project、graph、asset classifier。
- 輸出 inventory/validation report，不接 memory。

### Phase 1：安全 source registration

- explicit export-root resolver。
- default-off selection。
- realpath、symlink、root containment、size/file/record limits。
- catalog/doctor/setup/sandbox 行為一致。

### Phase 2：bounded adapter

- 新增 `adapter-chatgpt-web.md` 或等價 bounded projection adapter。
- index-first，逐 conversation 讀取。
- branch-aware normalized projection。
- source/image/Codex connector provenance labels。
- 不建立 generic events table。

### Phase 3：snapshot diff

- 以 archive/index hash 與 `normalizedHash` 判定 added/changed/unchanged。
- 保留 snapshot cursor/manifest metadata。
- absent row 只標 stale，不自動刪除 memory。

### Phase 4：若必要才做 deterministic projector

只有在 LLM-first projection 的 privacy、重現性或增量需求不足時，才新增 deterministic normalized projection；仍不需要把完整 raw archive 複製進 Syke DB。

## 11. 測試與驗收規格

### Fixture / schema

必測：

- provider/schema/normalizer mismatch。
- unknown top-level fields。
- malformed JSONL 與單筆損壞。
- main/project/shared 重疊 membership。
- project metadata 與 conversation index join。
- root node、current head、多 child branch。
- null timestamp、unknown part、missing message。
- `nodes[].raw` 不出現在 projection。
- same logical asset → same sha256。
- user upload 與 tool-generated image 的區分。
- pending image task 無 output。
- Codex keyword 不誤判 standalone Codex source；`contains_mcp_source` 可標記 Web connector。

### Snapshot/incremental

- 同一 archive 重跑為 zero-change。
- 只修改一個 normalizedHash 時只更新該 conversation。
- 新增 membership 不產生第二個 conversation。
- duplicate batch source 不產生第二份 conversation。
- partial/incomplete run 不破壞上次 accepted state。
- absent conversation 不會自動刪除 memory。

### Syke runtime

- `get_source("chatgpt-web")` 行為正確。
- 未設定 root 時不 discovery、不 grant sandbox。
- 未選 source 時不應意外讀取 ChatGPT archive。
- explicit source selection 會同步到 setup、sync、daemon、prompt、sandbox。
- seed 進入 wheel/sdist。
- `PSYCHE.md` 只列 selected source。
- source exclusion/skip reason 可觀測。
- raw/account/assets 不會因 adapter read 進入 trace。

### Current archive manual acceptance

本地不提交真實 archive fixture；手動驗證應得到：

```text
312 unique conversations
4 projects
main=229, project=91, shared=3, archived=0
805 logical asset refs
522 physical assets
partial asset refs=0
88 high-confidence generated-output conversations
82 of those in the dedicated image project
6 generated-output conversations outside that project
8 conversations with ChatGPT Web Codex/CodexMCP connector provenance
0 bundled standalone Codex rollout files
```

## 12. 明確非目標

- 不登入 ChatGPT，不呼叫 ChatGPT remote API。
- 不修改、搬移、刪除 exporter archive。
- 不把 Web conversation 與 Codex rollout 合併成一個 source identity。
- 不恢復舊 `Event`/`events.db`/ZIP parser pipeline。
- 不把所有 account/project instructions 自動轉成 memory。
- 不自動下載 citation URL 或 connector 外部檔案。
- 不做 OCR、vision embedding、圖片內容摘要，除非另行明確需求。
- 不保證 snapshot 能恢復遠端刪除歷史。
- 不以 title、keyword 或 semantic similarity 做 destructive dedup。
- 不宣稱 exporter metadata 是瀏覽器登入 session 的鑑證。

## 13. 開發時的固定判斷

| 問題                                      | 固定答案                                                   |
| ----------------------------------------- | ---------------------------------------------------------- |
| 這批 archive 的外層來源？                 | `chatgpt-web` exporter                                     |
| 是否存在 standalone Codex archive？       | 目前 archive 內沒有確認到                                  |
| 是否有 Web 對話使用 Codex？               | 有；8/312 有 connector provenance                          |
| 圖片 project 能否過濾？                   | 能作高精度過濾，但不能取代全 archive generation classifier |
| `kind=generated_image` 是否足夠？         | 不足                                                       |
| title 是否可作 identity？                 | 不可                                                       |
| JSON 陣列順序是否是對話順序？             | 不可依賴                                                   |
| `normalizedHash` 是否是永久 ID？          | 不是；是 snapshot/version hash                             |
| 能否把 absent row 當遠端刪除？            | 不能                                                       |
| 能否直接加入 `~/Downloads/chat` catalog？ | 不可；必須 explicit root + privacy/sandbox boundary        |
| 是否先建立 generic event database？       | 不需要                                                     |
