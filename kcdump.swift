// kcdump: list macOS keychain items, inspect ownership, and explore the security model.
//
// ============================================================================
// ARCHITECTURE & LESSONS LEARNED
// ============================================================================
//
// 1. World-Readable Metadata vs. Guarded Secrets:
//    - Metadata (service name, account name, creation/modification timestamps,
//      keychain source database, and Access Control Lists) is world-readable
//      by ANY process running in the user session without permission dialogs.
//    - Secret payload bytes are strictly guarded. Apple's Security framework
//      refuses bulk secret export outright (errSecParam / -50). Secrets can only
//      be fetched one item at a time, where each foreign item triggers an OS
//      system access dialog.
//
// 2. Search Lists and the "sudo" Context Difference:
//    - Normal execution (`./kcdump`): Queries the user's default keychain search
//      list, combining the user's login keychain (~/Library/Keychains/login.keychain-db,
//      often thousands of app/test items) and the System keychain.
//    - Root execution (`sudo ./kcdump`): Evaluated in root's security domain,
//      which only searches root and system databases (/Library/Keychains/System.keychain,
//      typically ~100 items). Item indices will differ because the list size differs.
//
// 3. Keychain Ownership & Access Control Lists (ACLs):
//    macOS items do not have a simple POSIX "owner" field. Instead, ownership is
//    represented by three layers:
//    a) Keychain file location:
//       - `login.keychain-db` (owned by $USER:staff, mode 0600)
//       - `System.keychain` (owned by root:wheel, mode 0644)
//    b) Trusted Applications in ACL:
//       Legacy and native app access lists storing application bundle paths
//       (e.g., `/Applications/ExampleApp.app`, `/System/Library/.../exampledaemon`).
//    c) Partition IDs (Apple Developer Team IDs):
//       Modern sandboxed apps and system services restrict access using
//       developer Team IDs stored in ACL descriptions (either raw or hex-encoded
//       XML plists), formatted like `teamid:ABC123XYZ0`.
//    d) Access Groups:
//       Entitlements for shared access within an app group or suite
//       (`kSecAttrAccessGroup`, e.g. `group.com.example.app`).
//
// 4. Deletion Permissions & The Exit Code 195 (-61) Quirk:
//    - Items residing in `System.keychain` require `sudo` to delete or modify.
//    - If a non-root process attempts to delete a System keychain item, the OS
//      rejects the operation with OSStatus -61 (`wrPermErr` / `errSecWrPerm`:
//      Write permissions error).
//    - When returned to a UNIX shell, status -61 wraps into an 8-bit unsigned exit code:
//      exit code 195 = (256 - 61).
//    - Confusingly, Apple's native `security delete-generic-password` CLI prints
//      "password has been deleted." *before* the file write fails, dumps the attributes,
//      and exits with code 195 without actually deleting the item.
//
// 5. C-String Null Terminator Quirk:
//    - `SecTrustedApplicationCopyData` returns CFData containing C strings that
//      include the trailing '\0' null terminator.
//    - If converted directly to Swift String without filtering '\0', print()
//      emits raw null bytes to stdout, causing POSIX tools like `grep` to report
//      "Binary file (standard input) matches". All strings must strip null bytes.
//
// ============================================================================
// BUILD & USAGE
// ============================================================================
//
// Build:
//   swiftc -O -o kcdump kcdump.swift -framework Security
//
// List all items with keychain and owner info:
//   ./kcdump | less
//
// Filter by service, account, or owner:
//   ./kcdump | grep <pattern>
//   ./kcdump | grep com.example
//
// Inspect secrets (triggers OS prompt per item):
//   ./kcdump --secrets --limit=3
//
// Wipe items by service prefix:
//   ./kcdump --delete-prefix=com.example.test
//
// Wipe system items (requires sudo for System.keychain):
//   sudo ./kcdump --delete-prefix=com.example.systemservice
//
import Foundation
import Security

/// Parses a hex-encoded ASCII string into raw Data.
func dataFromHex(_ hexString: String) -> Data? {
  guard hexString.count % 2 == 0 else { return nil }
  var data = Data()
  var temp = ""
  for char in hexString {
    temp.append(char)
    if temp.count == 2 {
      guard let byte = UInt8(temp, radix: 16) else { return nil }
      data.append(byte)
      temp = ""
    }
  }
  guard temp.isEmpty else { return nil }
  return data
}

/// Retrieves the filename of the keychain containing the item (e.g. login.keychain-db, System.keychain).
func keychainName(for itemRef: SecKeychainItem) -> String {
  var kcRef: SecKeychain?
  guard SecKeychainItemCopyKeychain(itemRef, &kcRef) == errSecSuccess, let kc = kcRef else {
    return "?"
  }
  var pathLen: UInt32 = 1024
  var path = [CChar](repeating: 0, count: 1024)
  guard SecKeychainGetPath(kc, &pathLen, &path) == errSecSuccess else {
    return "?"
  }
  return (String(cString: path) as NSString).lastPathComponent
}

/// Extracts owner information from ACLs (trusted apps, team ID partitions) and access groups.
func itemOwners(for itemRef: SecKeychainItem, attributes: [String: Any]) -> String {
  var owners: [String] = []

  if let ag = attributes[kSecAttrAccessGroup as String] as? String, !ag.isEmpty {
    owners.append("group:\(ag)")
  }

  var accessRef: SecAccess?
  if SecKeychainItemCopyAccess(itemRef, &accessRef) == errSecSuccess, let access = accessRef {
    var aclList: CFArray?
    if SecAccessCopyACLList(access, &aclList) == errSecSuccess, let acls = aclList as? [SecACL] {
      for acl in acls {
        var appList: CFArray?
        var desc: CFString?
        var promptSelector = SecKeychainPromptSelector()
        SecACLCopyContents(acl, &appList, &desc, &promptSelector)

        if let apps = appList as? [SecTrustedApplication] {
          for app in apps {
            var data: CFData?
            SecTrustedApplicationCopyData(app, &data)
            if let d = data as Data? {
              let cleanData = d.filter { $0 != 0 }
              if let s = String(data: cleanData, encoding: .utf8) {
                let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                  let name = (trimmed as NSString).lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
                  if name != "apple:" && name != "apple-tool:" {
                    owners.append(name)
                  }
                }
              }
            }
          }
        }

        if let d = desc as String?, !d.isEmpty {
          let cleanDesc = d.replacingOccurrences(of: "\0", with: "")
          var xmlData: Data?
          if cleanDesc.hasPrefix("3c3f786d6c") {
            xmlData = dataFromHex(cleanDesc)
          } else if cleanDesc.hasPrefix("<?xml") {
            xmlData = cleanDesc.data(using: .utf8)
          }
          if let data = xmlData,
             let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
             let parts = plist["Partitions"] as? [String] {
            owners.append(contentsOf: parts)
          } else if cleanDesc.contains("teamid:") {
            for part in cleanDesc.components(separatedBy: .whitespacesAndNewlines) where part.contains("teamid:") {
              owners.append(part.trimmingCharacters(in: .whitespacesAndNewlines))
            }
          }
        }
      }
    }
  }

  var unique: [String] = []
  for o in owners {
    let trimmed = o.trimmingCharacters(in: .whitespacesAndNewlines)
    if !trimmed.isEmpty && !unique.contains(trimmed) {
      unique.append(trimmed)
    }
  }
  return unique.isEmpty ? "-" : unique.joined(separator: ", ")
}

/// Lists keychain items. Without --secrets, only metadata (service/account),
/// which the OS hands over without prompts. With --secrets, each foreign
/// item pops a system access dialog -- that dialog IS the security model.
///
/// Bulk secret export is refused outright (errSecParam), so secrets are
/// fetched one item at a time, which is also where the per-item prompts
/// bite.
func secretByteCount(item: [String: Any], class cls: CFString) -> String {
  var query: [String: Any] = [
    kSecClass as String: cls,
    kSecMatchLimit as String: kSecMatchLimitOne,
    kSecReturnData as String: true,
  ]
  for key in [kSecAttrService, kSecAttrServer, kSecAttrAccount] {
    if let value = item[key as String] {
      query[key as String] = value
    }
  }
  var result: CFTypeRef?
  let status = SecItemCopyMatching(query as CFDictionary, &result)
  guard status == errSecSuccess, let data = result as? Data else {
    return "<denied or empty>"
  }
  return "\(data.count)B <redacted>"
}

func dump(class cls: CFString, fetchSecrets: Bool, limit: Int) {
  let query: [String: Any] = [
    kSecClass as String: cls,
    kSecMatchLimit as String: kSecMatchLimitAll,
    kSecReturnAttributes as String: true,
    kSecReturnRef as String: true,
  ]
  var result: CFTypeRef?
  let status = SecItemCopyMatching(query as CFDictionary, &result)
  guard status == errSecSuccess else {
    print("\(cls): query failed, OSStatus=\(status)")
    return
  }
  guard let items = result as? [[String: Any]] else {
    print("\(cls): unexpected result shape")
    return
  }
  print("\(cls): \(items.count) items (showing \(min(limit, items.count)))")
  for (index, item) in items.prefix(limit).enumerated() {
    let rawAccount = (item[kSecAttrAccount as String] as? String) ?? "?"
    let account = rawAccount.replacingOccurrences(of: "\0", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    let rawService =
      (item[kSecAttrService as String] as? String) ?? (item[kSecAttrServer as String] as? String) ?? "?"
    let service = rawService.replacingOccurrences(of: "\0", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    var kcName = "?"
    var owner = "-"
    if let rawRef = item["v_Ref"] ?? item[kSecValueRef as String],
       CFGetTypeID(rawRef as CFTypeRef) == SecKeychainItemGetTypeID() {
      let itemRef = rawRef as! SecKeychainItem
      kcName = keychainName(for: itemRef)
      owner = itemOwners(for: itemRef, attributes: item)
    }
    var line = "[\(index)] kc=\(kcName) owner=\(owner) service=\(service) account=\(account)"
    if fetchSecrets {
      line += " secret=" + secretByteCount(item: item, class: cls)
    }
    print(line)
  }
}

/// Deletes every item whose service starts with prefix, both classes.
/// Returns (deleted, failed). A foreign item answers errSecAuthFailed;
/// data-protection items are invisible to this tool entirely.
func wipe(prefix: String) -> (deleted: Int, failed: Int) {
  var deleted = 0
  var failed = 0
  for cls in [kSecClassGenericPassword, kSecClassInternetPassword] {
    let query: [String: Any] = [
      kSecClass as String: cls,
      kSecMatchLimit as String: kSecMatchLimitAll,
      kSecReturnAttributes as String: true,
    ]
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
      let items = result as? [[String: Any]]
    else { continue }
    for item in items {
      guard let service = item[kSecAttrService as String] as? String,
        service.hasPrefix(prefix),
        let account = item[kSecAttrAccount as String] as? String
      else { continue }
      var delete: [String: Any] = [
        kSecClass as String: cls,
        kSecAttrService as String: service,
        kSecAttrAccount as String: account,
      ]
      if let server = item[kSecAttrServer as String] {
        delete[kSecAttrServer as String] = server
      }
      let status = SecItemDelete(delete as CFDictionary)
      if status == errSecSuccess || status == errSecItemNotFound {
        deleted += 1
      } else {
        failed += 1
        var note = ""
        if status == -61 {
          note = " (write permission denied, needs sudo for System.keychain)"
        }
        print("keep \(service) account=\(account): OSStatus=\(status)\(note)")
      }
    }
  }
  return (deleted, failed)
}

let args = CommandLine.arguments
let secrets = args.contains("--secrets")
if let prefixFlag = args.first(where: { $0.hasPrefix("--delete-prefix=") }) {
  let prefix = String(prefixFlag.dropFirst("--delete-prefix=".count))
  let outcome = wipe(prefix: prefix)
  print("deleted=\(outcome.deleted) failed=\(outcome.failed)")
  exit(0)
}
let limitValue =
  args
  .first(where: { $0.hasPrefix("--limit=") })
  .flatMap { Int($0.dropFirst("--limit=".count)) } ?? Int.max
dump(class: kSecClassGenericPassword, fetchSecrets: secrets, limit: limitValue)
dump(class: kSecClassInternetPassword, fetchSecrets: secrets, limit: limitValue)
