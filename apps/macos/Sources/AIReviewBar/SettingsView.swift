import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var confirmReal = false
    @State private var confirmRestore = false
    @State private var confirmRefresh = false
    private var isSolo: Bool { model.draft.mode == "solo" }
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
                    Picker("评审模型", selection: Binding(get: { model.reviewFamily }, set: { model.switchReviewFamily($0) })) {
                        if model.draft.host == "claude" { Text("Codex").tag("codex") }
                        Text("Qwen").tag("qwen"); Text("DeepSeek").tag("deepseek")
                        if !["codex", "qwen"].contains(model.draft.channel) { Text("已有通道：\(model.draft.channel)（首期需改选）").tag(model.draft.channel) }
                    }.disabled(isSolo)
                    modelPicker("评审模型 ID", selection: model.draft.channel == "codex" ? $model.draft.reviewerModel : $model.draft.qwenModel, options: model.reviewerOptions)
                        .disabled(isSolo)
                    Picker("评审执行器", selection: .constant(model.draft.channel == "codex" ? "codex" : model.draft.effectiveExecutor)) {
                        Text("Claude Code CLI").tag("claude"); Text("Codex CLI").tag("codex")
                    }.disabled(true)
                    if model.executorMigrationPending {
                        Text("旧配置使用 Claude Code CLI；保存后，Codex 主模型的 API 评审将改用 Codex CLI。请确认 Responses 服务地址。").font(.caption).foregroundStyle(.secondary)
                    }
                    TextField("服务地址", text: model.draft.channel == "qwen" ? $model.draft.activeBaseURL : .constant("不适用（CLI 登录配置）"))
                        .disabled(isSolo || model.draft.channel != "qwen")
                    SecureField("评审模型秘钥", text: model.draft.channel == "qwen" ? $model.draft.secret : .constant(""), prompt: Text(model.draft.channel != "qwen" ? "不适用" : model.credentialPresent ? String(repeating: "•", count: 60) : "输入密钥"))
                        .disabled(isSolo || model.draft.channel != "qwen")
                    Text("空主模型表示保留已有默认值；一次保存包含 Claude 与 Codex 两组已编辑字段。").font(.caption).foregroundStyle(.secondary)
                    if model.draft.host == "claude" {
                        Text("已配置回退列表：" + (model.claudeFallbacks.isEmpty ? "无" : model.claudeFallbacks.joined(separator: " → "))).font(.caption)
                    } else {
                        Text("Codex 评审失败即停止，不自动回退。").font(.caption)
                    }
                    if model.draft.channel == "qwen" {
                        Text(model.draft.effectiveExecutor == "codex" ? "Codex CLI 使用 Responses 兼容地址；仅确认请求模型，服务端实际模型未知。" : "Claude Code CLI 使用 Anthropic 兼容地址；Claude 主模型固定使用此执行器。")
                            .font(.caption).foregroundStyle(.secondary)
                        Text("使用所选模型所属入口的地址和密钥；两个协议地址独立保存，不会自动更换。目录选项不代表已验证可调用，DeepSeek 尚未验证完整流程。").font(.caption).foregroundStyle(.secondary)
                        Text(model.credentialPresent ? "发现凭证文件；是否可解析需手动检查" : "未发现凭证文件").font(.caption)
                        if model.relativeCredentials { Text("项目相对凭证未检查；业务项目可能优先使用。桌面仅检查 ~/ 或绝对路径凭证。").font(.caption).foregroundStyle(.secondary) }
                        Text("密钥存入 ~/.config/ai-anti/secrets.json 的受限明文文件（0600），不是钥匙串；保留已有凭证路径。").font(.caption).foregroundStyle(.secondary)
                    }
                }.frame(width: geometry.size.width - 8, alignment: .leading).padding(4)
              }
            }
            Divider()
            Text(model.dirty ? "未检测（设置草稿尚未保存）" : model.status).font(.callout).textSelection(.enabled)
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
        } message: { Text("仅使用临时合成仓库；不读取业务材料。兼容 API 最多可能需要 30 分钟，无自动重试或降级。") }
        .alert("恢复上次保存？", isPresented: $confirmRestore) {
            Button("恢复") { model.restore() }; Button("取消", role: .cancel) {}
        } message: { Text("只在保存后没有外部修改时恢复，备份可能包含敏感配置。") }
        .alert("重新读取配置会放弃未保存草稿", isPresented: $confirmRefresh) {
            Button("放弃并重新读取配置") { model.refresh() }; Button("取消", role: .cancel) {}
        }
    }
}
