import Foundation

@main
struct LibraryTests {
    @MainActor static func main() async {
        let model = ExportModel()
        model.start()
        precondition(model.books.count == 300)
        for category in LibraryFilter.allCases {
            let result = LibraryQuery.books(model.books, filter: category, search: "", sort: .pendingFirst, failed: model.failedIDs)
            precondition(result.count == (category == .all ? 300 : 75), "Category counts incorrect")
        }
        let all = LibraryQuery.books(model.books, filter: .all, search: "", sort: .pendingFirst, failed: model.failedIDs)
        precondition(all.prefix(225).allSatisfy { !$0.exported })
        precondition(all.prefix(75).allSatisfy { $0.ready && model.failures[$0.id] == nil })
        precondition(all.suffix(75).allSatisfy { $0.exported })
        let match = LibraryQuery.books(model.books, filter: .all, search: "003 作者 3", sort: .title, failed: model.failedIDs)
        precondition(match.count == 1 && match[0].id == "fixture:3")
        precondition(LibraryQuery.books(model.books, filter: .exported, search: "003", sort: .title, failed: model.failedIDs).isEmpty)
        let candidates = model.books.filter { $0.ready && !$0.exported }
        var launched: [String] = []
        var maximumActive = 0
        model.fixtureResponse = { _, identity in
            let id = identity!
            launched.append(id)
            maximumActive = max(maximumActive, model.exportingBookIDs.count)
            return id == "fixture:3"
                ? WorkerResponse(ok: false, error: "测试：配图下载失败", kind: "network")
                : WorkerResponse(ok: true, result: ExportResult(title: id, epub: "/tmp/fixture.epub"))
        }
        model.exportBatch(candidates + [candidates[0]])
        precondition(launched.count == 3, "Batch must launch three exports before any completion")
        for _ in 0..<500 {
            if !model.batchRunning { break }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(!model.batchRunning && model.batchCompleted == 150)
        precondition(Set(launched).count == 150 && launched.count == 150)
        precondition(!model.busy && maximumActive == 3)
        precondition(model.books.filter(\.exported).count == 224)
        precondition(model.failures.count == 1 && model.failures["fixture:3"] != nil)
        precondition(model.error.contains("1 本导出失败"))
        let cancelled = ExportModel(); cancelled.start()
        cancelled.exportBatch(cancelled.books.filter { $0.ready && !$0.exported && cancelled.failures[$0.id] == nil })
        cancelled.stopBatch()
        precondition(cancelled.busy && cancelled.batchRunning)
        cancelled.exportBatch(cancelled.books)
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(!cancelled.batchRunning && cancelled.books.filter(\.exported).count == 78)
        let denied = ExportModel(); denied.start()
        denied.fixtureResponse = { _, identity in
            identity == "fixture:3"
                ? WorkerResponse(ok: false, error: "测试授权失效", kind: "permission")
                : WorkerResponse(ok: true, result: ExportResult(title: identity!, epub: "/tmp/fixture.epub"))
        }
        denied.exportBatch(denied.books.filter { $0.ready && !$0.exported })
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(!denied.batchRunning && denied.permissionNeeded && denied.batchCompleted == 3 && !denied.busy)
        let automatic = ExportModel(); automatic.start(); automatic.auto = true
        automatic.refresh()
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(automatic.batchCompleted == 0, "New cache must stabilize before auto export")
        automatic.refresh()
        for _ in 0..<500 {
            if !automatic.busy && !automatic.batchRunning { break }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(automatic.batchTotal == 150 && automatic.batchCompleted == 150,
                     "Auto export must drain all stable books in one queue")
        precondition(automatic.books.filter(\.exported).count == 224)
        automatic.refresh()
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(!automatic.batchRunning && automatic.batchCompleted == 150,
                     "Failed books must wait for retry cooldown")
        print("PASS: 300 books, disjoint categories, multi-term search, pending-first sort, 150-item batch with failure, cancellation and permission stop")
    }
}
