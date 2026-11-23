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
            "values": ["host": "codex", "channel": "qwen", "mode": "rules", "primary_model": "fixture", "qwen_executor": "codex", "claude_qwen_executor": "codex", "credential_present": true, "base_url": "https://fixture.invalid/anthropic", "responses_base_url": "https://fixture.invalid/v1"]]
        checkDualPlanAndReviewAddress(snapshot)
        checkAgyDualPlan(snapshot)
        checkAgyHost()
        checkExternalChanges()
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
        assert(ModelCatalog.claude.count == 4 && ModelCatalog.qwen.count == 2 && ModelCatalog.deepseek.count == 1)
        assert(ModelCatalog.codex == ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"])
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
        assert(model.reviewerOptions.first?.label == "Kimi-K3.1-Pro")
        model.discard()
        model.switchHost("claude")
        assert(model.primaryOptions.contains(ModelOption(id: "legacy-claude", label: "已有配置：legacy-claude")))
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
        assert(model.reviewUsesQwen && model.reviewExecutorLabel == "Codex CLI · Responses")
        model.draft.responsesBaseURL = "https://changed.invalid/v1"
        assert(model.draft.baseURL == "https://fixture.invalid/anthropic")
        model.switchHost("claude")
        assert(model.reviewUsesQwen && model.reviewExecutorLabel == "Codex CLI · Responses")
        assert(model.draft.responsesBaseURL == "https://changed.invalid/v1")
        model.discard(); assert(model.draft.responsesBaseURL == "https://fixture.invalid/v1")
        model.save()
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
        // Legacy migrations require an explicit save before either kind of check.
        for (host, executorKey) in [("claude", "claude_qwen_executor"), ("codex", "qwen_executor")] {
            let legacyFront = FakeClient()
            let legacy = AppModel(foreground: legacyFront, background: FakeClient(), automatic: false)
            var legacySnapshot = apiSnapshot
            var legacyValues = apiValues
            legacyValues["host"] = host; legacyValues["channel"] = "qwen"
            legacyValues[executorKey] = "claude"
            legacySnapshot["values"] = legacyValues
            legacy.refresh(); legacyFront.complete(.success(legacySnapshot))
            assert(legacy.executorMigrationPending && legacy.dirty)
            assert(legacy.reviewUsesQwen && legacy.reviewExecutorLabel == "Codex CLI · Responses")
            assert(legacy.draft.responsesBaseURL == "https://fixture.invalid/v1")
            let legacyCount = legacyFront.requests.count
            legacy.check(); legacy.check(real: true)
            legacy.discard()
            assert(legacy.dirty && legacy.executorMigrationPending)
            legacy.check(); legacy.check(real: true)
            assert(legacyFront.requests.count == legacyCount, "Unsaved executor migration must block all checks")
            legacy.save()
            let migration = legacyFront.requests.last?["values"] as! [String: Any]
            assert(migration["qwen_executor"] as? String == "codex")
            assert(migration["claude_qwen_executor"] as? String == "codex")
            assert(migration["base_url"] as? String == "https://fixture.invalid/anthropic")
            assert(migration["responses_base_url"] as? String == "https://fixture.invalid/v1")
            legacyFront.complete(.failure(BridgeFailure(message: "migration conflict")))
            assert(legacy.dirty && legacy.executorMigrationPending)
            legacyValues[executorKey] = "codex"; legacySnapshot["values"] = legacyValues
            legacy.save(); legacyFront.complete(.success(legacySnapshot))
            assert(!legacy.dirty && !legacy.executorMigrationPending)
            for (real, action) in [(false, "local"), (true, "real")] {
                let before = legacyFront.requests.count
                legacy.check(real: real)
                assert(legacyFront.requests.count == before + 1)
                assert(legacyFront.requests.last?["action"] as? String == action)
                legacyFront.complete(.success(checked))
            }
        }
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
        model.switchHost("claude"); assert(model.draft.responsesBaseURL == "https://one.invalid/r")
        model.switchHost("codex"); assert(model.draft.responsesBaseURL == "https://one.invalid/r")
        model.switchReviewFamily("custom_api"); assert(model.draft.secretAction == "clear" && model.draft.baseURL == "https://two.invalid/a")
        model.discard(); assert(!model.dirty)
        var serviceSnapshot = snapshot
        var serviceValues = snapshot["values"] as! [String: Any]
        var token = ServiceProfile.initial("token_plan"); token.model = "historical-unknown"
        serviceValues["qwen_model"] = token.model; serviceValues["base_url"] = token.baseURL; serviceValues["responses_base_url"] = token.responsesBaseURL
        serviceValues["model_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": token.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
        serviceSnapshot["values"] = serviceValues
        model.refresh(); front.complete(.success(serviceSnapshot))
        // A retained external backup must keep its declared legacy lookup key.
        let defaultBackup = Draft().accountDictionary["backup"] as! [String: Any]
        assert(defaultBackup["credential_key"] as? String == "model_services.token_plan_backup.value")
        let backupFront = FakeClient()
        let backupModel = AppModel(foreground: backupFront, background: FakeClient(), automatic: false)
        var oldBackupSnapshot = serviceSnapshot
        var oldBackupValues = serviceValues
        oldBackupValues["token_plan_accounts"] = ["version": 1, "auto_failover": false, "backup": ["credential_paths": ["project/legacy-secrets.json"], "credential_key": "review_services.token_plan_backup.value"]]
        oldBackupSnapshot["values"] = oldBackupValues
        backupModel.refresh(); backupFront.complete(.success(oldBackupSnapshot))
        assert(backupModel.draft.backupProfile.credentialKey == "review_services.token_plan_backup.value")
        backupModel.draft.mode = "force"
        backupModel.save()
        let backupSavedValues = backupFront.requests.last?["values"] as! [String: Any]
        let backupSavedAccounts = backupSavedValues["token_plan_accounts"] as! [String: Any]
        let backupSavedSlot = backupSavedAccounts["backup"] as! [String: Any]
        assert(backupSavedSlot["credential_key"] as? String == "review_services.token_plan_backup.value", "Saving unrelated fields must retain an external backup key")
        assert(backupSavedSlot["credential_paths"] as? [String] == ["project/legacy-secrets.json"])
        backupFront.complete(.success(oldBackupSnapshot))
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
        missingAddressValues["model_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": missingToken.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
        missingAddressSnapshot["values"] = missingAddressValues
        addressModel.refresh(); addressFront.complete(.success(missingAddressSnapshot))
        assert(addressModel.draft.baseURL == "https://token-plan.maas.qianwenaiapi.com/apps/anthropic", "Missing Token Plan Anthropic address must receive its default")
        assert(addressModel.saved.baseURL.isEmpty && addressModel.dirty, "Default is an unsaved draft, not a claim about disk")
        addressModel.switchHost("claude")
        assert(addressModel.draft.responsesBaseURL == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1")
        addressModel.switchHost("codex")
        assert(addressModel.draft.responsesBaseURL == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1")
        missingToken.baseURL = "https://existing.invalid/anthropic"; missingToken.responsesBaseURL = ""
        missingAddressValues["base_url"] = missingToken.baseURL
        missingAddressValues["responses_base_url"] = ""
        missingAddressValues["model_services"] = ["version": 1, "active_api": "token_plan", "profiles": ["token_plan": missingToken.dictionary, "custom_api": ServiceProfile.initial("custom_api").dictionary]]
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
        // Implementation service is independent of review, with one shared Token Plan draft.
        var implementationDraft = Draft()
        implementationDraft.host = "codex"; implementationDraft.secondaryModel = "historical-native"
        implementationDraft.implementationProvider = "token_plan"
        assert(implementationDraft.implementationModel == "glm-5.3")
        implementationDraft.implementationModel = "qwen4-max"
        implementationDraft.implementationURL = "https://implementation.fixture.invalid/v1"
        implementationDraft.implementationKeyInput = "shared-fixture-key"
        assert(implementationDraft.activeAPI == "custom_api" && implementationDraft.secondaryModel == "historical-native")
        implementationDraft.activeAPI = "token_plan"
        assert(implementationDraft.apiKeyInput == "shared-fixture-key")
        assert(implementationDraft.responsesBaseURL == "https://implementation.fixture.invalid/v1")
        implementationDraft.apiKeyInput = "updated-shared-key"
        assert(implementationDraft.implementationKeyInput == "updated-shared-key")
        implementationDraft.implementationProvider = "qoder"
        assert(implementationDraft.implementationModel == "glm-5.3")
        implementationDraft.implementationModel = "must-not-overwrite-native"
        assert(implementationDraft.secondaryModel == "historical-native")
        assert(implementationDraft.dictionary["implementation_provider"] as? String == "qoder")
        implementationDraft.implementationProvider = "token_plan"
        assert(implementationDraft.implementationModel == "qwen4-max")
        assert(implementationDraft.dictionary["token_plan_implementation_model"] as? String == "qwen4-max")
        implementationDraft.implementationProvider = "codex"
        assert(implementationDraft.implementationModel == "historical-native")
        implementationDraft.host = "claude"
        assert(implementationDraft.implementationEnabled && !implementationDraft.implementationModelEnabled)
        implementationDraft.claudeImplementationProvider = "codex"
        assert(implementationDraft.implementationModelEnabled)
        implementationDraft.mode = "solo"
        assert(!implementationDraft.implementationEnabled && !implementationDraft.implementationModelEnabled)
        implementationDraft.host = "codex"; assert(!implementationDraft.implementationEnabled)
        implementationDraft.mode = "rules"; assert(implementationDraft.implementationEnabled)
        addressModel.draft.implementationProvider = "token_plan"
        addressModel.draft.secondaryModel = "native-draft"
        addressModel.switchHost("claude"); addressModel.switchHost("codex")
        assert(addressModel.draft.implementationProvider == "token_plan" && addressModel.draft.secondaryModel == "native-draft")
        addressModel.discard()
        assert(addressModel.draft.implementationProvider == addressModel.saved.implementationProvider && addressModel.draft.secondaryModel == addressModel.saved.secondaryModel)
        assert(addressModel.draft.tokenPlanImplementationModel == "glm-5.3", "Old snapshots must initialize a valid default")
        addressModel.draft.implementationProvider = "token_plan"
        addressModel.draft.tokenPlanImplementationModel = "qwen4-max"
        assert(addressModel.secondaryOptions.map { $0.id } == ["glm-5.3", "qwen4-max"])
        addressModel.draft.activeAPI = "token_plan"
        addressModel.draft.channel = "qwen"
        assert(addressModel.reviewerOptions.contains { $0.id == "qwen4-max" })
        addressModel.save()
        let implementationValues = addressFront.requests.last?["values"] as! [String: Any]
        assert(implementationValues["implementation_provider"] as? String == "token_plan")
        assert(implementationValues["token_plan_implementation_model"] as? String == "qwen4-max")
        addressFront.complete(.success(missingAddressSnapshot))
        addressModel.draft.implementationProvider = "qoder"
        addressModel.save()
        let qoderValues = addressFront.requests.last?["values"] as! [String: Any]
        assert(qoderValues["implementation_provider"] as? String == "qoder")
        addressFront.complete(.success(missingAddressSnapshot))
        var dual = Draft()
        dual.activeAPI = "token_plan"; dual.apiKeyInput = "primary-fixture"
        dual.backupKeyInput = "backup-fixture"; dual.autoFailover = true
        dual.activeAPI = "custom_api"; dual.apiKeyInput = "custom-fixture"
        dual.host = "codex"; dual.implementationProvider = "token_plan"
        assert(dual.implementationKeyInput == "primary-fixture" && dual.backupKeyInput == "backup-fixture")
        assert(dual.autoFailover)
        let dualActions = dual.secretActions
        assert((dualActions["token_plan_backup"] as! [String: String])["value"] == "backup-fixture")
        assert((dual.dictionary["token_plan_accounts"] as! [String: Any])["auto_failover"] as? Bool == true)
        dual.backupProfile.secret = ""; dual.backupProfile.action = "retain"; dual.backupProfile.credentialPaths = ["~/fixture.json"]
        assert(dual.backupKeyInput == Draft.credentialMask)
        assert((dual.secretActions["token_plan_backup"] as! [String: String])["value"] == "")
        dual.backupKeyInput = ""; assert(dual.backupProfile.action == "clear")
        let claudeFront = FakeClient()
        let claude = AppModel(foreground: claudeFront, background: FakeClient(), automatic: false)
        var claudeValues = apiValues
        claudeValues["host"] = "claude"
        claudeValues["channel"] = "codex"
        claudeValues["claude_channel"] = "codex"
        claudeValues["fallback_channels"] = ["qwen", "same-family"]
        claudeValues["reviewer_model"] = "historical-review"
        claudeValues["primary_model"] = "opus"
        claudeValues["claude_model"] = "opus"
        claudeValues["codex_models"] = ModelCatalog.codex
        var claudeSnapshot = apiSnapshot
        claudeSnapshot["values"] = claudeValues
        claude.refresh(); claudeFront.complete(.success(claudeSnapshot))
        assert(claude.draft.claudeImplementationProvider == "claude-agent")
        assert(claude.reviewUsesQwen && claude.reviewExecutorLabel == "Codex CLI")
        let expectedPrimaryOptions = [
            ModelOption(id: "", label: "不修改（沿用当前设置）"),
            ModelOption(id: "fable", label: "fable（别名 · 自动跟随最新 Fable）"),
            ModelOption(id: "opus", label: "opus（别名 · 自动跟随最新 Opus）"),
            ModelOption(id: "sonnet", label: "sonnet（别名 · 自动跟随最新 Sonnet）"),
            ModelOption(id: "haiku", label: "haiku（别名 · 自动跟随最新 Haiku）"),
            ModelOption(id: "claude-fable-5-1", label: "claude-fable-5-1（固定版本）"),
            ModelOption(id: "claude-opus-5-5", label: "claude-opus-5-5（固定版本）"),
            ModelOption(id: "claude-sonnet-5-5", label: "claude-sonnet-5-5（固定版本）"),
            ModelOption(id: "claude-haiku-5-5", label: "claude-haiku-5-5（固定版本）"),
        ]
        assert(claude.primaryOptions == expectedPrimaryOptions)
        claude.draft.primaryModel = "future-claude"
        assert(claude.primaryOptions.last == ModelOption(id: "future-claude", label: "已有配置：future-claude"))
        claude.discard()
        claude.switchHost("codex")
        assert(claude.primaryOptions.first == expectedPrimaryOptions[0])
        assert(claude.primaryOptions.dropFirst().prefix(6).map(\.label) == ModelCatalog.codex)
        claude.switchReviewFamily("qoder")
        assert(!claude.reviewUsesQwen && claude.reviewExecutorLabel == "Qoder CLI")
        claude.discard()
        assert(claude.reviewerOptions.contains { $0.id == "gpt-6.2-astra" })
        assert(claude.reviewerOptions.contains { $0.id == "historical-review" })
        claude.draft.reviewerModel = "custom-review"
        claude.draft.claudeImplementationProvider = "codex"
        claude.draft.claudeImplementationModel = "bad model"
        let requestsBeforeInvalidModel = claudeFront.requests.count
        claude.save()
        assert(claudeFront.requests.count == requestsBeforeInvalidModel)
        claude.draft.claudeImplementationModel = "custom-implementation"
        assert(claude.draft.responsesBaseURL == "https://fixture.invalid/v1")
        assert(claude.draft.channel == "codex" && claude.claudeFallbacks == ["qwen", "same-family"])
        assert(claude.secondaryOptions.contains { $0.id == "gpt-6.2-sol" })
        claude.save()
        let claudeSave = claudeFront.requests.last?["values"] as! [String: Any]
        assert(claudeSave["reviewer_model"] as? String == "custom-review")
        assert(claudeSave["claude_implementation_provider"] as? String == "codex")
        assert(claudeSave["claude_implementation_model"] as? String == "custom-implementation")
        assert(claudeSave["claude_qwen_executor"] as? String == "codex")
        assert((claudeSave["host_updates"] as! [String: [String: String]]).isEmpty)
        for key in ["reviewer_model", "claude_implementation_provider", "claude_implementation_model", "claude_qwen_executor"] {
            claudeValues[key] = claudeSave[key]
        }
        claudeSnapshot["values"] = claudeValues
        claudeFront.complete(.success(claudeSnapshot))
        assert(!claude.dirty && claude.saved.reviewerModel == "custom-review" && claude.saved.claudeImplementationModel == "custom-implementation")
        assert(!claude.executorMigrationPending && claude.claudeFallbacks == ["qwen", "same-family"])
        claude.draft.mode = "solo"
        assert(!claude.draft.implementationModelEnabled)
        claude.discard()
        claude.draft.claudeImplementationProvider = "claude-agent"
        claude.save()
        let agentSave = claudeFront.requests.last?["values"] as! [String: Any]
        assert(agentSave["claude_implementation_provider"] as? String == "claude-agent")
        claudeFront.complete(.failure(BridgeFailure(message: "fixture end")))
        print("PASS: AppModel quiet inspect, single flight, foreground priority, stale callbacks, draft/save/refresh/restore/cancel")
    }

    @MainActor static func checkExternalChanges() {
        let values: [String: Any] = [
            "host": "claude", "channel": "codex", "mode": "rules", "primary_model": "fixture",
            "claude_channel": "codex", "codex_channel": "qoder",
            "qwen_executor": "codex", "claude_qwen_executor": "codex",
        ]
        let fingerprints: [String: Any] = ["ai": "ai-a", "claude": "claude-a", "codex": NSNull(), "secrets": NSNull()]
        let before: [String: Any] = ["values": values, "signature": "a", "fingerprints": fingerprints]
        let secretsChanged = fingerprints.merging(["secrets": "stat:b"]) { _, new in new }
        let withSecrets: [String: Any] = [
            "values": values.merging(["credential_present": true]) { _, new in new },
            "signature": "a", "fingerprints": secretsChanged,
        ]
        let claudeChanged = fingerprints.merging(["claude": "claude-b"]) { _, new in new }
        let after: [String: Any] = [
            "values": values.merging(["primary_model": "external"]) { _, new in new },
            "signature": "b", "fingerprints": claudeChanged,
        ]
        let checked: [String: Any] = ["signature": "a", "message": "本地检查通过", "checked_at": "fixture"]
        let failure = BridgeFailure(message: "fixture conflict")

        func makeModel(_ snapshot: [String: Any]? = nil) -> (AppModel, FakeClient, FakeClient) {
            let front = FakeClient(), back = FakeClient()
            let model = AppModel(foreground: front, background: back, automatic: false)
            if let snapshot {
                model.refresh()
                front.complete(.success(snapshot))
            }
            return (model, front, back)
        }
        func inspect(_ model: AppModel, using back: FakeClient, snapshot: [String: Any]) {
            model.inspect()
            assert(back.completions.count == 1 && back.requests.last?["action"] as? String == "inspect")
            back.complete(.success(snapshot.filter { $0.key == "signature" || $0.key == "fingerprints" }))
        }
        func assertLoad(_ back: FakeClient) {
            assert(back.completions.count == 1 && back.requests.last?["action"] as? String == "load")
        }
        func sentFingerprints(_ front: FakeClient) -> [String: Any] {
            assert(front.requests.last?["action"] as? String == "save")
            return front.requests.last?["fingerprints"] as! [String: Any]
        }

        // The full inspect response also keeps unchanged polls quiet.
        do {
            let (model, front, back) = makeModel(before)
            model.check(); front.complete(.success(checked))
            var publications = 0
            let observer = model.objectWillChange.sink { publications += 1 }
            for _ in 0..<3 { inspect(model, using: back, snapshot: before) }
            assert(publications == 0 && back.requests.count == 3)
            withExtendedLifetime(observer) {}
        }

        // The managed credential fingerprint and the signature can change independently.
        do {
            let (model, front, back) = makeModel(before)
            inspect(model, using: back, snapshot: withSecrets)
            assertLoad(back)
            assert(!model.busy && front.requests.count == 1)
            model.inspect(); assert(back.requests.count == 2, "Reload must retain the inspection flight")
            back.complete(.success(withSecrets))
            assert(model.credentialPresent && model.externalChanges.isEmpty && model.message.contains("已自动重新读取"))
            model.draft.mode = "force"; model.save()
            let posted = sentFingerprints(front)
            assert(posted["secrets"] as? String == "stat:b" && posted["codex"] is NSNull)
            front.complete(.success(withSecrets))
            var signatureOnly = withSecrets
            signatureOnly["signature"] = "credential-path-changed"
            inspect(model, using: back, snapshot: signatureOnly)
            assertLoad(back)
            back.complete(.success(signatureOnly))
            let count = back.requests.count
            inspect(model, using: back, snapshot: signatureOnly)
            assert(back.requests.count == count + 1 && back.completions.isEmpty)
        }

        // Keep changes to an inactive host, show ordered conflict labels, and recover warnings.
        do {
            let (model, _, back) = makeModel(before)
            model.switchHost("codex")
            model.draft.primaryModel = "unsaved-codex"
            model.switchHost("claude")
            assert(model.draft == model.saved && model.dirty)
            inspect(model, using: back, snapshot: after)
            assert(model.externalChanges == ["Claude 设置"] && back.completions.isEmpty)
            var multiple = after
            multiple["fingerprints"] = ["secrets": "stat:b", "codex": "codex-b", "claude": "claude-b", "ai": "ai-b"]
            inspect(model, using: back, snapshot: multiple)
            assert(model.externalChanges == ["ai-anti 配置", "Claude 设置", "Codex 配置", "凭证文件"])
            model.inspect(); back.complete(.failure(failure))
            assert(model.externalReadUnreadable && model.status.contains("不可验证"))
            inspect(model, using: back, snapshot: before)
            assert(model.externalChanges.isEmpty && !model.externalReadUnreadable && back.completions.isEmpty)
            model.switchHost("codex")
            assert(model.draft.primaryModel == "unsaved-codex")
        }

        // A successful check must preserve existing warnings and the load baseline.
        for real in [false, true] {
            let (model, front, back) = makeModel(before)
            model.draft.primaryModel = "earlier-edit"
            inspect(model, using: back, snapshot: after)
            model.discard()
            model.check(real: real); assert(model.checking)
            model.draft.primaryModel = "edited-during-check"
            var checkResponse = checked
            checkResponse["fingerprints"] = claudeChanged
            front.complete(.success(checkResponse))
            assert(model.externalChanges == ["Claude 设置"] && model.draft.primaryModel == "edited-during-check")
            inspect(model, using: back, snapshot: after)
            assert(back.completions.isEmpty && model.externalChanges == ["Claude 设置"],
                   "A check must not adopt edits made while it was in flight")
            assert(model.dirty && model.saved.primaryModel == "fixture" && model.draft.primaryModel == "edited-during-check")
            model.save()
            assert(sentFingerprints(front)["claude"] as? String == "claude-a")
            front.complete(.failure(failure))
        }

        // Discarding an edit made during a reload makes the same disk snapshot eligible again.
        do {
            let (model, front, back) = makeModel(before)
            inspect(model, using: back, snapshot: after)
            assertLoad(back)
            model.draft.primaryModel = "typed-during-reload"
            back.complete(.success(after))
            assert(model.draft.primaryModel == "typed-during-reload" && model.saved.primaryModel == "fixture")
            model.discard()
            assert(!model.dirty && front.requests.count == 1)
            inspect(model, using: back, snapshot: after)
            assert(!back.completions.isEmpty, "Discard must allow retrying a reload that was interrupted by an edit")
            assertLoad(back)
            back.complete(.success(after))
            assert(model.saved.primaryModel == "external" && !model.dirty)
            model.draft.mode = "force"; model.save()
            assert(sentFingerprints(front)["claude"] as? String == "claude-b")
            front.complete(.success(after))
        }

        // Foreground cancellation does not count as a reload failure. Reopening clears a real failure.
        do {
            let (model, front, back) = makeModel(before)
            inspect(model, using: back, snapshot: after)
            assertLoad(back)
            let cancellations = back.cancellations
            model.draft.mode = "force"; model.save()
            assert(model.busy && back.cancellations == cancellations + 1)
            front.complete(.failure(failure))
            model.discard()
            model.inspect(); assert(back.requests.count == 2)
            back.complete(.failure(BridgeFailure(message: "cancelled")))
            assert(!model.externalReadUnreadable && model.saved.primaryModel == "fixture")
            inspect(model, using: back, snapshot: after)
            assertLoad(back)
            back.complete(.failure(failure))
            assert(model.externalReadUnreadable && model.externalChanges == ["Claude 设置"])
            inspect(model, using: back, snapshot: after)
            assert(back.completions.isEmpty && !model.externalReadUnreadable, "The same snapshot must not restart a failed load")
            model.refreshOnOpen()
            assert(model.busy && front.requests.last?["action"] as? String == "load")
            front.complete(.failure(failure))
            inspect(model, using: back, snapshot: after)
            assertLoad(back)
            back.complete(.success(after))
            assert(model.saved.primaryModel == "external" && !model.messageIsError && model.externalChanges.isEmpty)
        }

        // A slow window-open load gates editing. Opening with a draft only inspects.
        do {
            let (model, front, back) = makeModel()
            model.refreshOnOpen()
            assert(model.busy && !model.loaded && front.requests.last?["action"] as? String == "load")
            model.refreshOnOpen()
            assert(front.requests.count == 1 && back.requests.isEmpty)
            front.complete(.success(before))
            model.check(); front.complete(.success(checked))
            let checkStatus = model.status
            model.refreshOnOpen()
            assert(model.busy && front.requests.last?["action"] as? String == "load", "Opening an already loaded clean form must use foreground load")
            front.complete(.success(before))
            assert(model.status == checkStatus, "An unchanged reload preserves the check result")
            model.refreshOnOpen()
            assert(model.loaded && model.busy && !model.checking && front.requests.last?["action"] as? String == "load")
            model.switchHost("codex")
            assert(model.draft.host == "claude" && model.saved.primaryModel == "fixture")
            front.complete(.success(after))
            model.draft.mode = "force"; model.save()
            assert(sentFingerprints(front)["claude"] as? String == "claude-b")
            var savedOnOpen = after
            savedOnOpen["values"] = (after["values"] as! [String: Any]).merging(["mode": "force"]) { _, new in new }
            front.complete(.success(savedOnOpen))
            model.draft.mode = "auto"
            let draft = model.draft
            let count = front.requests.count
            model.refreshOnOpen()
            assert(!model.busy && front.requests.count == count && back.requests.last?["action"] as? String == "inspect")
            back.complete(.success(["signature": "a", "fingerprints": fingerprints]))
            assert(model.draft == draft && model.externalChanges == ["Claude 设置"] && back.completions.isEmpty)
        }

        // Both local validation and backend conflicts are errors while retaining the draft.
        do {
            let (model, front, back) = makeModel(before)
            model.draft.claudeImplementationProvider = "codex"
            model.draft.claudeImplementationModel = "invalid id"
            let invalid = model.draft
            model.save()
            assert(model.messageIsError && model.message.contains("Claude") && model.dirty && model.draft == invalid)
            assert(front.requests.count == 1)
            model.draft.claudeImplementationModel = "valid-id"
            let valid = model.draft
            model.save(); front.complete(.failure(failure))
            assert(model.messageIsError && model.message == "fixture conflict" && model.dirty && model.draft == valid)
            model.save(); front.complete(.success(before))
            assert(!model.messageIsError && !model.dirty)
            model.draft.mode = "force"
            model.inspect(); back.complete(.failure(failure))
            assert(model.externalReadUnreadable && model.externalChanges.isEmpty)
            model.save(); front.complete(.failure(failure))
            let count = front.requests.count
            model.discard()
            assert(!model.messageIsError && model.message.contains("已放弃草稿") && !model.dirty && front.requests.count == count)
        }

        // Token Plan recommendations can differ from the disk draft without user input.
        do {
            var prefilled = before
            var prefilledValues = values
            prefilledValues["model_services"] = [
                "active_api": "custom_api", "profiles": ["token_plan": ["base_url": "", "responses_base_url": ""]],
            ] as [String: Any]
            prefilled["values"] = prefilledValues
            let (model, front, back) = makeModel(prefilled)
            assert(model.dirty && model.saved.profiles["token_plan"]?.responsesBaseURL == "")
            assert(model.draft.profiles["token_plan"]?.responsesBaseURL == ServiceProfile.initial("token_plan").responsesBaseURL)
            var updated = after
            prefilledValues["primary_model"] = "external"
            updated["values"] = prefilledValues
            inspect(model, using: back, snapshot: updated)
            assertLoad(back)
            back.complete(.success(updated))
            assert(model.dirty && model.saved.primaryModel == "external")
            model.discard()
            assert(!model.dirty && model.draft == model.saved && front.requests.count == 1)
            inspect(model, using: back, snapshot: prefilled)
            assertLoad(back)
            back.complete(.success(prefilled))
            assert(model.dirty && model.saved.primaryModel == "fixture")
        }
        print("PASS: AppModel external changes, interrupted reloads, window-open refresh, and save errors")
    }

    @MainActor static func checkAgyHost() {
        let front = FakeClient()
        let model = AppModel(foreground: front, background: FakeClient(), automatic: false)
        let claude: [String: Any] = [
            "host": "claude", "channel": "codex", "mode": "rules", "primary_model": "fixture-claude",
            "claude_model": "fixture-claude", "codex_model": "fixture-codex",
            "claude_channel": "codex", "codex_channel": "qoder", "fallback_channels": ["qwen"],
            "reviewer_model": "claude-review-id", "qwen_executor": "codex", "claude_qwen_executor": "codex",
        ]
        func load(_ values: [String: Any]) {
            model.refresh()
            front.complete(.success(["values": values, "signature": "fixture"]))
        }
        func postedValues() -> [String: Any] {
            assert(front.requests.last?["action"] as? String == "save")
            return front.requests.last?["values"] as! [String: Any]
        }

        load(claude)
        assert(!model.dirty && model.draft.claudeImplementationProvider == "claude-agent")
        model.draft.primaryModel = "unsaved-claude"
        model.switchHost("agy")
        assert(model.draft.host == "agy" && model.draft.primaryModel.isEmpty && model.reviewFamily == "codex")
        assert(model.draft.implementationModelEnabled && !model.reviewUsesQwen && !model.showsReviewAddress)
        assert(model.primaryOptions == [ModelOption(id: "", label: ModelCatalog.agyManaged)])
        assert(model.reviewerOptions.first == ModelOption(id: "", label: "CLI 默认"))
        assert(model.secondaryOptions.first == ModelOption(id: "", label: "未配置（实施阶段停止）"))
        model.switchReviewFamily("token_plan")
        model.switchReviewFamily("qoder")
        assert(model.reviewFamily == "codex")
        model.draft.mode = "solo"
        assert(!model.draft.implementationModelEnabled)
        model.draft.mode = "rules"
        model.switchHost("claude")
        assert(model.draft.primaryModel == "unsaved-claude" && model.draft.reviewerModel == "claude-review-id")
        model.discard()

        model.menuHost("agy")
        let menu = postedValues()
        assert(menu["host"] as? String == "agy" && menu["channel"] as? String == "codex")
        assert(menu["agy_review_model"] == nil && menu["agy_implementation_model"] == nil)
        assert((menu["host_updates"] as? [String: [String: String]])?.isEmpty == true)
        var agy = claude
        agy["host"] = "agy"; agy["primary_model"] = ""
        front.complete(.success(["values": agy]))
        assert(model.title == "AGY → Codex · CLI 默认" && !model.dirty)

        model.draft.agyReviewModel = "custom/review"
        model.save()
        let review = postedValues()
        assert(review["agy_review_model"] as? String == "custom/review" && review["agy_implementation_model"] == nil)
        assert(review["agy_relay"] == nil && review["reviewer_model"] as? String == "claude-review-id")
        agy["agy_review_model"] = "custom/review"
        agy["agy_relay"] = ["review": ["effort": "high"], "implementation": NSNull()] as [String: Any]
        front.complete(.success(["values": agy]))
        assert(model.title == "AGY → Codex · custom/review" && model.draft.agyReviewEffort == "high")
        assert(model.reviewerOptions.contains { $0.id == "custom/review" })

        model.switchHost("claude")
        model.draft.primaryModel = "changed-claude"
        model.switchHost("agy")
        model.draft.agyImplementationModel = "custom/implementation"
        model.save()
        let implementation = postedValues()
        assert(implementation["agy_implementation_model"] as? String == "custom/implementation" && implementation["agy_review_model"] == nil)
        assert(implementation["host_updates"] as? [String: [String: String]] == ["claude": ["primary_model": "changed-claude"]])
        agy["agy_implementation_model"] = "custom/implementation"
        agy["agy_relay"] = ["review": ["effort": "high"], "implementation": ["effort": "xhigh"]]
        agy["claude_model"] = "changed-claude"
        front.complete(.success(["values": agy]))
        assert(model.draft.agyImplementationEffort == "xhigh" && !model.secondaryOptions.contains { $0.id.isEmpty })
        let requests = front.requests.count
        for invalid in ["", "a b"] {
            model.draft.agyImplementationModel = invalid
            model.save()
            assert(front.requests.count == requests && model.message.contains("AGY 的 Codex 实施模型 ID"))
        }
        model.discard()

        var invalid = claude
        invalid["agy_relay_error"] = "agy_relay.implementation 无效"
        load(invalid)
        let beforeMenu = front.requests.count
        model.menuHost("agy")
        assert(front.requests.count == beforeMenu && model.draft.host == "claude")
        assert(model.message.contains("AGY 接力配置无效"))
        model.switchHost("agy")
        assert(!model.draft.implementationModelEnabled && !model.draft.agyRelayError.isEmpty)
        print("PASS: AppModel AGY host, draft isolation, incremental saves, and soft errors")
    }

    @MainActor static func checkAgyDualPlan(_ snapshot: [String: Any]) {
        let front = FakeClient()
        let model = AppModel(foreground: front, background: FakeClient(), automatic: false)
        var state = snapshot
        var values = snapshot["values"] as! [String: Any]
        values["host"] = "agy"
        values["channel"] = "codex"
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(!model.draft.agyDualPlanEnabled && model.draft.agyDualPlanError.isEmpty)
        assert(model.draft.agyDualPlanDetail == "Claude · Claude 默认模型 · 默认思考深度 · AGY 汇总最终方案")

        values["agy_dual_plan"] = ["partner": "claude", "model": "haiku", "effort": "high", "synthesizer": "claude"]
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(model.draft.agyDualPlanDetail == "Claude · haiku · high · Claude 汇总最终方案")
        for enabled in [true, false] {
            model.draft.agyDualPlanEnabled = enabled
            assert(model.dirty)
            model.save()
            let payload = front.requests.last?["values"] as! [String: Any]
            assert(payload["agy_dual_plan_enabled"] as? Bool == enabled)
            assert(payload["dual_plan_enabled"] == nil && payload["agy_dual_plan"] == nil)
            values["agy_dual_plan_enabled"] = enabled
            state["values"] = values
            front.complete(.success(state))
            assert(model.saved.agyDualPlanEnabled == enabled && !model.dirty)
        }

        let error = "agy_relay.dual_plan.effort 无效"
        values["agy_dual_plan_error"] = error
        values.removeValue(forKey: "agy_dual_plan")
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(model.draft.agyDualPlanError == error && model.draft.agyRelayError.isEmpty)
        model.draft.mode = "auto"
        model.save()
        let ordinary = front.requests.last?["values"] as! [String: Any]
        assert(ordinary["mode"] as? String == "auto" && ordinary["agy_dual_plan_enabled"] == nil)
        front.complete(.success(state))
        model.menuMode("solo")
        let menu = front.requests.last?["values"] as! [String: Any]
        assert(menu["mode"] as? String == "solo" && menu["agy_dual_plan_enabled"] == nil)
        front.complete(.success(state))
        print("PASS: AppModel AGY dual-plan settings, summaries, and isolated save payloads")
    }

    @MainActor static func checkDualPlanAndReviewAddress(_ snapshot: [String: Any]) {
        let front = FakeClient()
        let model = AppModel(foreground: front, background: FakeClient(), automatic: false)
        model.refresh(); front.complete(.success(snapshot))
        assert(!model.draft.dualPlanEnabled, "Old snapshots default to dual plan off")
        assert(model.showsReviewAddress, "Codex→Qwen shows the Responses address")

        var state = snapshot
        var values = snapshot["values"] as! [String: Any]
        values["host"] = "claude"; values["channel"] = "same-family"; values["fallback_channels"] = ["qwen"]
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(model.showsReviewAddress, "Claude same-family with Qwen fallback shows the Responses address")

        values["channel"] = "codex"; values["dual_plan_enabled"] = false
        values["dual_plan"] = ["partner": "agy", "model": "gemini-4-neon", "effort": "xhigh",
                               "synthesizer": "claude", "on_failure": "stop"]
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(model.reviewUsesQwen && !model.showsReviewAddress, "Claude→Codex hides the address even with Qwen fallback")
        model.switchReviewFamily("custom_api")
        assert(model.draft.channel == "qwen" && model.showsReviewAddress, "Claude custom API shows the Responses address")
        model.discard()

        assert(!model.dirty && !model.draft.dualPlanEnabled && model.draft.dualPlanError.isEmpty)
        assert(model.draft.dualPlanDetail == "AGY · gemini-4-neon · xhigh · Claude 汇总最终方案")
        model.draft.dualPlanEnabled = true
        assert(model.dirty)
        model.save()
        let enabledSave = front.requests.last?["values"] as! [String: Any]
        assert(enabledSave["dual_plan_enabled"] as? Bool == true)
        assert(enabledSave["dual_plan"] == nil)
        values["dual_plan_enabled"] = true
        state["values"] = values
        front.complete(.success(state))
        assert(model.saved.dualPlanEnabled && !model.dirty)

        model.draft.reviewerModel = "new-review-model"
        model.save()
        let ordinarySave = front.requests.last?["values"] as! [String: Any]
        assert(ordinarySave["dual_plan_enabled"] == nil, "Saving unrelated changes must omit dual_plan_enabled")
        front.complete(.success(state))
        model.menuMode("solo")
        let menuSave = front.requests.last?["values"] as! [String: Any]
        assert(menuSave["mode"] as? String == "solo" && menuSave["dual_plan_enabled"] == nil,
               "Menu mode saves must omit dual_plan_enabled")
        front.complete(.success(state))

        model.draft.dualPlanEnabled = false
        model.save()
        let disabledSave = front.requests.last?["values"] as! [String: Any]
        assert(disabledSave["dual_plan_enabled"] as? Bool == false && disabledSave["dual_plan"] == nil)
        values["dual_plan_enabled"] = false
        state["values"] = values
        front.complete(.success(state))
        assert(!model.saved.dualPlanEnabled && !model.draft.dualPlanEnabled && !model.dirty)

        values["dual_plan"] = ["model": NSNull(), "effort": NSNull(), "synthesizer": "agy", "on_failure": "fallback"]
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(model.draft.dualPlanModel.isEmpty && model.draft.dualPlanEffort.isEmpty)
        assert(model.draft.dualPlanDetail == "AGY · AGY 默认模型 · 默认思考深度 · AGY 汇总最终方案")

        let planError = "claude_relay.dual_plan.effort 无效"
        values["dual_plan_enabled"] = false; values["dual_plan_error"] = planError
        values.removeValue(forKey: "dual_plan")
        state["values"] = values
        model.refresh(); front.complete(.success(state))
        assert(!model.draft.dualPlanEnabled && model.draft.dualPlanError == planError)
        print("PASS: AppModel dual-plan load, summaries, save payloads, and review address visibility")
    }
}
