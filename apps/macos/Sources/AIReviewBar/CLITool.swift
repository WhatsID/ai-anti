import AppKit
import Darwin
import Foundation

enum CLIToolID: String {
    case claude, codex, agy, qodercli
}

struct RequiredCLI: Identifiable {
    static let legacyQwenReviewRole = "评审（保存后改用 Codex）"

    let id: CLIToolID
    var roles: [String]
    var roleLabel: String { roles.joined(separator: " / ") }
}

enum CLIStatus: Equatable {
    case installed(String)
    case missing
    case error(String)

    init?(_ data: [String: Any]) {
        switch data["state"] as? String {
        case "installed":
            guard let version = data["version"] as? String else { return nil }
            self = .installed(version)
        case "missing":
            self = .missing
        case "error":
            guard let reason = data["reason"] as? String else { return nil }
            self = .error(reason)
        default:
            return nil
        }
    }

    var offersInstallation: Bool { self == .missing }
}

enum CLIInstallMethod {
    case claudeNative, codexHomebrew, codexStandalone, agyNative

    var command: String {
        switch self {
        case .claudeNative: return "curl -fsSL https://claude.ai/install.sh | bash"
        case .codexHomebrew: return "brew install --cask codex"
        case .codexStandalone: return "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
        case .agyNative: return "curl -fsSL https://antigravity.google/cli/install.sh | bash"
        }
    }
}

struct CLITool {
    let title: String
    let docsURL: URL
    let methods: [CLIInstallMethod]

    fileprivate init(_ title: String, _ docsURL: String, methods: [CLIInstallMethod]) {
        self.title = title
        self.docsURL = URL(string: docsURL)!
        self.methods = methods
    }

    func installMethod(homebrewAvailable: Bool) -> CLIInstallMethod? {
        if homebrewAvailable && methods.contains(.codexHomebrew) { return .codexHomebrew }
        return methods.first
    }
}

enum CLICatalog {
    static func tool(_ id: CLIToolID) -> CLITool {
        switch id {
        case .claude:
            return CLITool("Claude Code", "https://code.claude.com/docs/en/setup", methods: [.claudeNative])
        case .codex:
            return CLITool("Codex CLI", "https://github.com/openai/codex", methods: [.codexStandalone, .codexHomebrew])
        case .agy:
            return CLITool("AGY（Antigravity CLI）", "https://antigravity.google/docs/cli/install", methods: [.agyNative])
        case .qodercli:
            return CLITool("Qoder CLI", "https://docs.qoder.com/cli/installation", methods: [])
        }
    }
}

enum CLIInstallScript {
    static func render(toolID: String, method: CLIInstallMethod) -> String? {
        guard let id = CLIToolID(rawValue: toolID), CLICatalog.tool(id).methods.contains(method) else { return nil }
        let cli = id.rawValue
        let login = id == .agy ? "启动 agy 登录 Google 账号" : "启动 \(cli) 登录"
        let preflight = method == .codexHomebrew ? """
        if ! command -v brew >/dev/null 2>&1; then
            printf '%s\\n' '终端中找不到 Homebrew，请恢复 brew 后重试。'
            finish
        fi
        """ : ""
        return """
        #!/bin/bash
        set -o pipefail
        export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
        SCRIPT_PATH="$0"
        SCRIPT_DIR="${SCRIPT_PATH%/*}"
        cleanup() {
            rm -f -- "$SCRIPT_PATH"
            rmdir -- "$SCRIPT_DIR"
        }
        trap cleanup EXIT
        finish() {
            USER_SHELL="${SHELL:-/bin/zsh}"
            [ -x "$USER_SHELL" ] || USER_SHELL="/bin/zsh"
            cleanup
            trap - EXIT
            exec "$USER_SHELL" -l
        }

        \(preflight)
        printf '%s\\n' '\(method.command)'
        if \(method.command); then
            if command -v \(cli) >/dev/null 2>&1; then
                printf '%s\\n' '安装完成。按回车\(login)（输入 s 回车跳过）。完成后请回到 AIAnti 设置窗口点击「重新检测」。'
                while IFS= read -r answer; do
                    case "$answer" in
                        '') \(cli); break ;;
                        s|S) break ;;
                        *) printf '%s\\n' '请按回车登录，或输入 s 回车跳过。' ;;
                    esac
                done
            else
                printf '%s\\n' '安装器已结束，但终端中仍找不到 \(cli)。请查看上方安装输出。'
            fi
        else
            printf '%s\\n' '安装命令失败，请查看上方输出。'
        fi
        finish

        """
    }
}

@MainActor protocol TerminalLaunching {
    func launch(toolID: CLIToolID, method: CLIInstallMethod) async throws
}

@MainActor struct TerminalLauncher: TerminalLaunching {
    func launch(toolID: CLIToolID, method: CLIInstallMethod) async throws {
        guard let script = CLIInstallScript.render(toolID: toolID.rawValue, method: method) else {
            throw BridgeFailure(message: "此 CLI 没有可用的内置安装方式")
        }
        let workspace = NSWorkspace.shared
        guard let terminal = workspace.urlForApplication(withBundleIdentifier: "com.apple.Terminal") else {
            throw BridgeFailure(message: "找不到系统「终端」")
        }
        let templatePath = FileManager.default.temporaryDirectory.appendingPathComponent("ai-anti-install-XXXXXX").path
        var template = templatePath.utf8CString
        let directory = template.withUnsafeMutableBufferPointer { buffer in
            mkdtemp(buffer.baseAddress).map { String(cString: $0) }
        }
        guard let directory else { throw BridgeFailure(message: "无法创建临时安装目录") }
        let scriptURL = URL(fileURLWithPath: directory, isDirectory: true)
            .appendingPathComponent("install-\(toolID.rawValue).command")
        var opened = false
        defer {
            if !opened {
                unlink(scriptURL.path)
                rmdir(directory)
            }
        }
        let fd = Darwin.open(scriptURL.path, O_CREAT | O_EXCL | O_WRONLY, 0o700)
        guard fd >= 0 else { throw BridgeFailure(message: "无法创建临时安装脚本") }
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        do {
            try file.write(contentsOf: Data(script.utf8))
            try file.close()
        } catch {
            try? file.close()
            throw BridgeFailure(message: "无法写入临时安装脚本")
        }
        do {
            _ = try await workspace.open([scriptURL], withApplicationAt: terminal, configuration: .init())
            opened = true
        } catch {
            throw BridgeFailure(message: "无法在系统「终端」中打开安装脚本")
        }
    }
}
