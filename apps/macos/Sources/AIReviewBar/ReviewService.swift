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
        var p = ServiceProfile(); p.credentialKey = "model_services.\(id).value"
        if id == "token_plan" {
            p.model = "qwen3.8-max"
            p.baseURL = "https://token-plan.maas.qianwenaiapi.com/apps/anthropic"
            p.responsesBaseURL = "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
        }
        return p
    }
}

struct RouteSelection: Equatable {
    var service: String
    var model: String
    var dictionary: [String: String] { ["service": service, "model": model] }

    init(service: String, model: String) {
        self.service = service
        self.model = model
    }

    init?(_ value: Any?) {
        guard let fields = value as? [String: String],
              let service = fields["service"], let model = fields["model"] else { return nil }
        self.init(service: service, model: model)
    }
}

struct VendorCredential: Equatable {
    var action = "retain"
    var secret = ""
    var request: [String: String] {
        let replace = action == "replace" && !secret.isEmpty
        return ["action": replace ? "replace" : action == "clear" ? "clear" : "retain",
                "value": replace ? secret : ""]
    }
}

enum ReviewService {
    static let api = ["token_plan", "custom_api"]
    static let vendors = ["qwen", "kimi", "deepseek"]
    static let officialChannel = "official_api"
    static let officialURLs = [
        "token_plan": ServiceProfile.initial("token_plan").responsesBaseURL,
        "qwen": "https://maas.qianwenaiapi.com/compatible-mode/v1",
        "kimi": "https://api.moonshot.cn/v1",
        "deepseek": "https://api.deepseek.com"
    ]
    static func title(_ id: String) -> String {
        switch id {
        case "token_plan": return "千问 Token Plan"
        case "custom_api": return "自定义 API"
        case "qwen": return "千问 官方直连"
        case "kimi": return "Kimi 官方直连"
        case "deepseek": return "DeepSeek 官方直连"
        case "qoder": return "Qoder 订阅"
        case "codex": return "Codex"
        default: return "已有配置：" + id
        }
    }
}
