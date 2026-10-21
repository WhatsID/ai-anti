import Foundation

struct ServiceProfile: Equatable {
    var model = ""
    var baseURL = ""
    var responsesBaseURL = ""
    var credentialPaths: [String] = []
    var credentialKey = ""
    var secret = ""
    var action = "retain"
    var dictionary: [String: Any] { ["model": model, "base_url": baseURL, "responses_base_url": responsesBaseURL, "credential_paths": credentialPaths, "credential_key": credentialKey] }
    static func initial(_ id: String) -> ServiceProfile {
        var p = ServiceProfile(); p.credentialKey = "review_services.\(id).value"
        if id == "token_plan" {
            p.model = "qwen3.8-max"
            p.baseURL = "https://token-plan.maas.qianwenaiapi.com/apps/anthropic"
            p.responsesBaseURL = "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
        }
        return p
    }
}

enum ReviewService {
    static let api = ["token_plan", "custom_api"]
    static func title(_ id: String) -> String {
        switch id { case "token_plan": return "千问 Token Plan"; case "custom_api": return "自定义 API"; case "qoder": return "Qoder"; case "codex": return "Codex"; default: return "已有配置：" + id }
    }
}
