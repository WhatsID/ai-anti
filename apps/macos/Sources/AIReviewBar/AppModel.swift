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
    var qwenModel = ""
    var secondaryModel = ""
    var mode = "required"
    var baseURL = ""
    var secret = ""
    var dictionary: [String: Any] { ["host": host, "primary_model": primaryModel, "channel": channel, "reviewer_model": reviewerModel, "qwen_model": qwenModel, "secondary_model": secondaryModel, "mode": mode, "base_url": baseURL] }
}

@MainActor final class AppModel: ObservableObject {
    static let shared = AppModel()
    @Published var draft = Draft()
    @Published var saved = Draft()
    @Published var busy = false
    @Published var loaded = false
    @Published var message = "正在读取配置…"
    @Published var status = "未检测"
    @Published var credentialPresent = false
    @Published var relativeCredentials = false
    @Published var canRestore = false
    @Published var fallbacks: [String] = []
    @Published var sources: [String: String] = [:]
    @Published var elapsed = 0
    @Published var checking = false
    private var fingerprints: [String: Any] = [:]
    private var signature = ""
    private var checkSignature: String?
    private var channels: [String: String] = [:]
    private var watching: Timer?
    private var menuSaving = false
    private var defaults: [String: String] = [:]
    private let foreground: AppRequesting
    private let background: AppRequesting
    private var inspectInFlight = false
    private var inspectGeneration = 0
    private var timer: Timer?
    var dirty: Bool { draft != saved }
    var title: String { "\(saved.host == "claude" ? "Claude" : "Codex") → \(saved.channel.capitalized)" }
    let modes = [("solo", "独立完成"), ("auto", "自动判断"), ("required", "按规模评审"), ("force", "始终评审")]
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
            message = payload["action"] as? String == "real" ? "合成检测进行中；Qwen 可能需要 10–20 分钟，可取消。" : "正在进行本地检查…"
        }
        foreground.start(payload) { [weak self] result in
                guard let self else { return }
                self.busy = false; self.checking = false; self.timer?.invalidate(); self.timer = nil
                switch result {
                case .failure(let error):
                    if self.menuSaving { self.draft = self.saved }; self.menuSaving = false
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
                        self.status = (data["message"] as? String ?? "检测完成") + "\n" + (data["checked_at"] as? String ?? "") + " · 实际模型：" + (data["actual_model"] as? String ?? "未知")
                        self.message = "检测完成，用时 \(self.elapsed) 秒"
                    } else {
                        self.menuSaving = false
                        self.apply(data)
                        self.message = payload["action"] as? String == "load" ? "已读取默认配置" : "配置已回读确认；用于后续启动或调用"
                    }
                }
        }
    }
    private func apply(_ data: [String: Any]) {
        guard let v = data["values"] as? [String: Any] else { message = "配置响应无效"; return }
        func text(_ key: String) -> String { v[key] as? String ?? "" }
        var d = Draft(); d.host = text("host"); d.primaryModel = text("primary_model"); d.channel = text("channel")
        d.reviewerModel = text("reviewer_model"); d.qwenModel = text("qwen_model"); d.secondaryModel = text("secondary_model"); d.mode = text("mode"); d.baseURL = text("base_url")
        saved = d; draft = d; loaded = true
        channels = ["claude": text("claude_channel"), "codex": text("codex_channel")]
        defaults = ["claude": text("claude_model"), "codex": text("codex_model")]
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
    func save() { operation(["action": "save", "values": draft.dictionary, "fingerprints": fingerprints, "secret": draft.secret]) }
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
        guard !dirty else { message = "请先保存或放弃草稿，再切换主模型"; return }
        draft.host = host; draft.primaryModel = defaults[host] ?? ""
        draft.channel = channels[host] ?? (host == "codex" ? "qwen" : "codex")
    }
    func menuHost(_ host: String) { switchHost(host); if draft.host == host && !busy { menuSaving = true; save() } }
    func menuChannel(_ channel: String) { guard !dirty else { message = "设置草稿尚未保存"; return }; draft.channel = channel; menuSaving = true; save() }
    func menuMode(_ mode: String) { guard !dirty else { message = "设置草稿尚未保存"; return }; draft.mode = mode; menuSaving = true; save() }
    func discard() { draft = saved }
}
