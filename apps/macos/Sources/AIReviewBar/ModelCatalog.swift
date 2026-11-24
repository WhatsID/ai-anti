import Foundation

struct ModelOption: Identifiable, Equatable {
    let id: String
    let label: String
}

enum ModelCatalog {
    static let agyManaged = "由 AGY 设置管理（本应用不修改）"
    static let implementationGLM = "glm-5.3"
    static let claude = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    private static let claudeAliases = ["Fable", "Opus", "Sonnet", "Haiku"]
    static let codex = ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"]
    static let tokenPlanImplementation = [implementationGLM, "qwen4-max", "qwen3.8-max", "deepseek-v4.1-flash"]
    static let officialReview = [
        "token_plan": ["qwen4-max", "qwen3.8-max", "deepseek-v4-pro", implementationGLM],
        "qwen": ["qwen4-max"],
        "kimi": ["kimi-k3.1-pro", "kimi-k3"],
        "deepseek": ["deepseek-v4-pro"]
    ]
    static let directImplementation = [
        "qwen": ["qwen4-max", "qwen3.8-max"],
        "kimi": ["kimi-k3.1-pro", "kimi-k3"],
        "deepseek": ["deepseek-v4-pro", "deepseek-flash"]
    ]
    static let qwen = ["qwen4-max", "qwen3.8-max"]
    static let deepseek = ["deepseek-v4-pro"]
    static func family(channel: String, apiModel: String) -> String {
        channel == "qwen" ? (apiModel.hasPrefix("deepseek-") ? "deepseek" : "qwen") : channel
    }
    static func title(_ family: String) -> String {
        switch family { case "codex": return "Codex"; case "qwen": return "Qwen"; case "deepseek": return "DeepSeek"; default: return family }
    }
    static func options(_ family: String, preserving values: [String], emptyLabel: String? = nil, codexModels: [String] = codex) -> [ModelOption] {
        var result: [ModelOption] = emptyLabel.map { [ModelOption(id: "", label: $0)] } ?? []
        if family == "claude" {
            result += claudeAliases.map { name in
                let id = name.lowercased()
                return ModelOption(id: id, label: "\(id)（别名 · 自动跟随最新 \(name)）")
            }
            result += claude.map { ModelOption(id: $0, label: $0 + "（固定版本）") }
        } else {
            let ids: [String]
            switch family { case "codex": ids = codexModels; case "qwen": ids = qwen; case "deepseek": ids = deepseek; default: ids = [] }
            result += ids.map { ModelOption(id: $0, label: $0) }
        }
        for value in values where !value.isEmpty && !result.contains(where: { $0.id == value }) {
            result.append(ModelOption(id: value, label: "已有配置：" + value))
        }
        return result
    }
}
