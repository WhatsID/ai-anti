import SwiftUI
import AppKit

// Separate clients keep background cancellation away from foreground requests.
@MainActor protocol AppRequesting: AnyObject {
    func start(_ payload: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void)
    func cancel()
}

@MainActor final class BridgeClient: AppRequesting {
    private let bridge = Bridge()
    func start(_ payload: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        bridge.prepare()
        let bridge = self.bridge
        DispatchQueue.global(qos: .userInitiated).async {
            let result: Result<[String: Any], Error>
            do { result = .success(try bridge.request(payload)) } catch { result = .failure(error) }
            DispatchQueue.main.async { completion(result) }
        }
    }
    func cancel() { bridge.cancel() }
}

struct Draft: Equatable {
    var host = "claude"
    var primaryModel = ""
    var channel = "codex"
    var reviewerModel = ""
    var activeAPI = "custom_api"
    var profiles = Dictionary(uniqueKeysWithValues: ReviewService.api.map { ($0, ServiceProfile.initial($0)) })
    var profile: ServiceProfile {
        get { profiles[activeAPI] ?? ServiceProfile.initial(activeAPI) }
        set { profiles[activeAPI] = newValue }
    }
    var autoFailover = false
    var backupProfile = ServiceProfile.initial("token_plan_backup")
    var backupKeyInput: String {
        get {
            if backupProfile.action == "replace" { return backupProfile.secret }
            if backupProfile.action == "clear" { return "" }
            return backupProfile.credentialPaths.isEmpty ? "" : Self.credentialMask
        }
        set {
            guard newValue != backupKeyInput else { return }
            if newValue == Self.credentialMask { backupProfile.action = "retain"; backupProfile.secret = "" }
            else {
                backupProfile.secret = newValue.replacingOccurrences(of: "•", with: "")
                backupProfile.action = backupProfile.secret.isEmpty ? "clear" : "replace"
            }
        }
    }
    var accountDictionary: [String: Any] { ["version": 1, "auto_failover": autoFailover, "backup": ["credential_paths": backupProfile.credentialPaths, "credential_key": backupProfile.credentialKey]] }
    var service: String { channel == "qwen" ? activeAPI : channel }
    var qwenModel: String { get { profile.model } set { profile.model = newValue } }
    var implementationProvider = "codex"
    var secondaryModel = ""
    var tokenPlanImplementationModel = ModelCatalog.implementationGLM
    var claudeImplementationProvider = "claude-agent"
    var claudeImplementationModel = ""
    var codexModels = ModelCatalog.codex
    var implementationEnabled: Bool { mode != "solo" }
    var implementationModelEnabled: Bool {
        implementationEnabled && (host == "codex" || claudeImplementationProvider == "codex")
    }
    var implementationModel: String {
        get { implementationProvider == "token_plan" ? tokenPlanImplementationModel : implementationProvider == "qoder" ? ModelCatalog.implementationGLM : secondaryModel }
        set { if implementationProvider == "codex" { secondaryModel = newValue } else if implementationProvider == "token_plan" { tokenPlanImplementationModel = newValue } }
    }
    var implementationURL: String {
        get { profiles["token_plan"]!.responsesBaseURL }
        set { profiles["token_plan"]!.responsesBaseURL = newValue }
    }
    var implementationKeyInput: String {
        get { var shared = self; shared.activeAPI = "token_plan"; return shared.apiKeyInput }
        set { var shared = self; shared.activeAPI = "token_plan"; shared.apiKeyInput = newValue; profiles["token_plan"] = shared.profile }
    }
    var mode = "rules"
    var baseURL: String { get { profile.baseURL } set { profile.baseURL = newValue } }
    var responsesBaseURL: String { get { profile.responsesBaseURL } set { profile.responsesBaseURL = newValue } }
    static func validModelID(_ id: String) -> Bool { !id.isEmpty && !id.unicodeScalars.contains { $0.properties.isWhitespace } }
    var secret: String { get { profile.secret } set { profile.secret = newValue } }
    // A display-only marker keeps stored credentials out of the UI process.
    static let credentialMask = String(repeating: "•", count: 50)
    var apiKeyInput: String {
        get {
            if profile.action == "replace" { return profile.secret }
            if profile.action == "clear" { return "" }
            return profile.credentialPaths.isEmpty ? "" : Self.credentialMask
        }
        set {
            guard newValue != apiKeyInput else { return }
            if newValue == Self.credentialMask {
                profile.action = "retain"; profile.secret = ""
            } else {
                let value = newValue.replacingOccurrences(of: "•", with: "")
                profile.secret = value
                profile.action = value.isEmpty ? "clear" : "replace"
            }
        }
    }
    var secretAction: String { get { profile.action } set { profile.action = newValue; if newValue != "replace" { profile.secret = "" } } }
    var serviceDictionary: [String: Any] { ["version": 1, "active_api": activeAPI, "profiles": profiles.mapValues { $0.dictionary }] }
    var secretActions: [String: Any] {
        var result: [String: Any] = profiles.mapValues { ["action": $0.action == "replace" && $0.secret.isEmpty ? "retain" : $0.action, "value": $0.secret] }
        result["token_plan_backup"] = ["action": backupProfile.action == "replace" && backupProfile.secret.isEmpty ? "retain" : backupProfile.action, "value": backupProfile.secret]
        return result
    }
    var dictionary: [String: Any] {
        ["token_plan_accounts": accountDictionary, "model_services": serviceDictionary, "host": host, "primary_model": primaryModel, "channel": channel, "reviewer_model": reviewerModel, "qwen_model": qwenModel, "secondary_model": secondaryModel, "token_plan_implementation_model": tokenPlanImplementationModel, "implementation_provider": implementationProvider, "mode": mode, "base_url": baseURL, "responses_base_url": responsesBaseURL, "qwen_executor": "codex",
         "claude_implementation_provider": claudeImplementationProvider, "claude_implementation_model": claudeImplementationModel,
         "claude_qwen_executor": "codex", "codex_models": codexModels]
    }
}

@MainActor final class AppModel: ObservableObject {
    static let shared = AppModel()
    @Published var draft = Draft() {
        didSet {
            if loaded && draft != oldValue { checkSignature = nil; status = "未检测（设置已变化）" }
        }
    }
    @Published var saved = Draft()
    @Published var busy = false
    @Published var loaded = false
    @Published var message = "正在读取配置…"
    @Published var status = "未检测"
    @Published var credentialPresent = false
    @Published var relativeCredentials = false
    var selectedCredentialMessage: String {
        if draft.secretAction == "clear" { return "保存后停用此服务的凭证查找" }
        if draft.secretAction == "replace" && !draft.secret.isEmpty { return "已输入此服务的新密钥（尚未保存）" }
        if draft.activeAPI != saved.activeAPI { return draft.profile.credentialPaths.isEmpty ? "此服务未配置凭证查找" : "此服务凭证待检测" }
        return credentialPresent ? "发现此服务的凭证文件；是否可解析需手动检查" : "未发现此服务的凭证文件"
    }
    @Published var canRestore = false
    @Published var fallbacks: [String] = []
    @Published private(set) var executorMigrationPending = false
    @Published var sources: [String: String] = [:]
    @Published var elapsed = 0
    @Published var checking = false
    private var fingerprints: [String: Any] = [:]
    private var signature = ""
    private var checkSignature: String?
    private var watching: Timer?
    private var menuSaving = false
    private var defaults: [String: String] = [:]
    private let foreground: AppRequesting
    private let background: AppRequesting
    private var inspectInFlight = false
    private var inspectGeneration = 0
    private var timer: Timer?
    private struct HostDraft: Equatable {
        var primaryModel: String
        var channel: String
    }
    private var hostDrafts: [String: HostDraft] = [:]
    private var savedHosts: [String: HostDraft] = [:]
    private var currentHosts: [String: HostDraft] {
        var hosts = hostDrafts
        hosts[draft.host] = HostDraft(primaryModel: draft.primaryModel, channel: draft.channel)
        return hosts
    }
    var dirty: Bool { executorMigrationPending || draft != saved || (loaded && currentHosts != savedHosts) }
    private func resetDrafts() { draft = saved; hostDrafts = savedHosts }
    var claudeFallbacks: [String] {
        ([savedHosts["claude"]?.channel ?? "codex"] + fallbacks).filter { $0 != draft.channel }
    }
    var reviewUsesQwen: Bool {
        draft.channel == "qwen" || (draft.host == "claude" && claudeFallbacks.contains("qwen"))
    }
    var reviewExecutorLabel: String {
        switch draft.channel {
        case "qwen": return "Codex CLI · Responses"
        case "qoder": return "Qoder CLI"
        default: return "Codex CLI"
        }
    }
    var reviewFamily: String { draft.service }
    var savedReviewFamily: String { saved.service }
    var title: String {
        let service = ReviewService.title(savedReviewFamily)
        let detail: String
        switch saved.channel {
        case "codex": detail = saved.reviewerModel.isEmpty ? "CLI 默认" : saved.reviewerModel
        case "qoder": detail = "Kimi-K3.1-Pro"
        case "qwen": detail = "Responses · " + saved.qwenModel
        default: detail = saved.qwenModel
        }
        return "\(saved.host == "claude" ? "Claude" : "Codex") → \(service) · \(detail)"
    }
    var primaryOptions: [ModelOption] {
        ModelCatalog.options(draft.host, preserving: [defaults[draft.host] ?? "", draft.host == saved.host ? saved.primaryModel : "", draft.primaryModel], emptyLabel: "不修改（沿用当前设置）", codexModels: draft.codexModels)
    }
    var secondaryOptions: [ModelOption] {
        if draft.host == "claude" {
            return ModelCatalog.options("codex", preserving: [saved.claudeImplementationModel, draft.claudeImplementationModel],
                                        emptyLabel: "请选择模型", codexModels: draft.codexModels)
        }
        if draft.implementationProvider == "token_plan" {
            return ModelCatalog.tokenPlanImplementation.map { ModelOption(id: $0, label: $0) }
        }
        if draft.implementationProvider == "qoder" { return [ModelOption(id: ModelCatalog.implementationGLM, label: "GLM-5.3")] }
        return ModelCatalog.options("codex", preserving: [saved.secondaryModel, draft.secondaryModel], emptyLabel: "未指定", codexModels: draft.codexModels)
    }
    var reviewerOptions: [ModelOption] {
        if draft.channel == "qoder" { return [ModelOption(id: "kmodel_latest", label: "Kimi-K3.1-Pro")] }
        if draft.channel == "codex" {
            return ModelCatalog.options("codex", preserving: [saved.reviewerModel, draft.reviewerModel],
                                        emptyLabel: "CLI 默认", codexModels: draft.codexModels)
        }
        let ids = draft.activeAPI == "token_plan" ? ["qwen4-max", "qwen3.8-max", "deepseek-v4-pro"] : []
        var result = ids.map { ModelOption(id: $0, label: $0 == "qwen4-max" ? "Qwen4-Max" : $0 == "qwen3.8-max" ? "Qwen3.8-Max" : "DeepSeek-V4-Pro") }
        for value in [saved.profiles[draft.activeAPI]?.model ?? "", draft.qwenModel] where !result.contains(where: { $0.id == value }) {
            result.append(ModelOption(id: value, label: value.isEmpty ? "请选择模型" : "已有配置：" + value))
        }
        return result
    }
    func switchReviewFamily(_ service: String) {
        guard !busy, loaded, service != reviewFamily else { return }
        guard ReviewService.api.contains(service) || (service == "qoder" && draft.host == "codex") || (service == "codex" && draft.host == "claude") else { return }
        if ReviewService.api.contains(service) { draft.activeAPI = service; draft.channel = "qwen" }
        else { draft.channel = service }
        status = "未检测（服务已变化）"; checkSignature = nil
    }
    let modes = [("solo", "独立完成"), ("auto", "自动判断"), ("rules", "按规模评审"), ("force", "始终评审")]
    init(foreground: AppRequesting? = nil, background: AppRequesting? = nil, automatic: Bool = true) {
        self.foreground = foreground ?? BridgeClient(); self.background = background ?? BridgeClient()
        if automatic {
            refresh()
            watching = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in Task { @MainActor in self?.inspect() } }
        }
    }
    func inspect() {
        guard loaded && !busy && !inspectInFlight else { return }
        inspectInFlight = true
        let generation = inspectGeneration
        background.start(["action": "inspect"]) { [weak self] result in
            guard let self else { return }
            self.inspectInFlight = false
            guard generation == self.inspectGeneration else { return }
            switch result {
            case .success(let data):
                if let prior = self.checkSignature, prior != data["signature"] as? String {
                    self.checkSignature = nil; self.status = "未检测（配置或凭证已变化）"
                }
            case .failure:
                if self.status != "未检测（当前配置不可验证）" {
                    self.checkSignature = nil; self.status = "未检测（当前配置不可验证）"
                }
            }
        }
    }
    private func invalidateInspect() {
        inspectGeneration += 1
        if inspectInFlight { background.cancel() }
        // Keep the flight occupied until its completion, even after cancellation.
    }
    func stopBackground() { watching?.invalidate(); watching = nil; invalidateInspect() }
    func operation(_ payload: [String: Any], checked: Bool = false) {
        guard !busy else { return }
        invalidateInspect()
        busy = true; checking = checked
        if checked {
            elapsed = 0
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in Task { @MainActor in self?.elapsed += 1 } }
            message = payload["action"] as? String == "real" ? "合成检测进行中；API 最多 30 分钟，Qoder 最多 10 分钟，可取消。" : "正在进行本地检查…"
        }
        foreground.start(payload) { [weak self] result in
                guard let self else { return }
                self.busy = false; self.checking = false; self.timer?.invalidate(); self.timer = nil
                switch result {
                case .failure(let error):
                    if self.menuSaving { self.resetDrafts() }; self.menuSaving = false
                    self.message = error.localizedDescription
                    if checked {
                        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
                        let kind = payload["action"] as? String == "real" ? "真实合成检测" : "本地检查"
                        self.status = kind + "失败：" + error.localizedDescription + "\n" + formatter.string(from: Date())
                        self.checkSignature = self.signature
                    }
                case .success(let data):
                    if checked {
                        self.checkSignature = data["signature"] as? String
                        self.status = (data["message"] as? String ?? "检测完成") + "\n" + (data["checked_at"] as? String ?? "")
                        if payload["action"] as? String == "real" {
                            if data["model_evidence"] as? String == "requested_only" {
                                self.status += " · 仅确认请求模型：" + (data["requested_model"] as? String ?? "未知") + "；服务端实际模型未知"
                            } else {
                                self.status += " · 实际模型：" + (data["actual_model"] as? String ?? "未知")
                            }
                        }
                        self.message = "检测完成，用时 \(self.elapsed) 秒"
                    } else {
                        let menuAPI = self.menuSaving && self.draft.channel == "qwen"
                        self.menuSaving = false
                        self.apply(data)
                        self.message = payload["action"] as? String == "load" ? "已读取默认配置" : "配置已回读确认；用于后续启动或调用"
                        if menuAPI { self.message += "；请确认服务地址和密钥支持所选模型" }
                    }
                }
        }
    }
    private func apply(_ data: [String: Any]) {
        guard let v = data["values"] as? [String: Any] else { message = "配置响应无效"; return }
        func text(_ key: String) -> String { v[key] as? String ?? "" }
        var d = Draft(); d.host = text("host"); d.primaryModel = text("primary_model"); d.channel = text("channel")
        if let accounts = v["token_plan_accounts"] as? [String: Any] {
            d.autoFailover = accounts["auto_failover"] as? Bool ?? false
            if let backup = accounts["backup"] as? [String: Any] {
                d.backupProfile.credentialPaths = backup["credential_paths"] as? [String] ?? []
                d.backupProfile.credentialKey = backup["credential_key"] as? String ?? "model_services.token_plan_backup.value"
            }
        }
        if let services = v["model_services"] as? [String: Any], let profiles = services["profiles"] as? [String: [String: Any]] {
            d.activeAPI = services["active_api"] as? String ?? "custom_api"
            for id in ReviewService.api {
                if let p = profiles[id] {
                    var profile = ServiceProfile.initial(id)
                    profile.model = p["model"] as? String ?? ""; profile.baseURL = p["base_url"] as? String ?? ""; profile.responsesBaseURL = p["responses_base_url"] as? String ?? ""
                    profile.credentialPaths = p["credential_paths"] as? [String] ?? []; profile.credentialKey = p["credential_key"] as? String ?? ""
                    d.profiles[id] = profile
                }
            }
        }
        d.reviewerModel = text("reviewer_model"); d.qwenModel = text("qwen_model"); d.secondaryModel = text("secondary_model"); d.tokenPlanImplementationModel = v["token_plan_implementation_model"] as? String ?? ModelCatalog.implementationGLM; d.implementationProvider = v["implementation_provider"] as? String ?? "codex"; d.mode = text("mode"); d.baseURL = text("base_url"); d.responsesBaseURL = text("responses_base_url")
        d.claudeImplementationProvider = v["claude_implementation_provider"] as? String ?? "claude-agent"
        d.claudeImplementationModel = text("claude_implementation_model")
        d.codexModels = v["codex_models"] as? [String] ?? ModelCatalog.codex
        executorMigrationPending = text("qwen_executor") == "claude" || text("claude_qwen_executor") == "claude"
        saved = d; draft = d; loaded = true
        // Legacy configurations may only contain the endpoint for one host.
        // Suggest missing Token Plan defaults in the draft; keep the disk snapshot intact.
        if var token = draft.profiles["token_plan"] {
            let defaults = ServiceProfile.initial("token_plan")
            if token.baseURL.isEmpty { token.baseURL = defaults.baseURL }
            if token.responsesBaseURL.isEmpty { token.responsesBaseURL = defaults.responsesBaseURL }
            draft.profiles["token_plan"] = token
        }
        let channels = ["claude": text("claude_channel"), "codex": text("codex_channel")]
        defaults = ["claude": text("claude_model"), "codex": text("codex_model")]
        savedHosts = [:]
        for host in ["claude", "codex"] {
            savedHosts[host] = HostDraft(primaryModel: defaults[host] ?? "", channel: channels[host].flatMap { $0.isEmpty ? nil : $0 } ?? (host == "claude" ? "codex" : "qwen"))
        }
        savedHosts[d.host] = HostDraft(primaryModel: d.primaryModel, channel: d.channel)
        hostDrafts = savedHosts
        fingerprints = data["fingerprints"] as? [String: Any] ?? [:]
        signature = data["signature"] as? String ?? ""
        if let checkSignature, checkSignature != signature { status = "未检测（配置或凭证已变化）"; self.checkSignature = nil }
        credentialPresent = v["credential_present"] as? Bool ?? false
        relativeCredentials = v["relative_credentials"] as? Bool ?? false
        fallbacks = v["fallback_channels"] as? [String] ?? []
        canRestore = data["can_restore"] as? Bool ?? false
        sources = data["sources"] as? [String: String] ?? [:]
    }
    func refresh() { operation(["action": "load"]) }
    func save() {
        if draft.claudeImplementationProvider == "codex" && !Draft.validModelID(draft.claudeImplementationModel) {
            message = "Claude 的 Codex 实施模型 ID 必须为非空且不含空白的字符串"
            return
        }
        var values = draft.dictionary
        if saved.claudeImplementationProvider == "claude-agent" && draft.claudeImplementationProvider == "claude-agent" {
            values.removeValue(forKey: "claude_implementation_provider")
        }
        var updates: [String: [String: String]] = [:]
        for (host, edit) in currentHosts {
            guard let original = savedHosts[host] else { continue }
            var fields: [String: String] = [:]
            if edit.primaryModel != original.primaryModel { fields["primary_model"] = edit.primaryModel }
            if edit.channel != original.channel { fields["channel"] = edit.channel }
            if !fields.isEmpty { updates[host] = fields }
        }
        values["host_updates"] = updates
        operation(["action": "save", "values": values, "fingerprints": fingerprints, "secret_actions": draft.secretActions])
    }
    func restore() { operation(["action": "restore", "fingerprints": fingerprints]) }
    func check(real: Bool = false) {
        guard !dirty else { message = "请先保存或放弃草稿，再检测已保存配置"; return }
        operation(["action": real ? "real" : "local"], checked: true)
    }
    func cancel() { foreground.cancel(); message = "正在取消并清理检测进程…" }
    func quit() {
        stopBackground(); foreground.cancel()
        if busy {
            message = "退出前正在清理检测进程…"
            DispatchQueue.main.asyncAfter(deadline: .now() + 6) { NSApplication.shared.terminate(nil) }
        } else { NSApplication.shared.terminate(nil) }
    }
    func switchHost(_ host: String) {
        guard !busy, loaded else { message = "配置尚未读取或正在忙碌，暂不能切换主模型"; return }
        guard ["claude", "codex"].contains(host), host != draft.host else { return }
        hostDrafts = currentHosts
        guard let target = hostDrafts[host] else { return }
        draft.host = host; draft.primaryModel = target.primaryModel; draft.channel = target.channel
    }
    func menuHost(_ host: String) {
        guard !busy, loaded, !dirty else { message = "设置草稿尚未保存或配置尚未读取、忙碌"; return }
        switchHost(host)
        guard dirty, draft.host == host else { return }
        if draft.channel == "qwen" && (draft.qwenModel.isEmpty || draft.responsesBaseURL.isEmpty || draft.profile.credentialPaths.isEmpty) {
            message = "请在设置中完成所选服务的地址、模型与认证后保存"; return
        }
        menuSaving = true; save()
    }
    func menuChannel(_ family: String) {
        guard !busy, loaded, !dirty else { message = "设置草稿尚未保存或配置忙碌"; return }
        switchReviewFamily(family)
        guard dirty else { return }
        if draft.channel == "qwen" && (draft.qwenModel.isEmpty || draft.responsesBaseURL.isEmpty || draft.profile.credentialPaths.isEmpty) {
            message = "请在设置中完成所选服务的地址、模型与认证后保存"; return
        }
        menuSaving = true; save()
    }
    func menuMode(_ mode: String) { guard !busy, loaded, !dirty else { message = "设置草稿尚未保存"; return }; draft.mode = mode; menuSaving = true; save() }
    func discard() { resetDrafts() }
}
