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
            created.title = "ai-anti 设置"; created.isReleasedWhenClosed = false
            created.contentView = NSHostingView(rootView: SettingsView(model: model))
            created.center(); window = created
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    func applicationWillTerminate(_ notification: Notification) { model?.cancel() }
}

@main struct AIReviewBar: App {
    @NSApplicationDelegateAdaptor(Delegate.self) var delegate
    @StateObject private var model = AppModel.shared
    private static let menuIcon: NSImage = {
        let image = Bundle.main.url(forResource: "MenuBarTemplate", withExtension: "png")
            .flatMap { NSImage(contentsOf: $0) }
            ?? NSImage(systemSymbolName: "checkmark.bubble", accessibilityDescription: "ai-anti")!
        image.size = NSSize(width: 22, height: 22)
        image.isTemplate = true
        return image
    }()
    var body: some Scene {
        MenuBarExtra {
            Text("当前选中配置（非运行中会话）")
            Text(model.title)
            Menu("主导工具") {
                Button("Claude Code") { model.menuHost("claude") }; Button("Codex") { model.menuHost("codex") }
            }.disabled(model.busy || model.dirty || !model.loaded)
            Menu("首选评审") {
                if model.saved.host == "claude" { Button("Codex") { model.menuChannel("codex") } }
                Button("Qwen") { model.menuChannel("qwen") }
            }.disabled(model.busy || model.dirty || !model.loaded)
            Menu("默认协作模式：" + (model.modes.first { $0.0 == model.saved.mode }?.1 ?? model.saved.mode)) {
                ForEach(model.modes, id: \.0) { item in Button(item.1) { model.menuMode(item.0) } }
            }.disabled(model.busy || model.dirty || !model.loaded)
            Text(model.status)
            if model.checking { Text("检测中 · \(model.elapsed) 秒"); Button("取消检测", action: model.cancel) }
            Text(model.message)
            Button("检查配置") { model.check() }.disabled(model.busy || model.dirty || !model.loaded)
            Button("刷新", action: model.refresh).disabled(model.busy || model.dirty)
            Divider()
            Button("设置…") { delegate.model = model; delegate.showSettings() }.keyboardShortcut(",")
            Button("退出", action: model.quit).keyboardShortcut("q")
        } label: {
            Image(nsImage: Self.menuIcon)
                .renderingMode(.template)
                .accessibilityLabel("ai-anti")
                .onAppear { delegate.model = model; if CommandLine.arguments.contains("--settings") { delegate.showSettings() } }
        }
        .commands { CommandGroup(replacing: .appSettings) { Button("设置…") { delegate.model = model; delegate.showSettings() } } }
        Settings { SettingsView(model: model).onAppear { delegate.model = model } }
    }
    init() { }
}
