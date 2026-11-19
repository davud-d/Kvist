import Foundation
import Security
import os
import KvistCore

/// The trusted computer's token and this device's own identity, in the Keychain: the token drives an agent that
/// runs commands, so it is not left in a plain preferences file. "This device only" keeps it out of backups
/// and out of a restore onto another device, as the Android app does with its manifest.
final class KeychainStore: SavedStore {
    private let service = "site.kvist.ios"
    private let log = Logger(subsystem: "site.kvist.ios", category: "keychain")

    func deviceId() -> String {
        if let data = read("device"), let id = String(data: data, encoding: .utf8) { return id }
        let fresh = UUID().uuidString
        write("device", Data(fresh.utf8))
        return fresh
    }

    func load() -> Saved? {
        guard let data = read("computer") else { return nil }
        do {
            return try JSONDecoder().decode(Saved.self, from: data)
        } catch {
            log.error("the saved computer is unreadable: \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }

    func save(_ saved: Saved) {
        do {
            write("computer", try JSONEncoder().encode(saved))
        } catch {
            log.error("could not encode the computer: \(error.localizedDescription, privacy: .public)")
        }
    }

    func forget() {
        let status = SecItemDelete(query("computer") as CFDictionary)
        if status != errSecSuccess && status != errSecItemNotFound { log.error("delete failed: \(status)") }
    }

    private func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    private func read(_ account: String) -> Data? {
        var request = query(account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &found)
        if status != errSecSuccess && status != errSecItemNotFound { log.error("read failed: \(status)") }
        return status == errSecSuccess ? found as? Data : nil
    }

    private func write(_ account: String, _ data: Data) {
        let update = SecItemUpdate(query(account) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { return log.error("update failed: \(update)") }
        var item = query(account)
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        if status != errSecSuccess { log.error("add failed: \(status)") }
    }
}
