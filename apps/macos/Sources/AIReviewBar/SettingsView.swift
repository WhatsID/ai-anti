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

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var confirmReal = false
    @State private var confirmRestore = false
    @State private var confirmRefresh = false
    private var isSolo: Bool { model.draft.mode == "solo" }
    private var reviewModelID: String {
        model.draft.channel == "qoder" ? "kmodel_latest" : model.draft.channel == "codex" ? (model.draft.reviewerModel.isEmpty ? "CLI 默认" : model.draft.reviewerModel) : model.draft.qwenModel
    }
    private func modelPicker(_ title: String, selection: Binding<String>, options: [ModelOption]) -> some View {
        Picker(title, selection: selection) {
            ForEach(options) { option in Text(option.label).tag(option.id) }
        }.pickerStyle(.menu)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("配置与检测").font(.title2.bold())
            Text("默认配置用于后续启动或调用；当前会话不会被切换。项目、命令行或会话设置可能覆盖。主模型选项决定编辑哪组默认配置，不切换当前会话。").font(.callout).foregroundStyle(.secondary)
            GeometryReader { geometry in
              ScrollView {
                Form {
                    Picker("主模型", selection: Binding(get: { model.draft.host }, set: { model.switchHost($0) })) {
                        Text("Claude Code").tag("claude"); Text("Codex").tag("codex")
                    }
                    modelPicker("主模型 ID", selection: $model.draft.primaryModel, options: model.primaryOptions)
                    modelPicker("实施模型 ID",
                                selection: model.draft.host == "claude" ? .constant("claude-agent") : $model.draft.secondaryModel,
                                options: model.draft.host == "claude" ? [ModelOption(id: "claude-agent", label: "由 Claude Code 实施代理配置")] : model.secondaryOptions)
                        .disabled(isSolo || model.draft.host == "claude")
                    Picker("协作模式", selection: $model.draft.mode) {
                        ForEach(model.modes, id: \.0) { item in Text(item.0).tag(item.0) }
                    }
                    Text("solo 同时停用交叉评审和独立实施。用户当次明确指令 > 项目 .claude/relay-mode.local > 全局默认 > rules。Codex 模式仅对已加载 codex-relay 的工作流生效。").font(.caption).foregroundStyle(.secondary)
                    Picker("评审服务", selection: Binding(get: { model.reviewFamily }, set: { model.switchReviewFamily($0) })) {
                        if model.draft.host == "claude" { Text("Codex").tag("codex") }
                        Text("千问 Token Plan").tag("token_plan")
                        if model.draft.host == "codex" { Text("Qoder").tag("qoder") }
                        Text("自定义 API").tag("custom_api")
                        if model.draft.channel == "qoder" && model.draft.host == "claude" { Text("已有 Qoder 配置（Claude 主模型暂不支持）").tag("qoder") }
                        if model.draft.channel == "codex" && model.draft.host == "codex" { Text("已有 Codex 配置（此主模型暂不支持）").tag("codex") }
                        if !["codex", "qwen", "qoder"].contains(model.draft.channel) { Text("已有配置：\(model.draft.channel)").tag(model.draft.channel) }
                    }.disabled(isSolo)
                    if model.draft.channel == "qwen" && model.draft.activeAPI == "custom_api" {
                        TextField("评审模型 ID", text: $model.draft.qwenModel).disabled(isSolo)
                    } else {
                        modelPicker("评审模型", selection: model.draft.channel == "qoder" ? .constant("kmodel_latest") : model.draft.channel == "codex" ? $model.draft.reviewerModel : $model.draft.qwenModel, options: model.reviewerOptions).disabled(isSolo || model.draft.channel == "qoder")
                    }
                    if model.draft.channel == "qwen" {
                        TextField("服务地址", text: $model.draft.activeBaseURL).disabled(isSolo)
                        SecureField("API 密钥", text: $model.draft.apiKeyInput).disabled(isSolo)
                        Text("Claude 与 Codex 共享当前 API 服务、模型及凭证；两个协议地址独立保存。切换服务保留各自草稿，未保存不写入磁盘。").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text(model.draft.channel == "qoder" ? "使用现有 Qoder CLI 登录；登录状态未检测。当前固定 Kimi-K3；仅支持 Codex 主模型。" : "使用现有 Codex CLI 登录；登录状态未检测。").font(.caption).foregroundStyle(.secondary)
                    }
                    if model.draft.host == "claude" { Text("Qoder 暂不支持 Claude 主模型。").font(.caption).foregroundStyle(.secondary) }
                    Picker("评审执行器", selection: .constant(model.draft.channel)) {
                        Text(model.draft.channel == "qoder" ? "Qoder CLI" : model.draft.channel == "codex" ? "Codex CLI" : model.draft.effectiveExecutor == "codex" ? "Codex CLI · Responses" : "Claude Code CLI · Anthropic").tag(model.draft.channel)
                    }.disabled(true)
                    Picker("模型 ID", selection: .constant(reviewModelID)) {
                        Text(reviewModelID).tag(reviewModelID)
                    }.disabled(true)
                    if model.executorMigrationPending { Text("保存后 Codex 主模型的 API 评审改用 Responses 地址，请确认服务支持。").font(.caption) }
                    Text("空主模型表示保留已有默认值；一次保存包含 Claude 与 Codex 两组已编辑字段。").font(.caption).foregroundStyle(.secondary)
                    if model.draft.host == "claude" {
                        Text("已配置回退列表：" + (model.claudeFallbacks.isEmpty ? "无" : model.claudeFallbacks.joined(separator: " → "))).font(.caption)
                    } else {
                        Text("Codex 评审失败即停止，不自动回退。").font(.caption)
                    }
                    if model.draft.channel == "qwen" {
                        Text(model.selectedCredentialMessage).font(.caption)
                        if model.relativeCredentials && model.draft.activeAPI == model.saved.activeAPI { Text("项目相对凭证未检查；业务项目可能优先使用。桌面仅检查 ~/ 或绝对路径凭证。").font(.caption).foregroundStyle(.secondary) }
                        Text("密钥按服务独立存入 ~/.config/ai-anti/secrets.json 的受限明文文件（0600）。").font(.caption).foregroundStyle(.secondary)
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

        }.padding(20).frame(width: 740, height: 760)
        .disabled(model.busy && !model.checking)
        .alert("测试会消耗模型额度", isPresented: $confirmReal) {
            Button("开始合成检测") { model.check(real: true) }; Button("取消", role: .cancel) {}
        } message: { Text("仅使用临时合成仓库；不读取业务材料。API 最多 30 分钟，Qoder 最多 10 分钟，无自动重试或降级。") }
        .alert("恢复上次保存？", isPresented: $confirmRestore) {
            Button("恢复") { model.restore() }; Button("取消", role: .cancel) {}
        } message: { Text("只在保存后没有外部修改时恢复，备份可能包含敏感配置。") }
        .alert("重新读取配置会放弃未保存草稿", isPresented: $confirmRefresh) {
            Button("放弃并重新读取配置") { model.refresh() }; Button("取消", role: .cancel) {}
        }
    }
}
