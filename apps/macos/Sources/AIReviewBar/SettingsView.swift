import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var confirmReal = false
    @State private var confirmRestore = false
    @State private var confirmRefresh = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("配置与检测").font(.title2.bold())
            Text("默认配置用于后续启动或调用；当前会话不会被切换。项目、命令行或会话设置可能覆盖。主模型选项决定编辑哪组默认配置，不切换当前会话。").font(.callout).foregroundStyle(.secondary)
            ScrollView {
                Form {
                    Picker("主模型", selection: Binding(get: { model.draft.host }, set: { model.switchHost($0) })) {
                        Text("Claude Code").tag("claude"); Text("Codex").tag("codex")
                    }.disabled(model.dirty)
                    TextField("主模型 ID", text: $model.draft.primaryModel)
                    Text("空主模型表示保留已有默认值；保存后显示实际回读值。").font(.caption).foregroundStyle(.secondary)
                    Picker("评审模型", selection: $model.draft.channel) {
                        if model.draft.host == "claude" { Text("Codex").tag("codex") }
                        Text("Qwen").tag("qwen")
                        if !["codex", "qwen"].contains(model.draft.channel) { Text("已有通道：\(model.draft.channel)（首期需改选）").tag(model.draft.channel) }
                    }
                    TextField("Codex 评审模型（空为 CLI 默认）", text: $model.draft.reviewerModel)
                    TextField("实施模型", text: $model.draft.secondaryModel)
                    Picker("协作模式", selection: $model.draft.mode) {
                        ForEach(model.modes, id: \.0) { item in Text(item.0).tag(item.0) }
                    }
                    Text("solo 同时停用交叉评审和独立实施。用户当次明确指令 > 项目 .claude/relay-mode.local > 全局默认 > required。Codex 模式仅对已加载 codex-relay 的工作流生效。").font(.caption).foregroundStyle(.secondary)
                    if model.draft.host == "claude" { Text("已配置回退列表：" + (model.fallbacks.isEmpty ? "无" : model.fallbacks.joined(separator: " → "))).font(.caption) }
                    Divider()
                    TextField("Qwen 模型 ID", text: $model.draft.qwenModel)
                    Text("qwen3.8-max / gpt-6-astra 有历史流程实测；其他 ID 未验证。历史证据不代表当前可调用。").font(.caption).foregroundStyle(.secondary)
                    TextField("Qwen 服务地址", text: $model.draft.baseURL)
                    Text(model.credentialPresent ? "发现凭证文件；是否可解析需手动检查" : "未发现凭证文件").font(.caption)
                    if model.relativeCredentials { Text("项目相对凭证未检查；业务项目可能优先使用。桌面仅检查 ~/ 或绝对路径凭证。").font(.caption).foregroundStyle(.secondary) }
                    SecureField("新密钥（留空保留现状）", text: $model.draft.secret)
                    Text("新密钥存入 ~/.config/ai-anti/secrets.json 的受限明文文件（0600），不是钥匙串；保留已有凭证路径。").font(.caption).foregroundStyle(.secondary)
                    if let path = model.sources[model.draft.host] { Text("默认值来源：\(path)").font(.caption).textSelection(.enabled) }
                    if let path = model.sources["ai"] { Text("机制配置：\(path)").font(.caption).textSelection(.enabled) }
                }.padding(4)
            }
            Divider()
            Text(model.dirty ? "未检测（设置草稿尚未保存）" : model.status).font(.callout).textSelection(.enabled)
            if model.checking { HStack { ProgressView().controlSize(.small); Text("已用时 \(model.elapsed) 秒"); Button("取消", action: model.cancel) } }
            Text(model.message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            HStack {
                Button("保存", action: model.save).disabled(model.busy || !model.loaded || !model.dirty)
                Button("放弃草稿", action: model.discard).disabled(model.busy || !model.dirty)
                Button("重新读取配置") { if model.dirty { confirmRefresh = true } else { model.refresh() } }.disabled(model.busy)
                    .help("重新从磁盘读取 Claude、Codex 和机制默认配置，不调用模型")
                Button("恢复上次保存") { confirmRestore = true }.disabled(model.busy || !model.canRestore || model.dirty)
            }
            HStack {
                Spacer()
                Button("检查配置") { model.check() }.disabled(model.busy || model.dirty || !model.loaded)
                Button("测试评审模型…") { confirmReal = true }.disabled(model.busy || model.dirty || !model.loaded)
            }
        }.padding(20).frame(width: 740, height: 760)
        .disabled(model.busy && !model.checking)
        .alert("测试会消耗模型额度", isPresented: $confirmReal) {
            Button("开始合成检测") { model.check(real: true) }; Button("取消", role: .cancel) {}
        } message: { Text("仅使用临时合成仓库；不读取业务材料。Qwen 可能需要 10–20 分钟，无自动重试或降级。") }
        .alert("恢复上次保存？", isPresented: $confirmRestore) {
            Button("恢复") { model.restore() }; Button("取消", role: .cancel) {}
        } message: { Text("只在保存后没有外部修改时恢复，备份可能包含敏感配置。") }
        .alert("重新读取配置会放弃未保存草稿", isPresented: $confirmRefresh) {
            Button("放弃并重新读取配置") { model.refresh() }; Button("取消", role: .cancel) {}
        }
    }
}
