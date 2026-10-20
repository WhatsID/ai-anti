import SwiftUI
import AppKit

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
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
            created.contentView = NSHostingView(rootView: SettingsView(model: model))
            created.center(); window = created
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
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
            Text("当前配置：" + model.title)
            Menu("主模型") {
                Button("Claude Code" + (model.saved.host == "claude" ? " ✓" : "")) { model.menuHost("claude") }; Button("Codex" + (model.saved.host == "codex" ? " ✓" : "")) { model.menuHost("codex") }
            }.disabled(model.busy || model.dirty || !model.loaded)
            Menu("评审模型") {
                if model.saved.host == "claude" { Button("Codex" + (model.savedReviewFamily == "codex" ? " ✓" : "")) { model.menuChannel("codex") } }
                Button("Qwen" + (model.savedReviewFamily == "qwen" ? " ✓" : "")) { model.menuChannel("qwen") }
                Button("DeepSeek" + (model.savedReviewFamily == "deepseek" ? " ✓" : "")) { model.menuChannel("deepseek") }
            }.disabled(model.saved.mode == "solo" || model.busy || model.dirty || !model.loaded)
            Menu("协作模式：" + model.saved.mode) {
                ForEach(model.modes, id: \.0) { item in Button(item.0 + (model.saved.mode == item.0 ? " ✓" : "")) { model.menuMode(item.0) } }
            }.disabled(model.busy || model.dirty || !model.loaded)
            CheckStatusText(status: model.dirty ? "未检测（设置草稿尚未保存）" : model.status)
            if model.checking { Text("检测中 · \(model.elapsed) 秒"); Button("取消检测", action: model.cancel) }
            Text(model.message)
            Button("检查配置") { delegate.model = model; delegate.showSettings(); model.check() }.disabled(model.busy || model.dirty || !model.loaded)
            Button("重新读取配置", action: model.refresh).disabled(model.busy || model.dirty)
                .help("重新从磁盘读取 Claude、Codex 和 ai-anti 默认配置，不调用模型")
            Divider()
            Button("设置…") { delegate.model = model; delegate.showSettings() }.keyboardShortcut(",")
            Button("退出", action: model.quit).keyboardShortcut("q")
        } label: {
            Image(nsImage: Self.menuIcon)
                .renderingMode(.template)
                .accessibilityLabel("AIAnti")
                .onAppear { delegate.model = model; if CommandLine.arguments.contains("--settings") { delegate.showSettings() } }
        }
        .commands { CommandGroup(replacing: .appSettings) { Button("设置…") { delegate.model = model; delegate.showSettings() } } }
        Settings { SettingsView(model: model).onAppear { delegate.model = model } }
    }
    init() { }
}
