import Foundation
import Combine

@MainActor final class FakeClient: AppRequesting {
    var requests: [[String: Any]] = []
    var completions: [(Result<[String: Any], Error>) -> Void] = []
    var cancellations = 0
    func start(_ payload: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        requests.append(payload); completions.append(completion)
    }
    func cancel() { cancellations += 1 }
    func complete(_ result: Result<[String: Any], Error>) { completions.removeFirst()(result) }
}

@main struct AppModelChecks {
    @MainActor static func main() {
        let front = FakeClient(), back = FakeClient()
        let model = AppModel(foreground: front, background: back, automatic: false)
        let snapshot: [String: Any] = ["signature": "a", "fingerprints": ["fixture": "a"], "can_restore": true,
            "values": ["host": "codex", "channel": "qwen", "mode": "required", "primary_model": "fixture", "credential_present": true]]
        let checked: [String: Any] = ["signature": "a", "message": "本地检查通过", "checked_at": "fixture", "actual_model": "fake"]
        model.refresh(); assert(model.busy)
        front.complete(.success(snapshot)); assert(model.loaded && !model.busy)
        model.check(); assert(model.checking && front.requests.last?["action"] as? String == "local")
        front.complete(.success(checked))
        var publications = 0
        let subscription = model.objectWillChange.sink { publications += 1 }
        for _ in 0..<3 {
            model.inspect(); model.inspect()
            assert(!model.busy && !model.checking && back.completions.count == 1)
            back.complete(.success(["signature": "a"]))
        }
        assert(publications == 0, "Unchanged inspections must publish no UI updates")
        for stale in [Result<[String: Any], Error>.success(["signature": "stale"]), .failure(BridgeFailure(message: "stale error"))] {
            model.inspect(); model.check()
            assert(model.busy && model.checking && back.cancellations > 0)
            let status = model.status, message = model.message
            back.complete(stale)
            assert(model.busy && model.checking && model.status == status && model.message == message && model.credentialPresent)
            front.complete(.success(checked))
        }
        // A late response after the new check completes is equally stale.
        model.inspect(); model.check(); front.complete(.success(checked))
        let status = model.status
        model.inspect(); assert(back.completions.count == 1, "Cancellation must retain single flight")
        back.complete(.failure(BridgeFailure(message: "late"))); assert(model.status == status)
        model.draft.primaryModel = "unsaved"
        let saved = model.saved, draft = model.draft
        model.inspect(); back.complete(.success(["signature": "b"]))
        assert(model.status.contains("已变化") && model.saved == saved && model.draft == draft)
        let requestCount = front.requests.count
        model.check(); model.switchHost("claude"); model.menuMode("solo")
        assert(front.requests.count == requestCount && model.draft == draft)
        model.discard(); assert(!model.dirty)
        model.menuMode("force"); assert(model.saved.mode == "required")
        front.complete(.failure(BridgeFailure(message: "fixture conflict")))
        assert(!model.dirty && model.saved.mode == "required")
        model.menuMode("force")
        var savedSnapshot = snapshot
        var values = snapshot["values"] as! [String: Any]; values["mode"] = "force"; savedSnapshot["values"] = values
        front.complete(.success(savedSnapshot)); assert(model.saved.mode == "force" && !model.dirty)
        model.refresh(); assert(front.requests.last?["action"] as? String == "load")
        assert(front.requests.last?["secret"] == nil); front.complete(.success(snapshot))
        model.restore(); assert(front.requests.last?["fingerprints"] != nil); front.complete(.success(snapshot))
        model.check(); model.cancel(); assert(front.cancellations == 1)
        front.complete(.failure(BridgeFailure(message: "检测已取消")))
        assert(!model.busy && !model.checking && model.status.contains("检测已取消"))
        model.inspect(); model.stopBackground(); back.complete(.failure(BridgeFailure(message: "shutdown")))
        assert(model.status.contains("检测已取消"))
        withExtendedLifetime(subscription) {}
        print("PASS: AppModel quiet inspect, single flight, foreground priority, stale callbacks, draft/save/refresh/restore/cancel")
    }
}
