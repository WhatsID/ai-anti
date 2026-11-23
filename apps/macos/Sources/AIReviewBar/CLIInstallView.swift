import AppKit
import SwiftUI

struct CLIStatusSection: View {
    @ObservedObject var model: AppModel
    let selectInstaller: (RequiredCLI) -> Void

    private var installedVersions: String {
        model.requiredCLIs.compactMap { item in
            guard case .installed(let version) = model.cliStatuses[item.id] else { return nil }
            let label = "\(CLICatalog.tool(item.id).title) \(version)"
            let role = RequiredCLI.legacyQwenReviewRole
            return item.roles.contains(role) ? "\(label) \(role)" : label
        }.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !model.loaded {
                if model.cliConfigurationLoadFailed {
                    warning("配置尚未读入，无法检测 CLI") { recheckButton }
                } else {
                    progress("正在读取配置…")
                }
            } else {
                if !installedVersions.isEmpty {
                    Text(installedVersions).font(.caption).foregroundStyle(.secondary)
                }
                if let error = model.cliProbeError {
                    warning("无法检测 CLI：\(error)") { recheckButton }
                } else {
                    ForEach(model.requiredCLIs) { item in
                        if let status = model.cliStatuses[item.id] {
                            statusRow(item, status)
                        }
                    }
                    if model.requiredCLIs.contains(where: { model.cliStatuses[$0.id] == nil }) {
                        if model.cliProbing {
                            progress("正在检测 CLI…")
                        } else {
                            warning("尚未检测当前配置所需的 CLI") { recheckButton }
                        }
                    }
                }
            }
        }
        .padding(.leading, SettingsColumns.labelWidth + SettingsColumns.spacing)
    }

    @ViewBuilder private func statusRow(_ item: RequiredCLI, _ status: CLIStatus) -> some View {
        let tool = CLICatalog.tool(item.id)
        switch status {
        case .installed:
            EmptyView()
        case .missing, .error:
            warning(statusMessage(item, tool, status)) {
                if status.offersInstallation {
                    if tool.methods.isEmpty {
                        Link("打开官方安装说明", destination: tool.docsURL)
                    } else {
                        Button("安装…") { selectInstaller(item) }
                    }
                }
                recheckButton
            }
        }
    }

    private func statusMessage(_ item: RequiredCLI, _ tool: CLITool, _ status: CLIStatus) -> String {
        let label = "\(tool.title)（\(item.roleLabel)）"
        switch status {
        case .installed: return label
        case .missing: return "未检测到 \(label)"
        case .error(let reason): return "\(label)已找到，版本读取失败：\(reason)"
        }
    }

    private var recheckButton: some View {
        Button("重新检测", action: model.recheckCLIs)
            .disabled(model.cliProbing || (!model.loaded && model.busy))
    }

    private func warning<Actions: View>(_ message: String, @ViewBuilder actions: () -> Actions) -> some View {
        HStack(spacing: 8) {
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            actions().fixedSize()
        }
        .font(.caption)
        .controlSize(.small)
    }

    private func progress(_ message: String) -> some View {
        HStack(spacing: 6) {
            ProgressView().controlSize(.mini)
            Text(message)
        }.font(.caption).foregroundStyle(.secondary)
    }
}

struct CLIInstallSheet: View {
    @ObservedObject var model: AppModel
    let item: RequiredCLI
    let method: CLIInstallMethod
    @Environment(\.dismiss) private var dismiss
    @State private var launching = false
    @State private var launchError: String?

    var body: some View {
        let tool = CLICatalog.tool(item.id)
        VStack(alignment: .leading, spacing: 12) {
            Text("安装 \(tool.title)").font(.title2.bold())
            Text("用于\(item.roleLabel)").foregroundStyle(.secondary)
            if item.id == .codex {
                Text(method == .codexHomebrew ? "已检测到 Homebrew，将使用 Homebrew 安装。" : "将使用官方独立安装器。")
            }
            HStack(alignment: .top, spacing: 12) {
                Text(method.command)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                Button("复制命令") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(method.command, forType: .string)
                }
            }
            Link(tool.docsURL.absoluteString, destination: tool.docsURL)
                .accessibilityLabel("官方安装说明，\(tool.docsURL.absoluteString)")
            Text("安装完成后，同一终端会提示按回车启动 \(item.id.rawValue) 登录。")
                .foregroundStyle(.secondary)
            if let launchError {
                Text(launchError).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction).disabled(launching)
                Button("在终端中打开并安装", action: launch)
                    .keyboardShortcut(.defaultAction)
                    .disabled(launching)
            }
        }
        .padding(20)
        .frame(width: 560)
        .interactiveDismissDisabled(launching)
    }

    private func launch() {
        launching = true
        launchError = nil
        Task { @MainActor in
            defer { launching = false }
            do {
                try await model.launchCLIInstaller(toolID: item.id, method: method)
                dismiss()
            } catch {
                launchError = error.localizedDescription
            }
        }
    }
}
