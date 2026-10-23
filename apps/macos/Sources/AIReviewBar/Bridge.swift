import Foundation
import Darwin

struct BridgeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

// One serial background request at a time. Input secrets travel only through stdin.
final class Bridge: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false
    private var timedOut = false
    private let arguments: [String]
    init(arguments: [String] = CommandLine.arguments) { self.arguments = arguments }
    private func option(_ flag: String) -> String? {
        guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else { return nil }
        return arguments[index + 1]
    }
    func prepare() {
        lock.lock(); cancelled = false; timedOut = false; lock.unlock()
    }
    func cancel() {
        lock.lock(); cancelled = true; let p = process; lock.unlock()
        if let p, p.isRunning {
            p.terminate()
            DispatchQueue.global().asyncAfter(deadline: .now() + 8) { [weak p] in if let p, p.isRunning { kill(p.processIdentifier, SIGKILL) } }
        }
    }
    private func begin(_ p: Process) throws {
        lock.lock(); defer { lock.unlock() }
        if cancelled { throw BridgeFailure(message: "检测已取消") }
        process = p
        try p.run()
    }
    private func python() throws -> String {
        var candidates: [String] = []
        if let explicit = option("--python") {
            guard explicit.hasPrefix("/") else { throw BridgeFailure(message: "--python 需要绝对路径") }
            candidates.append(explicit)
        }
        candidates += ["/opt/homebrew/bin/python3.11", "/opt/homebrew/bin/python3", "/usr/local/bin/python3.11", "/usr/local/bin/python3"]
        candidates += (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map { String($0) + "/python3" }
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            let p = Process(); p.executableURL = URL(fileURLWithPath: candidate)
            p.arguments = ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"]
            p.standardInput = FileHandle.nullDevice; p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
            try begin(p)
            let deadline = Date().addingTimeInterval(3)
            while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if p.isRunning {
                p.terminate()
                let grace = Date().addingTimeInterval(0.5)
                while p.isRunning && Date() < grace { Thread.sleep(forTimeInterval: 0.05) }
                if p.isRunning { kill(p.processIdentifier, SIGKILL) }
                p.waitUntilExit(); continue
            }
            if p.terminationStatus == 0 { return candidate }
            if option("--python") == candidate { throw BridgeFailure(message: "指定的 Python 需要 3.11 或更高版本") }
        }
        throw BridgeFailure(message: "未找到 Python 3.11+。安装 Homebrew Python，或启动时传 --python /绝对路径。")
    }
    func request(_ payload: [String: Any]) throws -> [String: Any] {
        defer { lock.lock(); process = nil; lock.unlock() }
        let interpreter = try python()
        guard let resources = Bundle.main.resourceURL else { throw BridgeFailure(message: "应用资源缺失") }
        let script = resources.appendingPathComponent("backend/control.py")
        guard FileManager.default.fileExists(atPath: script.path) else { throw BridgeFailure(message: "应用 backend 缺失；请通过 build-app.sh 构建 .app") }
        let p = Process(); p.executableURL = URL(fileURLWithPath: interpreter)
        var args = ["-B", script.path]
        for flag in ["--config-root", "--source-root"] { if let value = option(flag) { args += [flag, value] } }
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = NSTemporaryDirectory() + "ai-anti-python-cache"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        p.environment = env
        let input = Pipe(); let output = Pipe()
        p.standardInput = input; p.standardOutput = output; p.standardError = FileHandle.nullDevice
        defer {
            try? input.fileHandleForReading.close(); try? input.fileHandleForWriting.close()
            try? output.fileHandleForReading.close(); try? output.fileHandleForWriting.close()
        }
        try begin(p)
        let action = payload["action"] as? String ?? ""
        // 3700 covers the maximum review timeout (3600) + runner cleanup (60).
        // Keep this budget in sync with review_limits; cancellation remains immediate.
        let timeout: Double = action == "real" ? 3700 : (action == "local" ? 40 : 30)
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self, weak p] in
            guard let self, let p, p.isRunning else { return }
            self.lock.lock()
            let active = self.process === p
            if active { self.timedOut = true }
            self.lock.unlock()
            guard active else { return }
            p.terminate()
            DispatchQueue.global().asyncAfter(deadline: .now() + 8) { [weak p] in
                if let p, p.isRunning { kill(p.processIdentifier, SIGKILL) }
            }
        }
        let data = try JSONSerialization.data(withJSONObject: payload)
        try input.fileHandleForWriting.write(contentsOf: data)
        try input.fileHandleForWriting.close()
        // JSON response is deliberately bounded by backend; never includes raw CLI logs.
        let reply = output.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        lock.lock(); let wasCancelled = cancelled; let didTimeout = timedOut; lock.unlock()
        if wasCancelled { throw BridgeFailure(message: "检测已取消") }
        if didTimeout { throw BridgeFailure(message: "后台操作超时，进程已终止") }
        guard reply.count <= 1_048_576, let parsed = try JSONSerialization.jsonObject(with: reply) as? [String: Any] else {
            throw BridgeFailure(message: "后台响应无效；错误详情已隐藏")
        }
        guard parsed["ok"] as? Bool == true, let result = parsed["data"] as? [String: Any] else {
            throw BridgeFailure(message: parsed["error"] as? String ?? "后台操作失败")
        }
        return result
    }
}
