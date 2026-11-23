import SwiftUI
import AppKit

@MainActor final class Delegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var model: AppModel?
    var window: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        model = AppModel.shared
        NSApp.setActivationPolicy(.accessory)
        if CommandLine.arguments.contains("--settings") { showSettings() }
    }
    func showSettings() {
        guard let model else { return }
        if window == nil {
            let created = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 740, height: 760), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            created.title = "设置"; created.isReleasedWhenClosed = false
            created.delegate = self
            created.contentView = NSHostingView(rootView: SettingsView(model: model))
            created.center(); window = created
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        model.refreshOnOpen()
        model.probeOnSettingsOpen()
    }
    func windowWillClose(_ notification: Notification) { model?.settingsDidClose() }
    func applicationWillTerminate(_ notification: Notification) { model?.stopBackground(); model?.cancel() }
}

@main struct AIAnti: App {
    @NSApplicationDelegateAdaptor(Delegate.self) var delegate
    @StateObject private var model = AppModel.shared
    private static let menuIcon: NSImage = {
        let image = Bundle.main.url(forResource: "MenuBarTemplate", withExtension: "png")
            .flatMap { NSImage(contentsOf: $0) }
            ?? NSImage(systemSymbolName: "checkmark.bubble", accessibilityDescription: "AIAnti")!
        image.size = NSSize(width: 22, height: 22)
        image.isTemplate = true
        return image
    }()
    var body: some Scene {
        MenuBarExtra {
            Text("当前配置")
            Text("主模型：" + model.menuPrimary)
            Text("评审服务：" + model.menuReview)
            Text("实施服务：" + model.menuImplementation)
            Text("协作模式：" + model.saved.mode)
            // 具体设置项只在设置窗口调整；菜单仅在读取或保存出错时提示一行错误，正常状态说明不再显示。
            if model.messageIsError { Text("⚠︎ " + model.message) }
            Divider()
            Button("设置") { delegate.model = model; delegate.showSettings() }.keyboardShortcut(",")
            Button("退出", action: model.quit).keyboardShortcut("q")
        } label: {
            Image(nsImage: Self.menuIcon)
                .renderingMode(.template)
                .accessibilityLabel("AIAnti")
                .onAppear { delegate.model = model; if CommandLine.arguments.contains("--settings") { delegate.showSettings() } }
        }
        .commands { CommandGroup(replacing: .appSettings) { Button("设置") { delegate.model = model; delegate.showSettings() } } }
        Settings {
            SettingsView(model: model).onAppear {
                delegate.model = model
                model.refreshOnOpen()
                model.probeOnSettingsOpen()
            }
            .onDisappear { model.settingsDidClose() }
        }
    }
    init() { }
}

/// 菜单栏「当前配置」各行的展示文字，只读取已保存的配置。
extension AppModel {
    var menuPrimary: String {
        switch saved.host {
        case "agy": return "AGY（模型由 AGY 管理）"
        case "codex": return "Codex · " + (saved.primaryModel.isEmpty ? "CLI 默认" : saved.primaryModel)
        default: return "Claude Code · " + (saved.primaryModel.isEmpty ? "CLI 默认" : saved.primaryModel)
        }
    }
    var menuReview: String {
        if saved.mode == "solo" { return "停用（solo）" }
        if saved.host == "agy" { return "Codex · " + (saved.agyReviewModel.isEmpty ? "CLI 默认" : saved.agyReviewModel) }
        let detail: String
        switch saved.channel {
        case "codex": detail = saved.reviewerModel.isEmpty ? "CLI 默认" : saved.reviewerModel
        case "qoder": detail = "Kimi-K3.1-Pro"
        default: detail = saved.qwenModel
        }
        return ReviewService.title(savedReviewFamily) + " · " + detail
    }
    var menuImplementation: String {
        if saved.mode == "solo" { return "主模型自行实施（solo）" }
        switch saved.host {
        case "agy": return "Codex · " + (saved.agyImplementationModel.isEmpty ? "未配置" : saved.agyImplementationModel)
        case "codex":
            switch saved.implementationProvider {
            case "token_plan": return "千问 Token Plan · " + saved.tokenPlanImplementationModel
            case "qoder": return "Qoder · GLM-5.3"
            default: return "Codex · " + (saved.secondaryModel.isEmpty ? "每次任务指定" : saved.secondaryModel)
            }
        default:
            return saved.claudeImplementationProvider == "codex"
                ? "Codex · " + saved.claudeImplementationModel : "Claude Code 实施子代理"
        }
    }
}
