import Foundation
import Combine

@MainActor final class FakeClient: AppRequesting {
    var requests: [[String: Any]] = []
    var completions: [(Result<[String: Any], Error>) -> Void] = []
    var cancellations = 0
    func start(_ payload: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        requests.append(payload); completions.append(completion)
    }
    func cancel() { cancellations += 1 }
    func complete(_ result: Result<[String: Any], Error>) { completions.removeFirst()(result) }
}

@main struct AppModelChecks {
    @MainActor static func main() {
        let front = FakeClient(), back = FakeClient()
        let model = AppModel(foreground: front, background: back, automatic: false)
        let snapshot: [String: Any] = ["signature": "a", "fingerprints": ["fixture": "a"], "can_restore": true,
            "values": ["host": "codex", "channel": "qwen", "mode": "rules", "primary_model": "fixture", "qwen_executor": "codex", "credential_present": true]]
        let checked: [String: Any] = ["signature": "a", "message": "本地检查通过", "checked_at": "fixture", "actual_model": "fake"]
        model.refresh(); assert(model.busy)
        front.complete(.success(snapshot)); assert(model.loaded && !model.busy)
        model.check(); assert(model.checking && front.requests.last?["action"] as? String == "local")
        front.complete(.success(checked))
        var publications = 0
        let subscription = model.objectWillChange.sink { publications += 1 }
        for _ in 0..<3 {
            model.inspect(); model.inspect()
            assert(!model.busy && !model.checking && back.completions.count == 1)
            back.complete(.success(["signature": "a"]))
        }
        assert(publications == 0, "Unchanged inspections must publish no UI updates")
        for stale in [Result<[String: Any], Error>.success(["signature": "stale"]), .failure(BridgeFailure(message: "stale error"))] {
            model.inspect(); model.check()
            assert(model.busy && model.checking && back.cancellations > 0)
            let status = model.status, message = model.message
            back.complete(stale)
            assert(model.busy && model.checking && model.status == status && model.message == message && model.credentialPresent)
            front.complete(.success(checked))
        }
        // A late response after the new check completes is equally stale.
        model.inspect(); model.check(); front.complete(.success(checked))
        let status = model.status
        model.inspect(); assert(back.completions.count == 1, "Cancellation must retain single flight")
        back.complete(.failure(BridgeFailure(message: "late"))); assert(model.status == status)
        model.draft.primaryModel = "unsaved"
        let saved = model.saved, draft = model.draft
        model.inspect(); back.complete(.success(["signature": "b"]))
        assert(model.status.contains("已变化") && model.saved == saved && model.draft == draft)
        let requestCount = front.requests.count
        model.check(); model.menuHost("codex"); model.menuMode("solo")
        assert(front.requests.count == requestCount && model.draft == draft)
        model.discard(); assert(!model.dirty)
        assert(model.modes.map { $0.0 } == ["solo", "auto", "rules", "force"])
        model.menuMode("force"); assert(model.saved.mode == "rules")
        front.complete(.failure(BridgeFailure(message: "fixture conflict")))
        assert(!model.dirty && model.saved.mode == "rules")
        model.menuMode("force")
        var savedSnapshot = snapshot
        var values = snapshot["values"] as! [String: Any]; values["mode"] = "force"; savedSnapshot["values"] = values
        front.complete(.success(savedSnapshot)); assert(model.saved.mode == "force" && !model.dirty)
        model.refresh(); assert(front.requests.last?["action"] as? String == "load")
        assert(front.requests.last?["secret"] == nil); front.complete(.success(snapshot))
        model.restore(); assert(front.requests.last?["fingerprints"] != nil); front.complete(.success(snapshot))
        model.check(); model.cancel(); assert(front.cancellations == 1)
        front.complete(.failure(BridgeFailure(message: "检测已取消")))
        assert(!model.busy && !model.checking && model.status.contains("检测已取消"))
        model.inspect(); model.stopBackground(); back.complete(.failure(BridgeFailure(message: "shutdown")))
        assert(model.status.contains("检测已取消"))
        assert(ModelCatalog.claude.count == 4 && ModelCatalog.codex.count == 4 && ModelCatalog.qwen.count == 4 && ModelCatalog.deepseek.count == 5)
        var apiSnapshot = snapshot
        var apiValues = snapshot["values"] as! [String: Any]
        apiValues["qwen_model"] = "custom-api-id"; apiValues["secondary_model"] = "legacy-implementation"
        apiValues["base_url"] = "https://fixture.invalid/anthropic"
        apiValues["claude_model"] = "legacy-claude"; apiValues["codex_model"] = "fixture"
        apiValues["claude_channel"] = "qwen"; apiValues["codex_channel"] = "qwen"
        apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.reviewFamily == "qwen" && model.reviewerOptions.contains { $0.id == "custom-api-id" })
        model.switchReviewFamily("qwen"); assert(!model.dirty, "Same derived family must preserve custom API ID")
        model.draft.secret = "fake-secret"
        model.switchReviewFamily("deepseek")
        assert(model.draft.channel == "qwen" && model.draft.qwenModel == "deepseek-v4-pro")
        assert(model.draft.baseURL == "https://fixture.invalid/anthropic" && model.draft.secret == "fake-secret")
        model.draft.qwenModel = "deepseek-custom"; model.switchReviewFamily("deepseek")
        assert(model.draft.qwenModel == "deepseek-custom")
        model.switchReviewFamily("qwen")
        assert(model.reviewerOptions.contains { $0.id == "custom-api-id" }, "Loaded fallback must remain selectable after switching away")
        model.switchReviewFamily("codex"); assert(model.draft.channel == "qwen", "Codex cannot review Codex")
        model.discard(); assert(model.reviewFamily == "qwen" && !model.dirty)
        model.switchHost("claude")
        assert(model.primaryOptions.contains { $0.id == "legacy-claude" })
        model.discard()
        model.menuChannel("deepseek")
        let posted = front.requests.last?["values"] as! [String: Any]
        assert(posted["channel"] as? String == "qwen" && posted["qwen_model"] as? String == "deepseek-v4-pro")
        front.complete(.failure(BridgeFailure(message: "fixture conflict")))
        assert(model.savedReviewFamily == "qwen" && model.reviewFamily == "qwen" && !model.dirty)
        model.menuChannel("deepseek")
        apiValues["qwen_model"] = "deepseek-v4-pro"; apiSnapshot["values"] = apiValues
        front.complete(.success(apiSnapshot))
        assert(model.savedReviewFamily == "deepseek" && !model.dirty && model.title.contains("DeepSeek"))
        assert(model.message.contains("服务地址和密钥"))
        model.draft.qwenModel = "deepseek-custom"
        model.discard(); assert(model.draft.qwenModel == "deepseek-v4-pro")
        model.restore(); front.complete(.success(snapshot)); assert(model.reviewFamily == "qwen")
        apiValues["host"] = "claude"; apiValues["channel"] = "codex"
        apiValues["qwen_model"] = "dormant-api-id"; apiValues["reviewer_model"] = "dormant-codex-id"
        apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.reviewerOptions.contains { $0.id == "dormant-codex-id" })
        model.switchReviewFamily("qwen"); model.draft.qwenModel = "qwen3.7-plus"
        assert(model.reviewerOptions.contains { $0.id == "dormant-api-id" })
        assert(!model.reviewerOptions.contains { $0.id.isEmpty }, "Configured API models cannot select empty IDs")
        model.switchReviewFamily("codex"); model.draft.reviewerModel = "gpt-6-astra"
        assert(model.reviewerOptions.contains { $0.id == "dormant-codex-id" })
        assert(model.reviewerOptions.contains { $0.id.isEmpty && $0.label == "CLI 默认" })
        model.discard()
        apiValues["channel"] = "legacy-channel"; apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.reviewFamily == "legacy-channel" && !model.dirty)
        apiValues["host"] = "codex"; apiValues["channel"] = "qwen"
        apiValues["qwen_executor"] = "codex"; apiValues["responses_base_url"] = "https://fixture.invalid/v1"
        apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.draft.effectiveExecutor == "codex" && model.draft.activeBaseURL == "https://fixture.invalid/v1")
        model.draft.activeBaseURL = "https://changed.invalid/v1"
        assert(model.draft.baseURL == "https://fixture.invalid/anthropic")
        model.draft.qwenExecutor = "claude"
        assert(model.draft.activeBaseURL == "https://fixture.invalid/anthropic")
        model.draft.activeBaseURL = "https://changed.invalid/anthropic"
        assert(model.draft.responsesBaseURL == "https://changed.invalid/v1")
        model.discard(); assert(model.draft.activeBaseURL == "https://fixture.invalid/v1")
        model.switchHost("claude")
        assert(model.draft.effectiveExecutor == "claude" && model.draft.qwenExecutor == "codex")
        model.discard(); model.save()
        let endpointPost = front.requests.last?["values"] as! [String: Any]
        assert(endpointPost["responses_base_url"] as? String == "https://fixture.invalid/v1")
        assert(endpointPost["qwen_executor"] as? String == "codex")
        front.complete(.success(apiSnapshot))
        model.check(real: true)
        assert(model.message.contains("30 分钟"))
        front.complete(.success(["signature": "a", "message": "通过", "model_evidence": "requested_only", "requested_model": "qwen-fixture", "actual_model": NSNull()]))
        assert(model.status.contains("仅确认请求模型：qwen-fixture") && model.status.contains("服务端实际模型未知"))
        // Two hosts keep independent drafts while API and workflow fields stay shared.
        model.refresh(); front.complete(.success(apiSnapshot))
        let initial = model.draft
        let count = front.requests.count
        model.switchHost("claude"); model.switchHost("codex")
        assert(!model.dirty && model.draft == initial && front.requests.count == count)
        model.draft.primaryModel = "codex-edited"
        model.draft.secret = "shared-secret"
        model.draft.baseURL = "https://shared.invalid/anthropic"
        model.switchHost("claude")
        model.draft.primaryModel = "claude-edited"; model.draft.channel = "codex"
        assert(!model.claudeFallbacks.contains("codex"))
        model.switchHost("codex")
        assert(model.draft.primaryModel == "codex-edited" && model.draft.channel == "qwen")
        assert(model.draft.secret == "shared-secret" && model.draft.baseURL == "https://shared.invalid/anthropic")
        model.draft.primaryModel = initial.primaryModel
        model.draft.secret = initial.secret; model.draft.baseURL = initial.baseURL
        assert(model.dirty, "Inactive host changes must remain dirty")
        let beforeMenu = model.draft
        model.menuHost("codex"); model.menuHost("claude"); model.menuMode("solo"); model.menuChannel("deepseek")
        assert(front.requests.count == count && model.draft == beforeMenu, "Menus must never save window drafts")
        model.switchHost("unknown"); assert(model.draft == beforeMenu)
        model.save()
        let batch = front.requests.last?["values"] as! [String: Any]
        let updates = batch["host_updates"] as! [String: [String: String]]
        assert(updates["claude"]?["primary_model"] == "claude-edited" && updates["codex"] == nil)
        front.complete(.failure(BridgeFailure(message: "batch conflict")))
        model.switchHost("claude")
        assert(model.draft.primaryModel == "claude-edited" && model.dirty, "Failed save must retain every host draft")
        model.switchHost("codex"); model.draft.primaryModel = "codex-edited"
        model.save()
        let both = (front.requests.last?["values"] as! [String: Any])["host_updates"] as! [String: [String: String]]
        assert(both["claude"]?["primary_model"] == "claude-edited" && both["codex"]?["primary_model"] == "codex-edited")
        let busyDraft = model.draft, busyCount = front.requests.count
        model.switchHost("claude"); model.menuHost("codex"); model.menuMode("solo")
        assert(model.draft == busyDraft && front.requests.count == busyCount)
        front.complete(.success(apiSnapshot)); assert(!model.dirty)
        model.switchHost("claude"); assert(model.draft.primaryModel != "claude-edited")
        model.discard(); assert(model.draft == initial && !model.dirty)
        model.switchHost("claude"); model.draft.primaryModel = "discarded"
        model.refresh(); front.complete(.success(apiSnapshot))
        model.switchHost("claude"); assert(model.draft.primaryModel != "discarded")
        model.draft.primaryModel = "restored"
        model.restore(); front.complete(.success(apiSnapshot))
        model.switchHost("claude"); assert(model.draft.primaryModel != "restored")
        model.discard()
        let unloadedFront = FakeClient()
        let unloaded = AppModel(foreground: unloadedFront, background: FakeClient(), automatic: false)
        unloaded.menuHost("claude"); unloaded.menuMode("force"); unloaded.menuChannel("qwen")
        assert(unloadedFront.requests.isEmpty && !unloaded.dirty)
        // Fixed executor policy must stage legacy migrations, never silently check the old route.
        let legacyFront = FakeClient()
        let legacy = AppModel(foreground: legacyFront, background: FakeClient(), automatic: false)
        var legacySnapshot = apiSnapshot
        var legacyValues = apiValues
        legacyValues["host"] = "codex"; legacyValues["channel"] = "qwen"
        legacyValues["qwen_executor"] = "claude"
        legacySnapshot["values"] = legacyValues
        legacy.refresh(); legacyFront.complete(.success(legacySnapshot))
        assert(legacy.saved.qwenExecutor == "claude" && legacy.draft.qwenExecutor == "codex" && legacy.dirty,
               "Legacy Codex route must be staged for explicit save")
        assert(legacy.draft.activeBaseURL == "https://fixture.invalid/v1")
        let legacyCount = legacyFront.requests.count
        legacy.check(); legacy.check(real: true)
        assert(legacyFront.requests.count == legacyCount, "Unsaved executor migration must block all checks")
        legacy.discard()
        assert(legacy.dirty && legacy.draft.qwenExecutor == "codex" && legacy.saved.qwenExecutor == "claude")
        legacy.save()
        let migration = legacyFront.requests.last?["values"] as! [String: Any]
        assert(migration["qwen_executor"] as? String == "codex")
        assert(migration["base_url"] as? String == "https://fixture.invalid/anthropic")
        assert(migration["responses_base_url"] as? String == "https://fixture.invalid/v1")
        legacyFront.complete(.failure(BridgeFailure(message: "migration conflict")))
        assert(legacy.dirty && legacy.saved.qwenExecutor == "claude")
        legacyValues["qwen_executor"] = "codex"; legacySnapshot["values"] = legacyValues
        legacy.save(); legacyFront.complete(.success(legacySnapshot))
        assert(!legacy.dirty && legacy.saved.qwenExecutor == "codex")
        legacyValues["host"] = "claude"; legacyValues["channel"] = "qwen"
        legacyValues["qwen_executor"] = "claude"; legacySnapshot["values"] = legacyValues
        legacy.refresh(); legacyFront.complete(.success(legacySnapshot))
        assert(!legacy.dirty && legacy.draft.qwenExecutor == "claude" && legacy.draft.effectiveExecutor == "claude")
        assert(legacy.draft.activeBaseURL == "https://fixture.invalid/anthropic")
        legacy.switchHost("codex")
        assert(legacy.dirty && legacy.draft.qwenExecutor == "codex")
        legacy.switchHost("claude")
        assert(legacy.draft.qwenExecutor == "codex" && legacy.draft.effectiveExecutor == "claude")
        legacy.discard(); assert(!legacy.dirty && legacy.draft.qwenExecutor == "claude")
        withExtendedLifetime(subscription) {}
        print("PASS: AppModel quiet inspect, single flight, foreground priority, stale callbacks, draft/save/refresh/restore/cancel")
    }
}
