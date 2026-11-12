import SwiftUI

private struct FullWidthFormControls: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        // SDK 26 makes menu pickers fit their text by default.
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            content.buttonSizing(.flexible)
        } else {
            content
        }
        #else
        content
        #endif
    }
}

struct CheckStatusText: View {
    let status: String
    var body: some View {
        if status.hasPrefix("本地检查通过；") || status.hasPrefix("合成评审检测通过；") {
            (Text(Image(systemName: "checkmark")).foregroundColor(.green) + Text(" " + status))
        } else if status.hasPrefix("本地检查失败：") || status.hasPrefix("真实合成检测失败：") {
            (Text(Image(systemName: "xmark")).foregroundColor(.red) + Text(" " + status))
        } else {
            Text(status)
        }
    }
}

// Summaries describe the current draft without exposing addresses or credentials.
enum SettingsSummary {
    static func review(_ draft: Draft) -> String {
        let model: String
        switch draft.channel {
        case "qoder": model = "Kimi-K3.1-Pro"
        case "codex": model = draft.reviewerModel.isEmpty ? "CLI 默认" : draft.reviewerModel
        default: model = draft.qwenModel.isEmpty ? "未指定" : draft.qwenModel
        }
        return "评审服务：" + ReviewService.title(draft.service) + " / " + model + (draft.mode == "solo" ? "（solo 已停用）" : "")
    }
    static func implementation(_ draft: Draft) -> String {
        let detail: String
        if draft.host == "claude" { detail = "Claude Code 实施代理" }
        else if draft.implementationProvider == "qoder" { detail = "Qoder 订阅 / GLM-5.3" }
        else if draft.implementationProvider == "token_plan" { detail = "千问 Token Plan / " + ModelCatalog.implementationGLM }
        else { detail = "Codex / " + (draft.secondaryModel.isEmpty ? "未指定" : draft.secondaryModel) }
        return "实施服务：" + detail + (draft.mode == "solo" ? "（solo 已停用）" : "")
    }
}

private enum SettingsColumns {
    static let labelWidth: CGFloat = 132
    static let spacing: CGFloat = 12
}

private struct ServiceSettingsGroup<Selection: View, Content: View>: View {
    let title: String
    let summary: String
    @Binding var isExpanded: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let selection: Selection
    let content: Content
    init(_ title: String, summary: String, isExpanded: Binding<Bool>, @ViewBuilder selection: () -> Selection, @ViewBuilder content: () -> Content) {
        self.title = title; self.summary = summary; self._isExpanded = isExpanded
        self.selection = selection(); self.content = content()
    }
    private var detail: String {
        summary.hasPrefix(title + "：") ? String(summary.dropFirst(title.count + 1)) : summary
    }
    var body: some View {
        VStack(alignment: .leading, spacing: isExpanded ? 10 : 0) {
            HStack(spacing: SettingsColumns.spacing) {
                HStack(spacing: 6) {
                    Spacer(minLength: 0)
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { isExpanded.toggle() }
                    } label: {
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .rotationEffect(.degrees(isExpanded ? 90 : 0))
                            .frame(width: 24, height: 26).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                        .accessibilityLabel(title)
                        .accessibilityValue(isExpanded ? "展开" : "收起")
                    Text(title).accessibilityHidden(true)
                }.frame(width: SettingsColumns.labelWidth)
                ZStack(alignment: .leading) {
                    selection.labelsHidden().opacity(isExpanded ? 1 : 0)
                        .allowsHitTesting(isExpanded).accessibilityHidden(!isExpanded)
                    Text("：" + detail).lineLimit(1).truncationMode(.tail)
                        .opacity(isExpanded ? 0 : 1).accessibilityHidden(isExpanded)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(height: 26)
            if isExpanded {
                VStack(alignment: .leading, spacing: 10) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .clipped()
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: isExpanded)
        .transaction { if reduceMotion { $0.disablesAnimations = true } }
    }
}

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var reviewExpanded = true
    @State private var implementationExpanded = true
    @State private var confirmReal = false
    @State private var confirmRestore = false
    @State private var confirmRefresh = false
    private var isSolo: Bool { model.draft.mode == "solo" }
    private var versionLabel: String {
        guard let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
              !version.isEmpty else { return "当前版本：开发版" }
        return "当前版本：v\(version)"
    }
    private var reviewModelID: String {
        model.draft.channel == "qoder" ? "kmodel_latest" : model.draft.channel == "codex" ? (model.draft.reviewerModel.isEmpty ? "CLI 默认" : model.draft.reviewerModel) : model.draft.qwenModel
    }
    private func field<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: SettingsColumns.spacing) {
            Text(title).frame(width: SettingsColumns.labelWidth, alignment: .trailing).accessibilityHidden(true)
            content().labelsHidden().frame(maxWidth: .infinity, alignment: .leading)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func note(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary)
            .padding(.leading, SettingsColumns.labelWidth + SettingsColumns.spacing)
    }
    private func modelPicker(_ title: String, selection: Binding<String>, options: [ModelOption]) -> some View {
        field(title) {
            Picker(title, selection: selection) {
                ForEach(options) { option in Text(option.label).tag(option.id) }
            }.pickerStyle(.menu)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("配置与检测").font(.title2.bold())
            Text("默认配置用于后续启动或调用；当前会话不会被切换。项目、命令行或会话设置可能覆盖。主模型选项决定编辑哪组默认配置，不切换当前会话。").font(.callout).foregroundStyle(.secondary)
            GeometryReader { geometry in
              ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 10) {
                        field("主模型") {
                            Picker("主模型", selection: Binding(get: { model.draft.host }, set: { model.switchHost($0) })) {
                                Text("Claude Code").tag("claude"); Text("Codex").tag("codex")
                            }
                        }
                        modelPicker("主模型 ID", selection: $model.draft.primaryModel, options: model.primaryOptions)
                        field("协作模式") {
                            Picker("协作模式", selection: $model.draft.mode) {
                                ForEach(model.modes, id: \.0) { item in Text(item.0).tag(item.0) }
                            }
                        }
                        note("solo 同时停用交叉评审和独立实施。用户当次明确指令 > 项目 .claude/relay-mode.local > 全局默认 > rules。Codex 模式仅对已加载 codex-relay 的工作流生效。")
                        note("空主模型表示保留已有默认值；一次保存包含 Claude 与 Codex 两组已编辑字段。")
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    ServiceSettingsGroup("评审服务", summary: SettingsSummary.review(model.draft), isExpanded: $reviewExpanded) {
                        Picker("评审服务", selection: Binding(get: { model.reviewFamily }, set: { model.switchReviewFamily($0) })) {
                            if model.draft.host == "claude" { Text("Codex").tag("codex") }
                            Text("千问 Token Plan").tag("token_plan")
                            if model.draft.host == "codex" { Text("Qoder").tag("qoder") }
                            Text("自定义 API").tag("custom_api")
                            if model.draft.channel == "qoder" && model.draft.host == "claude" { Text("已有 Qoder 配置（Claude 主模型暂不支持）").tag("qoder") }
                            if model.draft.channel == "codex" && model.draft.host == "codex" { Text("已有 Codex 配置（此主模型暂不支持）").tag("codex") }
                            if !["codex", "qwen", "qoder"].contains(model.draft.channel) { Text("已有配置：\(model.draft.channel)").tag(model.draft.channel) }
                        }.disabled(isSolo)
                    } content: {
                        if model.draft.channel == "qwen" && model.draft.activeAPI == "custom_api" {
                            field("评审模型 ID") {
                                TextField("评审模型 ID", text: $model.draft.qwenModel).disabled(isSolo)
                            }
                        } else {
                            modelPicker("评审模型", selection: model.draft.channel == "qoder" ? .constant("kmodel_latest") : model.draft.channel == "codex" ? $model.draft.reviewerModel : $model.draft.qwenModel, options: model.reviewerOptions).disabled(isSolo || model.draft.channel == "qoder")
                        }
                        if model.draft.channel == "qwen" {
                            field("评审服务地址") {
                                TextField("评审服务地址", text: $model.draft.activeBaseURL).disabled(isSolo)
                            }
                            field("评审服务API密钥") {
                                SecureField("评审服务API密钥", text: $model.draft.apiKeyInput).disabled(isSolo)
                            }
                            note("Claude 与 Codex 共享当前 API 服务、模型及凭证；两个协议地址独立保存。切换服务保留各自草稿，未保存不写入磁盘。")
                        } else {
                            note(model.draft.channel == "qoder" ? "使用现有 Qoder CLI 登录；登录状态未检测。当前固定 Kimi-K3.1-Pro；仅支持 Codex 主模型。" : "使用现有 Codex CLI 登录；登录状态未检测。")
                        }
                        if model.draft.host == "claude" { note("Qoder 暂不支持 Claude 主模型。") }
                        field("评审执行器") {
                            Picker("评审执行器", selection: .constant(model.draft.channel)) {
                                Text(model.draft.channel == "qoder" ? "Qoder CLI" : model.draft.channel == "codex" ? "Codex CLI" : model.draft.effectiveExecutor == "codex" ? "Codex CLI · Responses" : "Claude Code CLI · Anthropic").tag(model.draft.channel)
                            }.disabled(true)
                        }
                        field("评审模型ID") {
                            Picker("评审模型ID", selection: .constant(reviewModelID)) {
                                Text(reviewModelID).tag(reviewModelID)
                            }.disabled(true)
                        }
                        if model.executorMigrationPending { note("保存后 Codex 主模型的 API 评审改用 Responses 地址，请确认服务支持。") }
                        if model.draft.host == "claude" {
                            note("已配置回退列表：" + (model.claudeFallbacks.isEmpty ? "无" : model.claudeFallbacks.joined(separator: " → ")))
                        } else {
                            note("Codex 评审失败即停止，不自动回退。")
                        }
                        if model.draft.channel == "qwen" {
                            note(model.selectedCredentialMessage)
                            if model.relativeCredentials && model.draft.activeAPI == model.saved.activeAPI { note("项目相对凭证未检查；业务项目可能优先使用。桌面仅检查 ~/ 或绝对路径凭证。") }
                            note("密钥按服务独立存入 ~/.config/ai-anti/secrets.json 的受限明文文件（0600）。")
                        }
                    }
                    ServiceSettingsGroup("实施服务", summary: SettingsSummary.implementation(model.draft), isExpanded: $implementationExpanded) {
                        Picker("实施服务", selection: model.draft.host == "claude" ? .constant("claude-agent") : $model.draft.implementationProvider) {
                            if model.draft.host == "claude" { Text("Claude Code 实施代理").tag("claude-agent") }
                            Text("Codex 原生代理").tag("codex")
                            Text("千问 Token Plan").tag("token_plan")
                            Text("Qoder 订阅").tag("qoder")
                        }.disabled(!model.draft.implementationEnabled)
                    } content: {
                        modelPicker("实施模型 ID",
                                    selection: model.draft.host == "claude" ? .constant("claude-agent") : $model.draft.implementationModel,
                                    options: model.draft.host == "claude" ? [ModelOption(id: "claude-agent", label: "由 Claude Code 实施代理配置")] : model.secondaryOptions)
                            .disabled(isSolo || model.draft.host == "claude")
                        if model.draft.host == "codex" && model.draft.implementationProvider == "token_plan" {
                            field("实施服务地址") {
                                TextField("实施服务地址", text: $model.draft.implementationURL).disabled(isSolo)
                            }
                            field("实施服务API密钥") {
                                SecureField("实施服务API密钥", text: $model.draft.implementationKeyInput).disabled(isSolo)
                            }
                            note("固定 glm-5.3；与 Token Plan 评审共享地址及凭证草稿，独立于当前评审服务和评审模型。评审测试不证明实施可用；业务材料外发仍需当次授权。仅确认请求模型，服务端实际模型未知。")
                        }
                        if model.draft.host == "codex" && model.draft.implementationProvider == "qoder" {
                            note("固定 GLM-5.3；使用已有 Qoder 订阅登录，要求 qodercli 1.1.77。检查配置仅检查版本，不验证登录或实施；评审检测仍走所选评审服务。CLI 报告模型，后端路由未知。工具限制不提供文件或网络硬隔离。")
                        }
                        note("Codex 保留已有实施模型 ID；目录选项不代表每个模型均通过实施验证。Claude Code 沿用自己的实施代理。")
                    }
                }.modifier(FullWidthFormControls())
                    .frame(width: geometry.size.width - 8, alignment: .leading).padding(4)
              }
            }
            Divider()
            CheckStatusText(status: model.dirty ? "未检测（设置草稿尚未保存）" : model.status).font(.callout).textSelection(.enabled)
            if model.checking { HStack { ProgressView().controlSize(.small); Text("已用时 \(model.elapsed) 秒"); Button("取消检测", action: model.cancel) } }
            Text(model.message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            HStack(alignment: .center, spacing: 8) {
                Button("检查配置") { model.check() }.disabled(model.busy || model.dirty || !model.loaded)
                Button("测试评审模型…") { confirmReal = true }.disabled(isSolo || model.busy || model.dirty || !model.loaded)
                Button("重新读取配置") { if model.dirty { confirmRefresh = true } else { model.refresh() } }.disabled(model.busy)
                    .help("重新从磁盘读取 Claude、Codex 和机制默认配置，不调用模型")
                Button("恢复上次保存") { confirmRestore = true }.disabled(model.busy || !model.canRestore || model.dirty)
                Spacer(minLength: 8)
                Button("取消", action: model.discard).disabled(model.busy || !model.dirty)
                Button("保存", action: model.save).buttonStyle(.borderedProminent).tint(.blue)
                    .disabled(model.busy || !model.loaded || !model.dirty)
            }.controlSize(.regular).fixedSize(horizontal: false, vertical: true)
            Text(versionLabel).font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }.padding(20).frame(width: 740, height: 760)
        .disabled(model.busy && !model.checking)
        .alert("测试会消耗模型额度", isPresented: $confirmReal) {
            Button("开始合成检测") { model.check(real: true) }; Button("取消", role: .cancel) {}
        } message: { Text("仅使用临时合成仓库；不读取业务材料。统一使用评审预算配置（默认总时长 30 分钟、空闲 15 分钟），无自动重试或降级。") }
        .alert("恢复上次保存？", isPresented: $confirmRestore) {
            Button("恢复") { model.restore() }; Button("取消", role: .cancel) {}
        } message: { Text("只在保存后没有外部修改时恢复，备份可能包含敏感配置。") }
        .alert("重新读取配置会放弃未保存草稿", isPresented: $confirmRefresh) {
            Button("放弃并重新读取配置") { model.refresh() }; Button("取消", role: .cancel) {}
        }
    }
}
