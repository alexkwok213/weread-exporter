import SwiftUI
import AppKit

struct Book: Identifiable, Decodable {
    var id: String
    var title: String
    var author: String
    var ready: Bool
    var status: String
    var downloaded: Int
    var expected: Int
    var fingerprint: String
    var exported: Bool
    var epub: String
}
struct WorkerResponse: Decodable {
    var ok: Bool
    var error: String?
    var kind: String?
    var books: [Book]?
    var skipped: Bool?
    var result: ExportResult?
}
struct ExportResult: Decodable { var title: String; var epub: String }

enum LibraryFilter: String, CaseIterable, Identifiable {
    case all = "全部", pending = "待导出", waiting = "未就绪", exported = "已导出", failed = "导出失败"
    var id: String { rawValue }
    func contains(_ book: Book, failed: Set<String>) -> Bool {
        switch self {
        case .all: return true
        case .exported: return book.exported
        case .failed: return !book.exported && failed.contains(book.id)
        case .pending: return !book.exported && book.ready && !failed.contains(book.id)
        case .waiting: return !book.exported && !book.ready && !failed.contains(book.id)
        }
    }
}
enum LibrarySort: String, CaseIterable, Identifiable {
    case pendingFirst = "未导出优先", title = "书名排序"
    var id: String { rawValue }
}
struct LibraryQuery {
    static func books(_ books: [Book], filter: LibraryFilter, search: String,
                      sort: LibrarySort, failed: Set<String>) -> [Book] {
        let terms = search.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        return books.filter { book in
            filter.contains(book, failed: failed) && terms.allSatisfy {
                (book.title + " " + book.author).localizedStandardContains($0)
            }
        }.sorted { a, b in
            func priority(_ book: Book) -> Int {
                if book.exported { return 3 }
                if failed.contains(book.id) { return 1 }
                return book.ready ? 0 : 2
            }
            if sort == .pendingFirst, priority(a) != priority(b) { return priority(a) < priority(b) }
            let order = a.title.localizedStandardCompare(b.title)
            return order == .orderedSame ? a.id < b.id : order == .orderedAscending
        }
    }
}
struct BookFailure: Codable {
    var fingerprint: String
    var message: String
}

@MainActor
final class ExportModel: ObservableObject {
    @Published var books: [Book] = []
    @Published var busy = false
    @Published var message = "正在读取本机下载…"
    @Published var error = ""
    @Published var permissionNeeded = false
    @Published var auto: Bool { didSet { UserDefaults.standard.set(auto, forKey: "autoExport") } }
    @Published var output: String
    @Published var lastChecked = ""
    @Published var failures: [String: BookFailure] = [:]
    @Published var exportingBookIDs: Set<String> = []
    private var activeRequests = 0
    private let concurrencyPreferences: UserDefaults
    @Published var maxConcurrentExports: Int {
        didSet {
            let limit = min(8, max(1, maxConcurrentExports))
            if maxConcurrentExports != limit { maxConcurrentExports = limit }
            concurrencyPreferences.set(limit, forKey: "maxConcurrentExports")
            if batchRunning { advanceQueue() }
        }
    }
    private var batchStopped = false
    @Published var batchTotal = 0
    @Published var batchCompleted = 0
    @Published var batchRunning = false
    private var queue: [Book] = []
    private var batchFailed = 0
    private var failureKey: String { "bookFailures|" + output }
    var failedIDs: Set<String> { Set(failures.keys) }
    private var previous: [String: String] = [:]
    private var retryAfter: [String: Date] = [:]
    private var timer: Timer?
    private var started = false

    init(concurrencyPreferences: UserDefaults = .standard) {
        self.concurrencyPreferences = concurrencyPreferences
        let savedLimit = concurrencyPreferences.object(forKey: "maxConcurrentExports") as? Int ?? 3
        maxConcurrentExports = min(8, max(1, savedLimit))
        auto = UserDefaults.standard.object(forKey: "autoExport") as? Bool ?? true
        output = UserDefaults.standard.string(forKey: "outputDirectory") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Downloads/微信读书导出").path
        loadFailures()
        #if DEBUG
        if fixtureMode { auto = false }
        #endif
    }
    func loadFailures() {
        failures = UserDefaults.standard.data(forKey: failureKey).flatMap {
            try? JSONDecoder().decode([String: BookFailure].self, from: $0)
        } ?? [:]
    }
    func saveFailures() {
        #if DEBUG
        if fixtureMode { return }
        #endif
        UserDefaults.standard.set(try? JSONEncoder().encode(failures), forKey: failureKey)
    }
    #if DEBUG
    var fixtureMode: Bool {
        ProcessInfo.processInfo.arguments.contains("--ui-fixture") || Bundle.main.bundleIdentifier == "io.github.alexkwok213.WeReadExporter.UITest"
    }
    var fixtureResponse: ((String, String?) -> WorkerResponse)?
    var fixtureBooks: [Book] {
        (1...300).map { n in
            Book(id: "fixture:\(n)", title: String(format: "测试书籍 %03d", n), author: "作者 \(n % 12)",
                 ready: n % 4 != 1, status: n % 4 == 2 ? "已导出" : (n % 4 == 1 ? "正文下载未完成" : "下载完成，可导出"),
                 downloaded: n % 4 == 1 ? 12 : 28, expected: 28, fingerprint: "fixture", exported: n % 4 == 2, epub: "")
        }
    }
    #endif
    func start() {
        guard !started else { return }; started = true
        #if DEBUG
        if fixtureMode {
            books = fixtureBooks
            failures = Dictionary(uniqueKeysWithValues: books.filter { Int($0.id.split(separator: ":").last!)! % 4 == 3 }.map {
                ($0.id, BookFailure(fingerprint: "fixture", message: "测试：配图下载失败，请重试"))
            })
            message = "界面测试 · 300 本模拟书籍"; return
        }
        #endif
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in if let s = self, s.auto { s.refresh() } }
        }
    }
    func perform(action: String, identity: String? = nil, completion: @escaping (WorkerResponse) -> Void) {
        activeRequests += 1; busy = true
        #if DEBUG
        if fixtureMode {
            let response = fixtureResponse?(action, identity) ?? (action == "scan"
                ? WorkerResponse(ok: true, books: books)
                : (identity == "fixture:3"
                    ? WorkerResponse(ok: false, error: "测试：配图下载失败", kind: "network")
                    : WorkerResponse(ok: true, result: ExportResult(title: books.first { $0.id == identity }?.title ?? "测试书籍", epub: "/tmp/fixture.epub"))))
            DispatchQueue.main.async { self.activeRequests -= 1; self.busy = self.activeRequests > 0; completion(response) }
            return
        }
        #endif
        let destination = output
        let resources = Bundle.main.resourceURL!
        DispatchQueue.global(qos: .utility).async {
            let response: WorkerResponse
            do {
                let process = Process()
                process.executableURL = resources.appendingPathComponent("Python/bin/python3")
                var args = ["-I", resources.appendingPathComponent("worker.py").path, action, "--output", destination]
                if let identity { args += ["--id", identity] }
                // -I intentionally omits the script directory; add the trusted bundled modules explicitly.
                args = ["-I", "-B", "-c", "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')", resources.path] + Array(args.dropFirst())
                process.arguments = args
                var environment = ProcessInfo.processInfo.environment
                environment["PYTHONHOME"] = nil; environment["PYTHONPATH"] = nil
                process.environment = environment
                let stdout = Pipe(); let stderr = Pipe()
                process.standardOutput = stdout; process.standardError = stderr
                try process.run()
                let data = stdout.fileHandleForReading.readDataToEndOfFile()
                let diagnostic = stderr.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if process.terminationStatus != 0 {
                    throw NSError(domain: "Worker", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: String(data: diagnostic, encoding: .utf8) ?? "导出程序未能启动"])
                }
                response = try JSONDecoder().decode(WorkerResponse.self, from: data)
            } catch {
                response = WorkerResponse(ok: false, error: error.localizedDescription, kind: "runtime")
            }
            DispatchQueue.main.async { self.activeRequests -= 1; self.busy = self.activeRequests > 0; completion(response) }
        }
    }
    func handleError(_ response: WorkerResponse) {
        if permissionNeeded && response.kind != "permission" { return }
        error = response.error ?? "操作失败，请稍后重试"
        permissionNeeded = response.kind == "permission"
        message = "需要处理后重试"
    }
    func refresh() {
        guard !busy && !batchRunning else { return }
        perform(action: "scan") { [weak self] response in
            guard let self else { return }
            guard response.ok else { self.handleError(response); return }
            self.books = response.books ?? []
            self.failures = self.failures.filter { id, failure in
                self.books.contains { $0.id == id && !$0.exported && $0.fingerprint == failure.fingerprint }
            }
            self.saveFailures()
            if self.permissionNeeded { self.error = "" }
            self.permissionNeeded = false
            self.lastChecked = Date().formatted(date: .omitted, time: .standard)
            self.message = self.books.isEmpty ? "尚未发现本机下载的电子书" : "已识别 \(self.books.count) 本本机缓存的书"
            let stable = self.books.filter { book in
                book.ready && !book.exported && self.previous[book.id] == book.fingerprint && (self.retryAfter[book.id] ?? .distantPast) <= Date()
            }
            self.previous = Dictionary(uniqueKeysWithValues: self.books.map { ($0.id, $0.fingerprint) })
            if self.auto { self.exportBatch(stable) }
        }
    }
    func export(_ book: Book) {
        guard book.ready && !book.exported && !exportingBookIDs.contains(book.id) else { return }
        guard batchRunning ? exportingBookIDs.count < maxConcurrentExports : !busy else { return }
        exportingBookIDs.insert(book.id)
        error = ""; message = "正在导出《\(book.title)》并整理配图…"
        perform(action: "export", identity: book.id) { [weak self] response in
            guard let self else { return }
            self.exportingBookIDs.remove(book.id)
            if response.ok, let result = response.result {
                self.message = "已导出《\(result.title)》"
                self.retryAfter[book.id] = nil
                self.failures[book.id] = nil
                if let index = self.books.firstIndex(where: { $0.id == book.id }) {
                    self.books[index].exported = true; self.books[index].epub = result.epub; self.books[index].status = "已导出"
                }
            } else {
                self.handleError(response)
                self.failures[book.id] = BookFailure(fingerprint: book.fingerprint, message: response.error ?? self.error)
                self.retryAfter[book.id] = Date().addingTimeInterval(120)
                if self.batchRunning { self.batchFailed += 1 }
            }
            self.saveFailures()
            if self.batchRunning {
                self.batchCompleted += 1
                if self.permissionNeeded { self.queue.removeAll() }
                self.advanceQueue()
            }
        }
    }
    func exportBatch(_ candidates: [Book]) {
        guard !busy && !batchRunning else { return }
        var seen: Set<String> = []
        queue = candidates.filter { $0.ready && !$0.exported && seen.insert($0.id).inserted }
        guard !queue.isEmpty else { return }
        batchTotal = queue.count; batchCompleted = 0; batchFailed = 0; batchStopped = false; batchRunning = true
        advanceQueue()
    }
    func advanceQueue() {
        guard batchRunning else { return }
        while !queue.isEmpty && !batchStopped && !permissionNeeded && exportingBookIDs.count < maxConcurrentExports {
            export(queue.removeFirst())
        }
        guard exportingBookIDs.isEmpty else { return }
        batchRunning = false
        if permissionNeeded { return }
        message = batchStopped
            ? "已停止排队，已完成 \(batchCompleted) 本"
            : "批量完成：成功 \(batchCompleted - batchFailed) 本，失败 \(batchFailed) 本"
        error = batchFailed == 0 ? "" : "\(batchFailed) 本导出失败，请到“导出失败”分类查看并重试。"
    }
    func stopBatch() {
        queue.removeAll(); batchStopped = true
        message = "已停止排队，正在导出的书籍会继续完成"
        advanceQueue()
    }
    func chooseOutput() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        panel.prompt = "选择导出位置"
        if panel.runModal() == .OK, let url = panel.url {
            output = url.path; UserDefaults.standard.set(output, forKey: "outputDirectory")
            loadFailures()
            previous.removeAll(); books = []; refresh()
        }
    }
    func showOutput() {
        let url = URL(fileURLWithPath: output)
        do { try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true); NSWorkspace.shared.open(url) }
        catch { self.error = error.localizedDescription }
    }
    func settings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
    }
    func showApp() { NSWorkspace.shared.activateFileViewerSelecting([Bundle.main.bundleURL]) }
}

struct ContentView: View {
    @StateObject private var model = ExportModel()
    @State private var filter: LibraryFilter = .all
    @State private var search = ""
    @State private var sort: LibrarySort = .pendingFirst
    @State private var selectedBook: Book.ID?
    var visibleBooks: [Book] {
        LibraryQuery.books(model.books, filter: filter, search: search, sort: sort, failed: model.failedIDs)
    }
    var candidates: [Book] { visibleBooks.filter { $0.ready && !$0.exported } }
    func count(_ category: LibraryFilter) -> Int {
        model.books.filter { category.contains($0, failed: model.failedIDs) }.count
    }
    func status(_ book: Book) -> String {
        if model.exportingBookIDs.contains(book.id) { return "正在导出…" }
        if book.exported { return "已导出" }
        if model.failures[book.id] != nil { return "导出失败" }
        return book.ready ? "待导出" : "未就绪"
    }
    func color(_ book: Book) -> Color {
        if book.exported { return .green }
        if model.failures[book.id] != nil { return .red }
        return book.ready ? .blue : .secondary
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: "books.vertical.fill").font(.system(size: 28)).foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 3) {
                    Text("微信读书导出助手").font(.title2.bold())
                    Text("共 \(model.books.count) 本 · 已导出 \(count(.exported)) 本").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Picker("同时导出", selection: $model.maxConcurrentExports) {
                    ForEach(1...8, id: \.self) { count in
                        Text("\(count) 本").tag(count)
                    }
                }.fixedSize()
                    .help("调整后立即用于后续任务，正在导出的书籍不会中断。")
                Toggle("自动导出", isOn: $model.auto).toggleStyle(.switch).fixedSize()
                    .help("应用打开时，每 15 秒检查新下载；内容齐全并稳定后自动导出。")
                Button("刷新书籍") { model.refresh() }.disabled(model.busy || model.batchRunning)
            }
            Divider()
            Picker("书籍分类", selection: $filter) {
                ForEach(LibraryFilter.allCases) { category in
                    Text("\(category.rawValue) \(count(category))").tag(category)
                }
            }.pickerStyle(.segmented).labelsHidden()
            HStack(spacing: 10) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索书名或作者", text: $search).textFieldStyle(.plain)
                        .accessibilityLabel("搜索书名或作者")
                    if !search.isEmpty {
                        Button { search = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.plain).foregroundStyle(.secondary).help("清除搜索")
                            .accessibilityLabel("清除搜索")
                    }
                }.padding(7).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 7))
                Picker("排序", selection: $sort) {
                    ForEach(LibrarySort.allCases) { Text($0.rawValue).tag($0) }
                }.labelsHidden().frame(width: 140)
                Button("导出当前列表（\(candidates.count)）") { model.exportBatch(candidates) }
                    .disabled(candidates.isEmpty || model.busy || model.batchRunning)
                    .help("最多同时导出 \(model.maxConcurrentExports) 本当前列表中已下载齐全、尚未导出的书籍。")
            }
            HStack {
                Text("显示 \(visibleBooks.count) / \(model.books.count) 本").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if filter == .waiting {
                    Text("等待下载齐全，或当前格式暂不支持").font(.caption).foregroundStyle(.secondary)
                }
                if model.busy { ProgressView().controlSize(.small) }
            }
            if model.books.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: model.permissionNeeded ? "lock.shield" : "arrow.down.book")
                        .font(.largeTitle).foregroundStyle(.secondary)
                    Text(model.permissionNeeded ? "暂时无法读取微信读书缓存" : "先在微信读书中下载电子书")
                    Text(model.permissionNeeded ? "请刷新此应用的系统授权，然后重新打开。" : "下载后点击刷新，或等待自动识别。")
                        .font(.callout).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if visibleBooks.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: search.isEmpty ? "checkmark.circle" : "magnifyingglass")
                        .font(.largeTitle).foregroundStyle(.secondary)
                    Text(search.isEmpty ? "此分类暂无书籍" : "没有匹配的书籍")
                    if !search.isEmpty { Button("清除搜索") { search = "" } }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Table(visibleBooks, selection: $selectedBook) {
                    TableColumn("书名") { book in
                        Text(book.title).lineLimit(1).help(book.title).fontWeight(.medium)
                    }.width(min: 190, ideal: 280)
                    TableColumn("作者") { book in
                        Text(book.author).lineLimit(1).foregroundStyle(.secondary).help(book.author)
                    }.width(min: 85, ideal: 130, max: 180)
                    TableColumn("状态") { book in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(status(book)).foregroundStyle(color(book))
                            Text("\(book.downloaded)/\(book.expected) 个正文文件").font(.caption2).foregroundStyle(.secondary)
                        }.padding(.vertical, 3)
                            .help(model.failures[book.id]?.message ?? book.status)
                    }.width(140)
                    TableColumn("操作") { book in
                        if book.exported {
                            Button("查看文件") {
                                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: book.epub)])
                            }.controlSize(.small)
                        } else {
                            Button(model.failures[book.id] == nil ? "导出" : "重试") { model.export(book) }
                                .controlSize(.small).disabled(model.busy || model.batchRunning || !book.ready)
                        }
                    }.width(80)
                }.tableStyle(.inset(alternatesRowBackgrounds: true))
            }
            Divider()
            if let id = selectedBook, let book = visibleBooks.first(where: { $0.id == id }) {
                Text("《\(book.title)》：" + (model.failures[id]?.message ?? book.status))
                    .font(.caption).foregroundStyle(model.failures[id] == nil ? Color.secondary : .red)
                    .lineLimit(2).textSelection(.enabled)
            }
            if model.batchRunning {
                HStack {
                    ProgressView(value: Double(model.batchCompleted), total: Double(model.batchTotal)).frame(width: 180)
                    Text("已完成 \(model.batchCompleted)/\(model.batchTotal) · 同时导出 \(model.exportingBookIDs.count) 本").font(.caption)
                    Spacer()
                    Button("停止排队") { model.stopBatch() }.controlSize(.small)
                }
            }
            Text(model.message).font(.callout).lineLimit(2).textSelection(.enabled)
            if !model.error.isEmpty {
                HStack(alignment: .top) {
                    Text(model.error).font(.caption).foregroundStyle(.red).lineLimit(3)
                        .textSelection(.enabled).help(model.error)
                    Spacer()
                    if model.permissionNeeded {
                        Button("打开权限设置") { model.settings() }
                        Button("显示此应用") { model.showApp() }
                    } else {
                        Button("查看失败") { filter = .failed; search = "" }.controlSize(.small)
                    }
                }
            }
            HStack {
                Text("导出位置：\(model.output)").font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle).help(model.output)
                Spacer()
                Button("更改") { model.chooseOutput() }.disabled(model.busy || model.batchRunning).controlSize(.small)
                Button("打开文件夹") { model.showOutput() }.controlSize(.small)
            }
            if !model.lastChecked.isEmpty {
                Text("上次检查：\(model.lastChecked)").font(.caption2).foregroundStyle(.secondary)
            }
        }.padding(20).frame(minWidth: 800, minHeight: 570).onAppear { model.start() }
    }
}

@main
struct WeReadExporterApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
            .windowStyle(.titleBar)
            .defaultSize(width: 960, height: 680)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
