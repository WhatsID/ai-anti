import Foundation

struct ModelOption: Identifiable, Equatable {
    let id: String
    let label: String
}

enum ModelCatalog {
    static let claude = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    static let codex = ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"]
    static let qwen = ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus"]
    static let deepseek = ["deepseek-v4-pro", "deepseek-flash", "deepseek-v4-pro-0813", "deepseek-v4.1-flash", "deepseek-v4-flash-0731"]
    static func family(channel: String, apiModel: String) -> String {
        channel == "qwen" ? (apiModel.hasPrefix("deepseek-") ? "deepseek" : "qwen") : channel
    }
    static func title(_ family: String) -> String {
        switch family { case "codex": return "Codex"; case "qwen": return "Qwen"; case "deepseek": return "DeepSeek"; default: return family }
    }
    static func options(_ family: String, preserving values: [String], emptyLabel: String? = nil) -> [ModelOption] {
        let ids: [String]
        switch family { case "claude": ids = claude; case "codex": ids = codex; case "qwen": ids = qwen; case "deepseek": ids = deepseek; default: ids = [] }
        var result: [ModelOption] = emptyLabel.map { [ModelOption(id: "", label: $0)] } ?? []
        result += ids.map { id in
            let source = family == "deepseek" ? (id == "deepseek-v4-pro" ? "（官方 API / 阿里云 Token Plan）" : id == "deepseek-flash" ? "（官方 API）" : "（阿里云 Token Plan）") : ""
            return ModelOption(id: id, label: id + source)
        }
        for value in values where !value.isEmpty && !result.contains(where: { $0.id == value }) {
            result.append(ModelOption(id: value, label: "已有配置：" + value))
        }
        return result
    }
}
