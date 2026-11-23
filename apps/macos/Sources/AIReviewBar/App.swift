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
        model.refreshOnOpen()
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
            }
        }
    }
    init() { }
}
