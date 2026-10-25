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
        model.exportBatch(candidates)
        for _ in 0..<500 {
            if !model.batchRunning { break }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(!model.batchRunning && model.batchCompleted == 150)
        precondition(model.books.filter(\.exported).count == 224)
        precondition(model.failures.count == 1 && model.failures["fixture:3"] != nil)
        precondition(model.error.contains("1 本导出失败"))
        let cancelled = ExportModel(); cancelled.start()
        cancelled.exportBatch(cancelled.books.filter { $0.ready && !$0.exported && cancelled.failures[$0.id] == nil })
        cancelled.stopBatch()
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(!cancelled.batchRunning && cancelled.books.filter(\.exported).count == 76)
        let denied = ExportModel(); denied.start()
        denied.fixtureResponse = { _, _ in WorkerResponse(ok: false, error: "测试授权失效", kind: "permission") }
        denied.exportBatch(denied.books.filter { $0.ready && !$0.exported })
        try? await Task.sleep(nanoseconds: 20_000_000)
        precondition(!denied.batchRunning && denied.permissionNeeded && denied.batchCompleted == 1)
        print("PASS: 300 books, disjoint categories, multi-term search, pending-first sort, 150-item batch with failure, cancellation and permission stop")
    }
}
