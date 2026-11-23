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
    var agyReviewModel = ""
    var agyImplementationModel = ""
    var agyReviewEffort = ""
    var agyImplementationEffort = ""
    var agyRelayError = ""
    var agyDualPlanEnabled = false
    var agyDualPlanPartner = "claude"
    var agyDualPlanModel = ""
    var agyDualPlanEffort = ""
    var agyDualPlanSynthesizer = "agy"
    var agyDualPlanError = ""
    var agyDualPlanDetail: String {
        [agyDualPlanPartner == "claude" ? "Claude" : agyDualPlanPartner,
         agyDualPlanModel.isEmpty ? "Claude 默认模型" : agyDualPlanModel,
         agyDualPlanEffort.isEmpty ? "默认思考深度" : agyDualPlanEffort,
         agyDualPlanSynthesizer == "claude" ? "Claude 汇总最终方案" : "AGY 汇总最终方案"].joined(separator: " · ")
    }
    var dualPlanEnabled = false
    var dualPlanPartner = "agy"
    var dualPlanModel = ""
    var dualPlanEffort = ""
    var dualPlanSynthesizer = "claude"
    var dualPlanError = ""
    var dualPlanDetail: String {
        [dualPlanPartner == "agy" ? "AGY" : dualPlanPartner,
         dualPlanModel.isEmpty ? "AGY 默认模型" : dualPlanModel,
         dualPlanEffort.isEmpty ? "默认思考深度" : dualPlanEffort,
         dualPlanSynthesizer == "agy" ? "AGY 汇总最终方案" : "Claude 汇总最终方案"].joined(separator: " · ")
    }
    var codexModels = ModelCatalog.codex
    var implementationEnabled: Bool { mode != "solo" }
    var implementationModelEnabled: Bool {
        implementationEnabled && (host == "codex" || (host == "agy" && agyRelayError.isEmpty) || claudeImplementationProvider == "codex")
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
    @Published private(set) var messageIsError = false
    @Published private(set) var externalChanges: [String] = []
    @Published private(set) var externalReadUnreadable = false
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
    @Published private(set) var cliStatuses: [CLIToolID: CLIStatus] = [:]
    @Published private(set) var cliProbing = false
    @Published private(set) var cliProbeError: String?
    @Published private(set) var cliConfigurationLoadFailed = false
    @Published private(set) var homebrewAvailable = false
    private var fingerprints: [String: Any] = [:]
    private var fingerprintValues: [String: String?] = [:]
    private var signature = ""
    private var checkSignature: String?
    private var watching: Timer?
    private var menuSaving = false
    private var defaults: [String: String] = [:]
    private let foreground: AppRequesting
    private let background: AppRequesting
    private let cliProbe: AppRequesting
    private let terminalLauncher: TerminalLaunching
    private var settingsOpen = false
    private var loadingConfiguration = false
    private var probeOnNextLoad = false
    private var automaticallyLoadedCLINames: [String]?
    private var cliProbeGeneration = 0
    private var cliProbeInFlight: (names: [String], force: Bool)?
    private var pendingCLIProbe: (names: [String], force: Bool)?
    private var legacyQwenExecutorHosts: Set<String> = []
    private var inspectInFlight = false
    private var inspectGeneration = 0
    private var failedAutoRefreshSnapshot: (signature: String?, fingerprints: [String: String?]?)?
    private var timer: Timer?
    private struct HostDraft: Equatable {
        var primaryModel: String
        var channel: String
    }
    private var hostDrafts: [String: HostDraft] = [:]
    private var savedHosts: [String: HostDraft] = [:]
    private var baselineDraft = Draft()
    private var baselineHosts: [String: HostDraft] = [:]
    private var currentHosts: [String: HostDraft] {
        var hosts = hostDrafts
        hosts[draft.host] = HostDraft(primaryModel: draft.primaryModel, channel: draft.channel)
        return hosts
    }
    var dirty: Bool { executorMigrationPending || draft != saved || (loaded && currentHosts != savedHosts) }
    private var isUntouched: Bool {
        ((draft == baselineDraft && currentHosts == baselineHosts) ||
         (draft == saved && currentHosts == savedHosts)) && !menuSaving
    }
    private func resetDrafts() { draft = saved; hostDrafts = savedHosts }
    var claudeFallbacks: [String] {
        ([savedHosts["claude"]?.channel ?? "codex"] + fallbacks).filter { $0 != draft.channel }
    }
    var reviewUsesQwen: Bool {
        if draft.host == "agy" { return false }
        return draft.channel == "qwen" || (draft.host == "claude" && claudeFallbacks.contains("qwen"))
    }
    var requiredCLIs: [RequiredCLI] {
        guard let host = CLIToolID(rawValue: draft.host), host != .qodercli else { return [] }
        var tools = [RequiredCLI(id: host, roles: ["主模型"])]
        guard draft.mode != "solo" else { return tools }

        func add(_ id: CLIToolID, _ role: String) {
            if let index = tools.firstIndex(where: { $0.id == id }) {
                tools[index].roles.append(role)
            } else {
                tools.append(RequiredCLI(id: id, roles: [role]))
            }
        }

        switch (host, draft.channel) {
        case (.claude, "codex"), (.claude, "qwen"), (.codex, "qwen"), (.agy, _):
            add(.codex, "评审")
        case (.claude, "same-family"), (.codex, "claude"):
            add(.claude, "评审")
        case (.codex, "qoder"):
            add(.qodercli, "评审")
        default:
            break
        }
        if reviewUsesQwen && legacyQwenExecutorHosts.contains(draft.host) {
            add(.claude, RequiredCLI.legacyQwenReviewRole)
        }

        if host == .codex {
            add(draft.implementationProvider == "qoder" ? .qodercli : .codex, "实施")
        } else if host == .agy || draft.claudeImplementationProvider == "codex" {
            add(.codex, "实施")
        }

        if host == .claude {
            if draft.dualPlanEnabled && draft.dualPlanError.isEmpty { add(.agy, "双方案规划") }
            if claudeFallbacks.contains(where: { $0 == "codex" || $0 == "qwen" }),
               !tools.contains(where: { $0.id == .codex }) { add(.codex, "回退评审") }
        } else if host == .agy && draft.agyDualPlanEnabled && draft.agyDualPlanError.isEmpty {
            add(.claude, "双方案规划")
        }
        return tools
    }
    var requiredCLINames: [String] { requiredCLIs.map { $0.id.rawValue } }
    var showsReviewAddress: Bool {
        reviewUsesQwen && !(draft.host == "claude" && draft.channel == "codex")
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
        if saved.host == "agy" { return "AGY → Codex · \(saved.agyReviewModel.isEmpty ? "CLI 默认" : saved.agyReviewModel)" }
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
        if draft.host == "agy" { return [ModelOption(id: "", label: ModelCatalog.agyManaged)] }
        return ModelCatalog.options(draft.host, preserving: [defaults[draft.host] ?? "", draft.host == saved.host ? saved.primaryModel : "", draft.primaryModel], emptyLabel: "不修改（沿用当前设置）", codexModels: draft.codexModels)
    }
    var secondaryOptions: [ModelOption] {
        if draft.host == "agy" {
            return ModelCatalog.options("codex", preserving: [saved.agyImplementationModel, draft.agyImplementationModel],
                                        emptyLabel: saved.agyImplementationModel.isEmpty ? "未配置（实施阶段停止）" : nil, codexModels: draft.codexModels)
        }
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
        if draft.host == "agy" {
            return ModelCatalog.options("codex", preserving: [saved.agyReviewModel, draft.agyReviewModel],
                                        emptyLabel: "CLI 默认", codexModels: draft.codexModels)
        }
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
        if draft.host == "agy" {
            guard service == "codex" else { return }
        } else {
            guard ReviewService.api.contains(service) || (service == "qoder" && draft.host == "codex") || (service == "codex" && draft.host == "claude") else { return }
        }
        if ReviewService.api.contains(service) { draft.activeAPI = service; draft.channel = "qwen" }
        else { draft.channel = service }
        status = "未检测（服务已变化）"; checkSignature = nil
    }
    let modes = [("solo", "独立完成"), ("auto", "自动判断"), ("rules", "按规模评审"), ("force", "始终评审")]
    init(foreground: AppRequesting? = nil, background: AppRequesting? = nil, cliProbe: AppRequesting? = nil,
         terminalLauncher: TerminalLaunching? = nil, automatic: Bool = true) {
        self.foreground = foreground ?? BridgeClient(); self.background = background ?? BridgeClient()
        self.cliProbe = cliProbe ?? BridgeClient()
        self.terminalLauncher = terminalLauncher ?? TerminalLauncher()
        if automatic {
            refresh()
            watching = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in Task { @MainActor in self?.inspect() } }
        }
    }
    func probeOnSettingsOpen() {
        settingsOpen = true
        recheckCLIs()
    }
    func settingsDidClose() {
        settingsOpen = false
        probeOnNextLoad = false
        automaticallyLoadedCLINames = nil
    }
    func ensureCLIStatus() {
        guard settingsOpen, loaded, !loadingConfiguration else { return }
        let names = requiredCLINames
        let automaticallyLoaded = automaticallyLoadedCLINames
        automaticallyLoadedCLINames = nil
        guard names != automaticallyLoaded else { return }
        requestCLIStatus(names: names, force: false)
    }
    func recheckCLIs() {
        automaticallyLoadedCLINames = nil
        if loadingConfiguration || !loaded {
            probeOnNextLoad = true
            if !busy { refresh() }
            return
        }
        requestCLIStatus(names: requiredCLINames, force: true)
    }
    private func finishCLILoad(succeeded: Bool) {
        loadingConfiguration = false
        let shouldProbe = probeOnNextLoad
        probeOnNextLoad = false
        if !loaded && !succeeded { cliConfigurationLoadFailed = true }
        if succeeded && shouldProbe { recheckCLIs() }
    }
    private func requestCLIStatus(names: [String], force: Bool) {
        if let active = cliProbeInFlight {
            // A settings redraw after load can request the selection already being probed.
            if pendingCLIProbe == nil && names == active.names && (active.force || !force) { return }
            let cancelActive = pendingCLIProbe == nil
            pendingCLIProbe = (names, force || (pendingCLIProbe?.force ?? false))
            cliProbeGeneration += 1
            if cancelActive { cliProbe.cancel() }
            return
        }
        let uncached = names.filter { name in
            CLIToolID(rawValue: name).map { cliStatuses[$0] == nil } ?? false
        }
        let requested = force ? names : uncached
        if cliProbeError != nil { cliProbeError = nil }
        guard !requested.isEmpty else {
            if cliProbing { cliProbing = false }
            return
        }
        cliProbeInFlight = (names, force)
        let generation = cliProbeGeneration
        if !cliProbing { cliProbing = true }
        cliProbe.start(["action": "cli_status", "names": requested]) { [weak self] result in
            guard let self else { return }
            self.cliProbeInFlight = nil
            if generation == self.cliProbeGeneration {
                switch result {
                case .success(let data):
                    self.applyCLIStatus(data, requested: requested)
                case .failure(let error):
                    self.cliProbeError = (error as? BridgeFailure)?.message ?? "CLI 检测请求失败"
                }
            }
            if let pending = self.pendingCLIProbe {
                self.pendingCLIProbe = nil
                self.requestCLIStatus(names: pending.names, force: pending.force)
            } else if self.cliProbing {
                self.cliProbing = false
            }
        }
    }
    private func applyCLIStatus(_ data: [String: Any], requested: [String]) {
        guard let items = data["items"] as? [String: [String: Any]],
              let managers = data["package_managers"] as? [String: Bool], let brew = managers["brew"] else {
            cliProbeError = "CLI 检测响应无效"
            return
        }
        var statuses = cliStatuses
        for name in requested {
            guard let id = CLIToolID(rawValue: name), let item = items[name], let status = CLIStatus(item) else {
                cliProbeError = "CLI 检测响应无效"
                return
            }
            statuses[id] = status
        }
        cliStatuses = statuses
        homebrewAvailable = brew
    }
    func launchCLIInstaller(toolID: CLIToolID, method: CLIInstallMethod) async throws {
        guard cliStatuses[toolID]?.offersInstallation == true,
              CLICatalog.tool(toolID).methods.contains(method) else {
            throw BridgeFailure(message: "此 CLI 当前没有可用的内置安装方式")
        }
        try await terminalLauncher.launch(toolID: toolID, method: method)
        setMessage("已在终端中打开安装脚本。完成安装和登录后请点击「重新检测」。")
    }
    private static func normalizeFingerprints(_ data: [String: Any]?) -> [String: String?]? {
        data?.mapValues { $0 as? String }
    }
    private static func changedLabels(from prior: [String: String?], to current: [String: String?]) -> [String] {
        let labels = [("ai", "ai-anti 配置"), ("claude", "Claude 设置"), ("codex", "Codex 配置"), ("secrets", "凭证文件")]
        return labels.compactMap { key, label in prior[key] != current[key] ? label : nil }
    }
    private func setMessage(_ text: String, isError: Bool = false) {
        if message != text { message = text }
        if messageIsError != isError { messageIsError = isError }
    }
    private func setExternalReadState(changes: [String], unreadable: Bool) {
        if externalChanges != changes { externalChanges = changes }
        if externalReadUnreadable != unreadable { externalReadUnreadable = unreadable }
    }
    private func markConfigurationUnverifiable() {
        checkSignature = nil
        let unverifiable = "未检测（当前配置不可验证）"
        if status != unverifiable { status = unverifiable }
    }
    func inspect() {
        guard loaded && !busy && !inspectInFlight else { return }
        inspectInFlight = true
        let generation = inspectGeneration
        background.start(["action": "inspect"]) { [weak self] result in
            guard let self else { return }
            guard generation == self.inspectGeneration else {
                self.inspectInFlight = false
                return
            }
            switch result {
            case .success(let data):
                let observedSignature = data["signature"] as? String
                let observedFingerprints = Self.normalizeFingerprints(data["fingerprints"] as? [String: Any])
                if let prior = self.checkSignature, prior != observedSignature {
                    self.checkSignature = nil; self.status = "未检测（配置或凭证已变化）"
                }
                let signatureChanged = observedSignature.map { $0 != self.signature } ?? false
                let fingerprintsChanged = observedFingerprints.map { $0 != self.fingerprintValues } ?? false
                guard signatureChanged || fingerprintsChanged else {
                    self.inspectInFlight = false
                    self.setExternalReadState(changes: [], unreadable: false)
                    return
                }
                let labels = observedFingerprints.map { Self.changedLabels(from: self.fingerprintValues, to: $0) }
                    ?? (signatureChanged ? ["配置文件或凭证"] : [])
                let alreadyFailed = self.failedAutoRefreshSnapshot.map {
                    $0.signature == observedSignature && $0.fingerprints == observedFingerprints
                } ?? false
                guard self.isUntouched && !alreadyFailed else {
                    self.inspectInFlight = false
                    self.setExternalReadState(changes: labels, unreadable: false)
                    return
                }
                // Keep the background flight occupied for both requests.
                self.background.start(["action": "load"]) { [weak self] result in
                    guard let self else { return }
                    self.inspectInFlight = false
                    guard generation == self.inspectGeneration, !self.busy, self.isUntouched else { return }
                    switch result {
                    case .success(let data):
                        let previousNames = self.settingsOpen ? self.requiredCLINames : []
                        if self.apply(data) {
                            if self.settingsOpen && previousNames != self.requiredCLINames {
                                // The settings view also observes reloads caused by the background timer.
                                self.automaticallyLoadedCLINames = self.requiredCLINames
                            }
                            self.setMessage("检测到外部配置变化，已自动重新读取")
                        }
                    case .failure:
                        self.failedAutoRefreshSnapshot = (observedSignature, observedFingerprints)
                        self.markConfigurationUnverifiable()
                        self.setExternalReadState(changes: labels, unreadable: true)
                    }
                }
            case .failure:
                self.inspectInFlight = false
                self.markConfigurationUnverifiable()
                if !self.isUntouched && !self.externalReadUnreadable { self.externalReadUnreadable = true }
            }
        }
    }
    private func invalidateInspect() {
        inspectGeneration += 1
        if inspectInFlight { background.cancel() }
        // Keep the flight occupied until its completion, even after cancellation.
    }
    func stopBackground() {
        watching?.invalidate(); watching = nil; invalidateInspect()
        settingsDidClose()
        cliProbeGeneration += 1
        pendingCLIProbe = nil
        if cliProbeInFlight != nil { cliProbe.cancel() }
        if cliProbing { cliProbing = false }
    }
    func operation(_ payload: [String: Any], checked: Bool = false) {
        guard !busy else { return }
        invalidateInspect()
        let isLoad = payload["action"] as? String == "load"
        if isLoad {
            loadingConfiguration = true
            if cliConfigurationLoadFailed { cliConfigurationLoadFailed = false }
        }
        busy = true; checking = checked
        if checked {
            elapsed = 0
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in Task { @MainActor in self?.elapsed += 1 } }
            setMessage(payload["action"] as? String == "real" ? "合成检测进行中；API 最多 30 分钟，Qoder 最多 10 分钟，可取消。" : "正在进行本地检查…")
        }
        foreground.start(payload) { [weak self] result in
                guard let self else { return }
                var configurationRead = false
                defer { if isLoad { self.finishCLILoad(succeeded: configurationRead) } }
                self.busy = false; self.checking = false; self.timer?.invalidate(); self.timer = nil
                switch result {
                case .failure(let error):
                    if self.menuSaving { self.resetDrafts() }; self.menuSaving = false
                    self.setMessage(error.localizedDescription, isError: true)
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
                        self.setMessage("检测完成，用时 \(self.elapsed) 秒")
                    } else {
                        let menuAPI = self.menuSaving && self.draft.channel == "qwen"
                        self.menuSaving = false
                        guard self.apply(data) else { return }
                        configurationRead = true
                        let message = payload["action"] as? String == "load" ? "已读取默认配置" : "配置已回读确认；用于后续启动或调用"
                        self.setMessage(message + (menuAPI ? "；请确认服务地址和密钥支持所选模型" : ""))
                    }
                }
        }
    }
    private func apply(_ data: [String: Any]) -> Bool {
        guard let v = data["values"] as? [String: Any] else {
            setMessage("配置响应无效", isError: true)
            return false
        }
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
        d.agyReviewModel = text("agy_review_model")
        d.agyImplementationModel = text("agy_implementation_model")
        d.agyRelayError = text("agy_relay_error")
        if let relay = v["agy_relay"] as? [String: Any] {
            d.agyReviewEffort = (relay["review"] as? [String: Any])?["effort"] as? String ?? ""
            d.agyImplementationEffort = (relay["implementation"] as? [String: Any])?["effort"] as? String ?? ""
        }
        d.dualPlanEnabled = v["dual_plan_enabled"] as? Bool ?? false
        d.dualPlanError = text("dual_plan_error")
        if let plan = v["dual_plan"] as? [String: Any] {
            d.dualPlanPartner = plan["partner"] as? String ?? "agy"
            d.dualPlanModel = plan["model"] as? String ?? ""
            d.dualPlanEffort = plan["effort"] as? String ?? ""
            d.dualPlanSynthesizer = plan["synthesizer"] as? String ?? "claude"
        }
        d.agyDualPlanEnabled = v["agy_dual_plan_enabled"] as? Bool ?? false
        d.agyDualPlanError = text("agy_dual_plan_error")
        if let plan = v["agy_dual_plan"] as? [String: Any] {
            d.agyDualPlanPartner = plan["partner"] as? String ?? "claude"
            d.agyDualPlanModel = plan["model"] as? String ?? ""
            d.agyDualPlanEffort = plan["effort"] as? String ?? ""
            d.agyDualPlanSynthesizer = plan["synthesizer"] as? String ?? "agy"
        }
        d.codexModels = v["codex_models"] as? [String] ?? ModelCatalog.codex
        legacyQwenExecutorHosts = []
        if text("qwen_executor") == "claude" { legacyQwenExecutorHosts.insert("codex") }
        if text("claude_qwen_executor") == "claude" { legacyQwenExecutorHosts.insert("claude") }
        executorMigrationPending = !legacyQwenExecutorHosts.isEmpty
        saved = d
        // Legacy configurations may only contain the endpoint for one host.
        // Suggest missing Token Plan defaults in the draft; keep the disk snapshot intact.
        if var token = d.profiles["token_plan"] {
            let defaults = ServiceProfile.initial("token_plan")
            if token.baseURL.isEmpty { token.baseURL = defaults.baseURL }
            if token.responsesBaseURL.isEmpty { token.responsesBaseURL = defaults.responsesBaseURL }
            d.profiles["token_plan"] = token
        }
        draft = d; loaded = true
        let channels = ["claude": text("claude_channel"), "codex": text("codex_channel"), "agy": "codex"]
        defaults = ["claude": text("claude_model"), "codex": text("codex_model"), "agy": ""]
        savedHosts = [:]
        for host in ["claude", "codex", "agy"] {
            savedHosts[host] = HostDraft(primaryModel: defaults[host] ?? "", channel: channels[host].flatMap { $0.isEmpty ? nil : $0 } ?? (host == "claude" ? "codex" : "qwen"))
        }
        savedHosts[d.host] = HostDraft(primaryModel: d.primaryModel, channel: d.channel)
        hostDrafts = savedHosts
        fingerprints = data["fingerprints"] as? [String: Any] ?? [:]
        fingerprintValues = Self.normalizeFingerprints(fingerprints) ?? [:]
        signature = data["signature"] as? String ?? ""
        if let checkSignature, checkSignature != signature { status = "未检测（配置或凭证已变化）"; self.checkSignature = nil }
        credentialPresent = v["credential_present"] as? Bool ?? false
        relativeCredentials = v["relative_credentials"] as? Bool ?? false
        fallbacks = v["fallback_channels"] as? [String] ?? []
        canRestore = data["can_restore"] as? Bool ?? false
        sources = data["sources"] as? [String: String] ?? [:]
        setExternalReadState(changes: [], unreadable: false)
        failedAutoRefreshSnapshot = nil
        baselineDraft = draft
        baselineHosts = currentHosts
        return true
    }
    func refresh() { operation(["action": "load"]) }
    func refreshOnOpen() {
        guard !busy else { return }
        failedAutoRefreshSnapshot = nil
        if !loaded || isUntouched { refresh() }
        else { inspect() }
    }
    func save() {
        if draft.claudeImplementationProvider == "codex" && !Draft.validModelID(draft.claudeImplementationModel) {
            setMessage("Claude 的 Codex 实施模型 ID 必须为非空且不含空白的字符串", isError: true)
            return
        }
        if draft.agyImplementationModel != saved.agyImplementationModel && !Draft.validModelID(draft.agyImplementationModel) {
            setMessage("AGY 的 Codex 实施模型 ID 必须为非空且不含空白的字符串", isError: true)
            return
        }
        var values = draft.dictionary
        if draft.dualPlanEnabled != saved.dualPlanEnabled {
            values["dual_plan_enabled"] = draft.dualPlanEnabled
        }
        if draft.agyDualPlanEnabled != saved.agyDualPlanEnabled {
            values["agy_dual_plan_enabled"] = draft.agyDualPlanEnabled
        }
        if draft.agyReviewModel != saved.agyReviewModel {
            values["agy_review_model"] = draft.agyReviewModel
        }
        if draft.agyImplementationModel != saved.agyImplementationModel {
            values["agy_implementation_model"] = draft.agyImplementationModel
        }
        if saved.claudeImplementationProvider == "claude-agent" && draft.claudeImplementationProvider == "claude-agent" {
            values.removeValue(forKey: "claude_implementation_provider")
        }
        var updates: [String: [String: String]] = [:]
        for (host, edit) in currentHosts where host != "agy" {
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
        guard !dirty else { setMessage("请先保存或放弃草稿，再检测已保存配置", isError: true); return }
        operation(["action": real ? "real" : "local"], checked: true)
    }
    func cancel() { foreground.cancel(); setMessage("正在取消并清理检测进程…") }
    func quit() {
        stopBackground(); foreground.cancel()
        if busy {
            setMessage("退出前正在清理检测进程…")
            DispatchQueue.main.asyncAfter(deadline: .now() + 6) { NSApplication.shared.terminate(nil) }
        } else { NSApplication.shared.terminate(nil) }
    }
    func switchHost(_ host: String) {
        guard !busy, loaded else { setMessage("配置尚未读取或正在忙碌，暂不能切换主模型", isError: true); return }
        guard ["claude", "codex", "agy"].contains(host), host != draft.host else { return }
        hostDrafts = currentHosts
        guard let target = hostDrafts[host] else { return }
        draft.host = host; draft.primaryModel = target.primaryModel; draft.channel = target.channel
    }
    func menuHost(_ host: String) {
        guard !busy, loaded, !dirty else { setMessage("设置草稿尚未保存或配置尚未读取、忙碌", isError: true); return }
        if host == "agy" && !saved.agyRelayError.isEmpty {
            setMessage("AGY 接力配置无效，请先在设置中查看并修复 config.json", isError: true)
            return
        }
        switchHost(host)
        guard dirty, draft.host == host else { return }
        if draft.channel == "qwen" && (draft.qwenModel.isEmpty || draft.responsesBaseURL.isEmpty || draft.profile.credentialPaths.isEmpty) {
            setMessage("请在设置中完成所选服务的地址、模型与认证后保存", isError: true); return
        }
        menuSaving = true; save()
    }
    func menuChannel(_ family: String) {
        guard !busy, loaded, !dirty else { setMessage("设置草稿尚未保存或配置忙碌", isError: true); return }
        switchReviewFamily(family)
        guard dirty else { return }
        if draft.channel == "qwen" && (draft.qwenModel.isEmpty || draft.responsesBaseURL.isEmpty || draft.profile.credentialPaths.isEmpty) {
            setMessage("请在设置中完成所选服务的地址、模型与认证后保存", isError: true); return
        }
        menuSaving = true; save()
    }
    func menuMode(_ mode: String) {
        guard !busy, loaded, !dirty else { setMessage("设置草稿尚未保存", isError: true); return }
        draft.mode = mode; menuSaving = true; save()
    }
    func discard() {
        resetDrafts()
        if messageIsError {
            setMessage(externalChanges.isEmpty && !externalReadUnreadable
                ? "已放弃草稿" : "已放弃草稿，检测到外部修改，将自动重新读取")
        }
    }
}
