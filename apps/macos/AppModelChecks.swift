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
            "values": ["host": "codex", "channel": "qwen", "mode": "rules", "primary_model": "fixture", "qwen_executor": "codex", "credential_present": true, "base_url": "https://fixture.invalid/anthropic", "responses_base_url": "https://fixture.invalid/v1"]]
        let checked: [String: Any] = ["signature": "a", "message": "本地检查通过", "checked_at": "fixture", "actual_model": "fake"]
        model.refresh(); assert(model.busy)
        front.complete(.success(snapshot)); assert(model.loaded && !model.busy)
        model.check(); assert(model.checking && front.requests.last?["action"] as? String == "local")
        front.complete(.success(checked))
        assert(model.status == "本地检查通过\nfixture", "Local checks must show only message and time even with actual_model")
        var localRequested = checked
        localRequested["model_evidence"] = "requested_only"; localRequested["requested_model"] = "qwen-fixture"
        model.check(); front.complete(.success(localRequested))
        assert(model.status == "本地检查通过\nfixture", "Local checks must ignore requested-only model evidence")
        model.check(real: true)
        assert(front.requests.last?["action"] as? String == "real")
        front.complete(.success(checked))
        assert(model.status == "本地检查通过\nfixture · 实际模型：fake", "Real checks must retain the actual model")
        var unknownModel = checked
        unknownModel["actual_model"] = NSNull()
        model.check(real: true); front.complete(.success(unknownModel))
        assert(model.status == "本地检查通过\nfixture · 实际模型：未知", "Real checks must retain unknown model evidence")
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
        assert(ModelCatalog.claude.count == 4 && ModelCatalog.codex.count == 4 && ModelCatalog.qwen.count == 1 && ModelCatalog.deepseek.count == 1)
        var apiSnapshot = snapshot
        var apiValues = snapshot["values"] as! [String: Any]
        apiValues["qwen_model"] = "custom-api-id"; apiValues["secondary_model"] = "legacy-implementation"
        apiValues["base_url"] = "https://fixture.invalid/anthropic"
        apiValues["claude_model"] = "legacy-claude"; apiValues["codex_model"] = "fixture"
        apiValues["claude_channel"] = "qwen"; apiValues["codex_channel"] = "qwen"
        apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.reviewFamily == "custom_api" && model.reviewerOptions.contains { $0.id == "custom-api-id" })
        model.switchReviewFamily("custom_api"); assert(!model.dirty)
        model.draft.secretAction = "replace"; model.draft.secret = "fake-secret"
        model.switchReviewFamily("token_plan")
        assert(model.draft.channel == "qwen" && model.draft.qwenModel == "qwen3.8-max")
        assert(model.draft.secret.isEmpty && model.draft.baseURL.contains("qianwenaiapi"))
        model.draft.qwenModel = "deepseek-v4-pro"; model.draft.secretAction = "replace"; model.draft.secret = "other-secret"
        model.switchReviewFamily("custom_api")
        assert(model.draft.qwenModel == "custom-api-id" && model.draft.secret == "fake-secret")
        assert(model.draft.baseURL == "https://fixture.invalid/anthropic")
        model.switchReviewFamily("token_plan"); assert(model.draft.secret == "other-secret" && model.draft.qwenModel == "deepseek-v4-pro")
        let failedDraft = model.draft
        model.save(); assert(front.requests.last?["secret"] == nil)
        let postedActions = front.requests.last?["secret_actions"] as! [String: [String: String]]
        assert(postedActions["custom_api"]?["value"] == "fake-secret" && postedActions["token_plan"]?["value"] == "other-secret")
        front.complete(.failure(BridgeFailure(message: "fixture conflict")))
        assert(model.draft == failedDraft && model.dirty)
        model.discard(); assert(model.reviewFamily == "custom_api" && !model.dirty && model.draft.secret.isEmpty)
        model.switchReviewFamily("codex"); assert(model.draft.channel == "qwen", "Codex cannot review Codex")
        model.switchReviewFamily("qoder"); assert(model.draft.channel == "qoder" && model.reviewerOptions.first?.id == "kmodel_latest")
        model.discard()
        model.switchHost("claude"); assert(model.primaryOptions.contains { $0.id == "legacy-claude" })
        model.switchReviewFamily("qoder"); assert(model.draft.channel != "qoder")
        model.discard()
        let menuCount = front.requests.count
        model.menuChannel("token_plan")
        assert(front.requests.count == menuCount && model.dirty && model.message.contains("设置"), "Incomplete service must guide settings without saving")
        model.discard()
        model.restore(); front.complete(.success(snapshot)); assert(model.reviewFamily == "custom_api")
        apiValues["host"] = "claude"; apiValues["channel"] = "codex"
        apiValues["qwen_model"] = "dormant-api-id"; apiValues["reviewer_model"] = "dormant-codex-id"
        apiSnapshot["values"] = apiValues
        model.refresh(); front.complete(.success(apiSnapshot))
        assert(model.reviewerOptions.contains { $0.id == "dormant-codex-id" })
        model.switchReviewFamily("custom_api"); model.draft.qwenModel = "qwen3.7-plus"
        assert(model.draft.qwenModel == "qwen3.7-plus")
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
        // Missing Qwen endpoints are suggestions in the active protocol draft only.
        // Service profiles retain independent protocol addresses and all unsaved actions.
        model.refresh(); front.complete(.success(apiSnapshot))
        model.switchReviewFamily("token_plan")
        model.draft.baseURL = "https://one.invalid/a"; model.draft.responsesBaseURL = "https://one.invalid/r"
        model.draft.secretAction = "replace"; model.draft.secret = "one"
        model.draft.qwenModel = "deepseek-v4-pro"
        assert(model.draft.secret == "one", "Changing a model must retain service credentials")
        model.switchReviewFamily("custom_api")
        model.draft.baseURL = "https://two.invalid/a"; model.draft.responsesBaseURL = "https://two.invalid/r"
        model.draft.secretAction = "clear"
        assert(model.draft.secret.isEmpty)
        model.switchReviewFamily("token_plan")
        assert(model.draft.baseURL == "https://one.invalid/a" && model.draft.responsesBaseURL == "https://one.invalid/r" && model.draft.secret == "one")
        model.switchHost("claude"); assert(model.draft.activeBaseURL == "https://one.invalid/a")
        model.switchHost("codex"); assert(model.draft.activeBaseURL == "https://one.invalid/r")
        model.switchReviewFamily("custom_api"); assert(model.draft.secretAction == "clear" && model.draft.baseURL == "https://two.invalid/a")
        model.discard(); assert(!model.dirty)
        var serviceSnapshot = snapshot
        var serviceValues = snapshot["values"] as! [String: Any]
        var token = ServiceProfile.initial("token_plan"); token.model = "historical-unknown"
        serviceValues["qwen_model"] = token.model; serviceValues["base_url"] = token.baseURL; serviceValues["responses_base_url"] = token.responsesBaseURL
        serviceValues["review_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": token.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
        serviceSnapshot["values"] = serviceValues
        model.refresh(); front.complete(.success(serviceSnapshot))
        model.draft.qwenModel = "deepseek-v4-pro"
        assert(model.reviewerOptions.contains { $0.id == "historical-unknown" }, "Loaded unknown service model remains selectable")
        model.draft.secretAction = "replace"; model.draft.secret = ""
        let emptyActions = model.draft.secretActions as! [String: [String: String]]
        assert(emptyActions["token_plan"]?["action"] == "retain", "Empty replacement input means retain")
        model.draft.secret = "new-value"; model.draft.secretAction = "clear"
        let clearActions = model.draft.secretActions as! [String: [String: String]]
        assert(clearActions["token_plan"]?["action"] == "clear" && clearActions["token_plan"]?["value"] == "")
        model.switchReviewFamily("custom_api")
        assert(!model.selectedCredentialMessage.contains("发现"), "Never reuse another service's file status")
        model.discard(); assert(!model.dirty && model.draft.qwenModel == "historical-unknown")
        withExtendedLifetime(subscription) {}
        let addressFront = FakeClient()
        let addressModel = AppModel(foreground: addressFront, background: FakeClient(), automatic: false)
        var missingAddressSnapshot = serviceSnapshot
        var missingAddressValues = serviceValues
        var missingToken = ServiceProfile.initial("token_plan")
        missingToken.baseURL = ""
        missingAddressValues["base_url"] = ""
        missingAddressValues["review_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": missingToken.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
        missingAddressSnapshot["values"] = missingAddressValues
        addressModel.refresh(); addressFront.complete(.success(missingAddressSnapshot))
        assert(addressModel.draft.baseURL == "https://token-plan.maas.qianwenaiapi.com/apps/anthropic", "Missing Token Plan Anthropic address must receive its default")
        assert(addressModel.saved.baseURL.isEmpty && addressModel.dirty, "Default is an unsaved draft, not a claim about disk")
        addressModel.switchHost("claude")
        assert(addressModel.draft.activeBaseURL == "https://token-plan.maas.qianwenaiapi.com/apps/anthropic")
        addressModel.switchHost("codex")
        assert(addressModel.draft.activeBaseURL == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1")
        missingToken.baseURL = "https://existing.invalid/anthropic"; missingToken.responsesBaseURL = ""
        missingAddressValues["base_url"] = missingToken.baseURL
        missingAddressValues["responses_base_url"] = ""
        missingAddressValues["review_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": missingToken.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
        missingAddressSnapshot["values"] = missingAddressValues
        addressModel.refresh(); addressFront.complete(.success(missingAddressSnapshot))
        assert(addressModel.draft.baseURL == "https://existing.invalid/anthropic")
        assert(addressModel.draft.responsesBaseURL == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1")
        addressModel.switchReviewFamily("custom_api")
        assert(addressModel.draft.baseURL.isEmpty && addressModel.draft.responsesBaseURL.isEmpty, "Custom API must not receive Token Plan endpoints")
        // Editing the single masked field maps to service-scoped retain/replace/clear.
        var keyDraft = Draft()
        keyDraft.profile.credentialPaths = ["fixture-credentials.json"]
        let untouched = keyDraft
        keyDraft.apiKeyInput = keyDraft.apiKeyInput
        assert(keyDraft == untouched && keyDraft.secretAction == "retain")
        keyDraft.apiKeyInput = ""
        assert(keyDraft.secretAction == "clear" && keyDraft.secret.isEmpty)
        keyDraft.apiKeyInput = "fixture-new-key"
        assert(keyDraft.secretAction == "replace" && keyDraft.secret == "fixture-new-key")
        keyDraft.activeAPI = "token_plan"
        assert(keyDraft.apiKeyInput.isEmpty && keyDraft.secretAction == "retain")
        keyDraft.apiKeyInput = "other-fixture-key"
        keyDraft.activeAPI = "custom_api"
        assert(keyDraft.apiKeyInput == "fixture-new-key")
        keyDraft.apiKeyInput = ""
        let editedKeys = keyDraft.secretActions as! [String: [String: String]]
        assert(editedKeys["custom_api"]?["action"] == "clear")
        assert(editedKeys["token_plan"]?["value"] == "other-fixture-key")
        keyDraft = untouched
        keyDraft.apiKeyInput += "replacement"
        assert(keyDraft.secret == "replacement", "The display mask must never be saved as a credential")
        print("PASS: AppModel quiet inspect, single flight, foreground priority, stale callbacks, draft/save/refresh/restore/cancel")
    }
}
