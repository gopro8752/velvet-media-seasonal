import Foundation

/// Pipe boundary only: real TmdbClient calls SeasonalCatalog request/decode/rowItems.
@main struct SeasonalContractCli {
    @MainActor static func main() async throws {
        let settings = SettingsStore()
        let client = TmdbClient(settings: settings)
        let encoder = JSONEncoder()
        while let line = readLine() {
            let args = line.components(separatedBy: "\t")
            settings.backendUrl = args[1]
            settings.syncUrl = args[1]
            settings.syncToken = args[2]
            let data: Data
            if args[0] == "clock" {
                let format = DateFormatter()
                format.locale = Locale(identifier: "en_US_POSIX")
                format.dateFormat = "yyyy-MM-dd"
                data = try JSONSerialization.data(withJSONObject: ["deviceDate": format.string(from: Date())])
            } else if args[0] == "discover" {
                let rows = await client.seasonalRows()
                data = try JSONSerialization.data(withJSONObject: rows.map { row in
                    ["id": row.id, "title": row.title, "style": row.style.rawValue,
                     "artworkUrl": row.artworkUrl?.absoluteString as Any? ?? NSNull()] as [String: Any]
                })
            } else {
                let items = await client.rowItems(rowId: args[3], page: Int(args[4])!)
                data = try encoder.encode(items)
            }
            print(String(decoding: data, as: UTF8.self))
            fflush(stdout)
        }
    }
}
